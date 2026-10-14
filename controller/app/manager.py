"""Tracks whether the Minecraft container is asleep / starting / online / stopping, sleeps it when idle,
and applies queued whitelist changes once it is up."""
import asyncio
import json
import logging
import os
import re
import time

from . import mcproto, offline
from . import rcon as rcon_module

log = logging.getLogger("manager")
NAME_RE = re.compile(r"^[A-Za-z0-9_]{1,16}$")


class PlayersOnline(Exception):
    """A stop was requested while people are playing."""

    def __init__(self, players: int, names: list):
        super().__init__(f"{players} player(s) online")
        self.players, self.names = players, names

    def message(self) -> str:
        who = f" ({', '.join(self.names)})" if self.names else ""
        return f"{self.players} player(s) are playing right now{who}, so the server won't be stopped."


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
        self.maintenance = False   # True while mods are being changed: the server must not start meanwhile
        self._lock = asyncio.Lock()
        self._pending_path = os.path.join(cfg.state_dir, "pending_whitelist.json")
        self.pending = self._load_pending()
        # Offline accounts can't be checked against Mojang, so the whitelist is off in that mode: players are
        # protected by their EasyAuth passwords instead.
        self.whitelist_on = cfg.account_mode != "offline"

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
            if self.state != "asleep" or self.maintenance:
                return False
            log.info("Starting server")
            previous = self.state
            if not self.whitelist_on:
                self._write_offline_ops()
            await asyncio.to_thread(self.runtime.start, self.cfg.mc_container)
            self._reset()
            self.started_at = self.clock()
            self.state = "starting"
            self._notify(previous)
            return True

    async def begin_maintenance(self) -> bool:
        """Reserve the server for a mods change. Only possible while it's asleep; blocks it from starting."""
        async with self._lock:
            if self.state != "asleep" or self.maintenance:
                return False
            self.maintenance = True
            return True

    def end_maintenance(self):
        self.maintenance = False

    async def stop(self, force: bool = False) -> bool:
        """Stop the server on request. Refuses while players are on it unless force=True."""
        async with self._lock:
            if self.state not in ("online", "starting"):
                return False
            if self.players > 0 and not force:
                raise PlayersOnline(self.players, list(self.names))
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

    def _write_offline_ops(self):
        names = [n.strip() for n in self.cfg.ops.split(",") if NAME_RE.match(n.strip())]
        try:
            offline.ensure_ops(os.path.join(os.path.dirname(self.cfg.whitelist_path), "ops.json"), names)
        except OSError:
            log.exception("could not write ops.json")

    def is_allowed(self, name: str) -> bool:
        """May this player wake/join? Everyone, unless the whitelist is on."""
        return (not self.whitelist_on) or name.lower() in self.allowed_names()

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
        if not self.whitelist_on:
            raise ValueError("The whitelist is off in offline mode: players protect their name with /register and "
                             "/login. Use `.\\set-account-mode.ps1 -Online` to go back to a whitelist.")
        if op not in ("add", "remove") or not NAME_RE.match(name or ""):
            raise ValueError("Use add/remove with a valid Minecraft name (3-16 letters, digits, underscore).")
        if self.state == "online":
            reply = await self.rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, f"whitelist {op} {name}")
            if "does not exist" in reply.lower():
                raise ValueError(f"'{name}' isn't a real Minecraft account. Check the spelling (and that they own the game).")
            return "applied"
        other = "remove" if op == "add" else "add"
        self.pending[other] = [n for n in self.pending[other] if n.lower() != name.lower()]
        if name.lower() not in (n.lower() for n in self.pending[op]):
            self.pending[op].append(name)
        self._save_pending()
        return "queued"

    async def _apply_ops(self):
        if not self.whitelist_on:
            return   # offline mode: ops.json was written with offline UUIDs before the server started
        for name in (n.strip() for n in self.cfg.ops.split(",")):
            if NAME_RE.match(name):
                try:
                    await self.rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, f"op {name}")
                except Exception:
                    log.exception("could not op %s", name)

    async def _apply_pending(self):
        if not self.whitelist_on or not (self.pending["add"] or self.pending["remove"]):
            return
        for op in ("add", "remove"):
            for name in list(self.pending[op]):
                try:
                    reply = await self.rcon(self.cfg.mc_host, self.cfg.rcon_port, self.cfg.rcon_password, f"whitelist {op} {name}")
                    if "does not exist" in reply.lower():
                        log.warning("Whitelist %s '%s' dropped: not a real Minecraft account", op, name)
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
            "whitelist": self.whitelist_on,
            "maintenance": self.maintenance,
            "mods_enabled": bool(self.cfg.server_dir),   # only the Windows/process backend manages the mods folder
        }
