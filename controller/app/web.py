"""Password-protected web panel + JSON API."""
import asyncio
import hmac
import logging
import os
import secrets
import tempfile
import time
from pathlib import Path

from aiohttp import web

from . import modpack
from .manager import NAME_RE, PlayersOnline

log = logging.getLogger("web")
SESSION_TTL = 7 * 24 * 3600
MAX_FAILS, LOCKOUT_SECONDS = 5, 60
MAX_UPLOAD = 512 * 1024 * 1024
NOT_AVAILABLE = "Mod uploads need the Windows (process) setup; with Docker, edit MODS in .env instead."
NOT_ASLEEP = "Stop the server first and wait until it says asleep, then change mods."


def build_app(cfg, manager) -> web.Application:
    sessions: dict[str, float] = {}
    fails: dict[str, tuple[int, float]] = {}
    page = Path(__file__).parent / "static" / "index.html"

    def client_ip(request) -> str:
        if cfg.trust_proxy:
            forwarded = request.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return request.remote or "?"

    @web.middleware
    async def auth(request, handler):
        if request.path.startswith("/api/") and request.path not in ("/api/login", "/api/public"):
            if sessions.get(request.cookies.get("sid", ""), 0) < time.time():
                return web.json_response({"error": "Not logged in"}, status=401)
        return await handler(request)

    async def index(request):
        return web.FileResponse(page)

    async def public(request):
        snap = manager.snapshot()
        return web.json_response({k: snap[k] for k in ("state", "players", "max_players", "server_name", "address")})

    async def login(request):
        ip = client_ip(request)
        count, until = fails.get(ip, (0, 0.0))
        if count >= MAX_FAILS and time.time() < until:
            return web.json_response({"error": "Too many attempts, wait a minute."}, status=429)
        body = await request.json()
        supplied = str(body.get("password", "")).encode()
        if not hmac.compare_digest(supplied, cfg.panel_password.encode()):
            fails[ip] = (count + 1, time.time() + LOCKOUT_SECONDS)
            return web.json_response({"error": "Wrong password"}, status=401)
        fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        sessions[token] = time.time() + SESSION_TTL
        response = web.json_response({"ok": True})
        secure = request.headers.get("X-Forwarded-Proto", request.scheme) == "https"
        response.set_cookie("sid", token, max_age=SESSION_TTL, httponly=True, samesite="Strict", secure=secure)
        return response

    async def logout(request):
        sessions.pop(request.cookies.get("sid", ""), None)
        response = web.json_response({"ok": True})
        response.del_cookie("sid")
        return response

    async def status(request):
        return web.json_response(manager.snapshot())

    async def start(request):
        return web.json_response({"changed": await manager.start(), **manager.snapshot()})

    async def stop(request):
        force = request.query.get("force") == "1"
        try:
            changed = await manager.stop(force=force)
        except PlayersOnline as exc:
            return web.json_response({"error": exc.message(), "can_force": True}, status=409)
        if changed:
            log.info("Server stop requested from the panel (force=%s)", force)
        return web.json_response({"changed": changed, **manager.snapshot()})

    async def logs(request):
        try:
            text = await asyncio.to_thread(manager.runtime.logs, cfg.mc_container, 200)
        except Exception as exc:
            return web.json_response({"error": str(exc)}, status=502)
        return web.json_response({"logs": text})

    async def whitelist_get(request):
        return web.json_response({"names": manager.whitelist_file_names(), "pending": manager.pending})

    async def whitelist_post(request):
        body = await request.json()
        name = str(body.get("name", "")).strip()
        if not NAME_RE.match(name):
            return web.json_response({"error": "Invalid Minecraft name"}, status=400)
        try:
            result = await manager.whitelist_change(str(body.get("op", "")), name)
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:
            return web.json_response({"error": f"RCON failed: {exc}"}, status=502)
        return web.json_response({"result": result})

    async def mods_list(request):
        if not cfg.server_dir:
            return web.json_response({"error": NOT_AVAILABLE}, status=501)
        mods = await asyncio.to_thread(modpack.list_mods, cfg.server_dir)
        return web.json_response({"mods": mods, "editable": manager.state == "asleep" and not manager.maintenance})

    async def mods_upload(request):
        if not cfg.server_dir:
            return web.json_response({"error": NOT_AVAILABLE}, status=501)
        if not request.content_type.startswith("multipart/"):
            return web.json_response({"error": "Send the files as a form upload."}, status=400)
        if not await manager.begin_maintenance():
            return web.json_response({"error": NOT_ASLEEP}, status=409)
        try:
            reader = await request.multipart()
            with tempfile.TemporaryDirectory() as tmp:
                saved, total = [], 0
                while (part := await reader.next()) is not None:
                    if part.name != "file" or not part.filename:
                        continue
                    path = os.path.join(tmp, f"{len(saved)}.bin")
                    with open(path, "wb") as out:
                        while chunk := await part.read_chunk(1 << 20):
                            total += len(chunk)
                            if total > MAX_UPLOAD:
                                return web.json_response({"error": "Upload too large (limit 512 MB)."}, status=413)
                            out.write(chunk)
                    saved.append((part.filename, path))
                if not saved:
                    return web.json_response({"error": "No file received."}, status=400)
                results = []
                for filename, path in saved:
                    try:
                        result = await asyncio.to_thread(modpack.install_any, path, filename, cfg.server_dir, cfg.mc_version)
                        results.append({"file": filename, "ok": True, **result})
                    except modpack.ModError as exc:
                        results.append({"file": filename, "ok": False, "error": str(exc)})
                    except Exception:
                        log.exception("mod install failed")
                        results.append({"file": filename, "ok": False, "error": "Unexpected error, see the controller window."})
            return web.json_response({"results": results})
        finally:
            manager.end_maintenance()

    async def mods_remove(request):
        if not cfg.server_dir:
            return web.json_response({"error": NOT_AVAILABLE}, status=501)
        if not await manager.begin_maintenance():
            return web.json_response({"error": NOT_ASLEEP}, status=409)
        try:
            name = await asyncio.to_thread(modpack.remove_mod, cfg.server_dir, request.match_info["name"])
        except modpack.ModError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        finally:
            manager.end_maintenance()
        return web.json_response({"removed": name})

    app = web.Application(middlewares=[auth], client_max_size=2 * 1024 * 1024)
    app.add_routes([
        web.get("/", index), web.get("/api/public", public), web.post("/api/login", login),
        web.post("/api/logout", logout), web.get("/api/status", status), web.post("/api/start", start),
        web.post("/api/stop", stop), web.get("/api/logs", logs),
        web.get("/api/whitelist", whitelist_get), web.post("/api/whitelist", whitelist_post),
        web.get("/api/mods", mods_list), web.post("/api/mods/upload", mods_upload),
        web.delete("/api/mods/{name}", mods_remove),
    ])
    return app
