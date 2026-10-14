import asyncio
import logging
import os

from .config import Config
from .gateway import Gateway
from .manager import Manager

log = logging.getLogger("main")


def make_runtime(cfg):
    if cfg.backend == "process":
        from .procctl import ProcessServer
        return ProcessServer(cfg)
    from .dockerctl import Docker
    return Docker(cfg.docker_socket)


def setup_logging():
    handlers = [logging.StreamHandler()]
    log_file = os.environ.get("LOG_FILE")
    if log_file:   # also keep a log on disk, useful when the controller runs hidden at startup
        from logging.handlers import RotatingFileHandler
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)


async def main():
    setup_logging()
    cfg = Config.from_env()
    if not cfg.rcon_password:
        log.warning("RCON_PASSWORD is empty; whitelist changes and graceful stops will fail.")
    manager = Manager(cfg, make_runtime(cfg))

    if cfg.backend == "process" and cfg.backup_dir:
        from .backups import make_backup

        async def backup_after_stop():
            await asyncio.to_thread(make_backup, cfg.server_dir, cfg.backup_dir, cfg.backups_keep)
        manager.after_stop = backup_after_stop

    gateway = Gateway(cfg, manager)
    server = await asyncio.start_server(gateway.handle, cfg.listen_host, cfg.listen_port)
    log.info("Gateway listening on %s:%s (backend: %s)", cfg.listen_host, cfg.listen_port, cfg.backend)
    background = [asyncio.create_task(manager.run())]

    if cfg.panel_password:
        from aiohttp import web
        from .web import build_app
        runner = web.AppRunner(build_app(cfg, manager))
        await runner.setup()
        await web.TCPSite(runner, cfg.panel_host, cfg.panel_port).start()
        log.info("Web panel on %s:%s", cfg.panel_host, cfg.panel_port)
    else:
        log.warning("PANEL_PASSWORD not set: web panel disabled.")

    if cfg.discord_token:
        from .bot import run_bot
        background.append(asyncio.create_task(run_bot(cfg, manager)))
    else:
        log.info("DISCORD_TOKEN not set: Discord bot disabled.")

    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(main())
