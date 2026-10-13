"""Tracks whether the Minecraft container is asleep / starting / online / stopping, sleeps it when idle,
and applies queued whitelist changes once it is up."""
import asyncio
import json
import logging
import os
import re
import time

from . import mcproto
from . import rcon as rcon_module

log = logging.getLogger("manager")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")


class Manager:
    def __init__(self, cfg, runtime, ping=mcproto.ping_server, rcon=rcon_module.rcon, clock=time.monotonic):
        self.cfg, self.runtime, self.ping, self.rcon, self.clock = cfg, runtime, ping, rcon, clock
        self.after_stop = None   # optional async callable, runs once the server has fully stopped (e.g. backup)
        self.on_change = None    # optional async callable(old_state, new_state), e.g. Discord announcements
        self.state = "asleep"
        self.players = 0
        self.max_players = 0
        self.names: list[str] = []
        self.version = ""
        self.started_at = None
        self.online_at = None
        self.last_active = None
        self.last_error = None
        self._stopping = False
        self._stop_task = None
        self._lock = asyncio.Lock()
        self._pending_path = os.path.join(cfg.state_dir, "pending_whitelist.json")
        self.pending = self._load_pending()

    # ---- state machine -------------------------------------------------------------------------
    def _reset(self):
        self.players, self.max_players, self.names = 0, 0, []
        self.started_at = self.online_at = self.last_active = None
        self._stopping = False

    def _notify(self, previous: str):
        if self.on_change and self.state != previous:
            asyncio.create_task(self._safe_notify(previous, self.state))

    async def _safe_notify(self, old: str, new: str):
        try:
            await self.on_change(old, new)
        except Exception:
            log.exception("state change listener failed")

    async def refresh(self):
        async with self._lock:
            previous = self.state
            await self._refresh()
            self._notify(previous)

    async def _refresh(self):
        cfg, now = self.cfg, self.clock()
        status = await asyncio.to_thread(self.runtime.state, cfg.mc_container)
        if status == "missing":
            self.state, self.last_error = "error", f"Container '{cfg.mc_container}' not found"
            self._reset()
            return
        if status != "running":
            self.state = "asleep"
            self._reset()
            return
        self.last_error = None
        try:
            info = await self.ping(cfg.mc_host, cfg.mc_port, 3.0)
        except Exception:
            info = None
        if info is None:
            if self.started_at is None:
                self.started_at = now
            self.online_at = None
            self.state = "stopping" if self._stopping else "starting"
            if self.state == "starting" and now - self.started_at > cfg.startup_timeout:
                self.last_error = "Startup is taking unusually long; check the logs."
            return

        players = info.get("players") or {}
        self.players = players.get("online", 0)
        self.max_players = players.get("max", 0)
        self.names = [p.get("name", "?") for p in players.get("sample") or []]
        self.version = (info.get("version") or {}).get("name", "")
        if self.online_at is None:
            self.online_at, self.last_active = now, now
            await self._apply_ops()
            await self._apply_pending()
        self.state = "stopping" if self._stopping else "online"
        if self.players > 0:
            self.last_active = now
        elif not self._stopping and now - self.last_active >= cfg.idle_minutes * 60:
            log.info("No players for %s min, putting the server to sleep", cfg.idle_minutes)
            self._begin_stop()

    async def run(self):
        while True:
            try:
                await self.refresh()
            except Exception:
                log.exception("refresh failed")
            await asyncio.sleep(self.cfg.poll_seconds)

    # ---- actions -------------------------------------------------------------------------------
    async def start(self) -> bool:
        async with self._lock:
            if self.state != "asleep":
                return False
            log.info("Starting server")
            previous = self.state
            await asyncio.to_thread(self.runtime.start, self.cfg.mc_container)
            self._reset()
            self.started_at = self.clock()
            self.state = "starting"
            self._notify(previous)
            return True

    async def stop(self) -> bool:
        async with self._lock:
            if self.state not in ("online", "starting"):
                return False
            previous = self.state
            self._begin_stop()
            self._notify(previous)
            return True

    def _begin_stop(self):
        self._stopping = True
        self.state = "stopping"
        self._stop_task = asyncio.create_task(self._do_stop())

    async def _do_stop(self):
        stopped = False
        try:
            await asyncio.to_thread(self.runtime.stop, self.cfg.mc_container, self.cfg.stop_grace)
            stopped = True
        except Exception as exc:
            log.exception("stop failed")
            self.last_error = str(exc)
        if stopped and self.after_stop:
            try:
                await self.after_stop()
            except Exception:
                log.exception("after-stop task failed")
        self._stopping = False
        try:
            await self.refresh()
        except Exception:
            log.exception("refresh after stop failed")

    # ---- whitelist -----------------------------------------------------------------------------
    def _load_pending(self) -> dict:
        try:
            with open(self._pending_path) as f:
                data = json.load(f)
            return {"add": list(data.get("add", [])), "remove": list(data.get("remove", []))}
        except (OSError, ValueError):
            return {"add": [], "remove": []}

    def _save_pending(self):
        try:
            os.makedirs(self.cfg.state_dir, exist_ok=True)
            with open(self._pending_path, "w") as f:
                json.dump(self.pending, f)
        except OSError:
            log.exception("could not save pending whitelist")

    def whitelist_file_names(self) -> list[str]:
        try:
            with open(self.cfg.whitelist_path) as f:
                return sorted({e["name"] for e in json.load(f)}, key=str.lower)
        except (OSError, ValueError, KeyError, TypeError):
            return []

    def allowed_names(self) -> set[str]:
        names = {n.lower() for n in self.whitelist_file_names()}
        names |= {n.lower() for n in self.pending["add"]}
        names -= {n.lower() for n in self.pending["remove"]}
        return names

    async def whitelist_change(self, op: str, name: str) -> str:
        """Returns 'applied' (server online) or 'queued' (applied the next time it starts)."""
        if op not in ("add", "remove") or not NAME_RE.match(name or ""):
            raise ValueError("Use add/remove with a valid Minecraft name (3-16 letters, digits, underscore).")
        if self.state == "online":
            await self.rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, f"whitelist {op} {name}")
            return "applied"
        other = "remove" if op == "add" else "add"
        self.pending[other] = [n for n in self.pending[other] if n.lower() != name.lower()]
        if name.lower() not in (n.lower() for n in self.pending[op]):
            self.pending[op].append(name)
        self._save_pending()
        return "queued"

    async def _apply_ops(self):
        for name in (n.strip() for n in self.cfg.ops.split(",")):
            if NAME_RE.match(name):
                try:
                    await self.rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, f"op {name}")
                except Exception:
                    log.exception("could not op %s", name)

    async def _apply_pending(self):
        if not (self.pending["add"] or self.pending["remove"]):
            return
        for op in ("add", "remove"):
            for name in list(self.pending[op]):
                try:
                    await self.rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, f"whitelist {op} {name}")
                    self.pending[op].remove(name)
                except Exception:
                    log.exception("could not apply whitelist %s %s yet", op, name)
        self._save_pending()

    # ---- reporting -----------------------------------------------------------------------------
    def snapshot(self) -> dict:
        now = self.clock()
        idle_in = None
        if self.state == "online" and self.players == 0 and self.last_active is not None:
            idle_in = max(0, int(self.cfg.idle_minutes * 60 - (now - self.last_active)))
        return {
            "state": self.state,
            "players": self.players,
            "max_players": self.max_players,
            "names": self.names,
            "version": self.version,
            "sleeps_in": idle_in,
            "error": self.last_error,
            "server_name": self.cfg.server_name,
            "address": self.cfg.public_address,
        }
