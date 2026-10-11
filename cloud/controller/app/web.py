"""Password-protected web panel + JSON API."""
import asyncio
import hmac
import secrets
import time
from pathlib import Path

from aiohttp import web

from .manager import NAME_RE

SESSION_TTL = 7 * 24 * 3600
MAX_FAILS, LOCKOUT_SECONDS = 5, 60


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
        return web.json_response({"changed": await manager.stop(), **manager.snapshot()})

    async def logs(request):
        try:
            text = await asyncio.to_thread(manager.docker.logs, cfg.mc_container, 200)
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

    app = web.Application(middlewares=[auth])
    app.add_routes([
        web.get("/", index), web.get("/api/public", public), web.post("/api/login", login),
        web.post("/api/logout", logout), web.get("/api/status", status), web.post("/api/start", start),
        web.post("/api/stop", stop), web.get("/api/logs", logs),
        web.get("/api/whitelist", whitelist_get), web.post("/api/whitelist", whitelist_post),
    ])
    return app
