import asyncio
import logging

from .config import Config
from .dockerctl import Docker
from .gateway import Gateway
from .manager import Manager

log = logging.getLogger("main")


async def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config.from_env()
    if not cfg.rcon_password:
        log.warning("RCON_PASSWORD is empty; whitelist changes from the panel/bot will fail.")
    manager = Manager(cfg, Docker(cfg.docker_socket))
    gateway = Gateway(cfg, manager)

    server = await asyncio.start_server(gateway.handle, "0.0.0.0", cfg.listen_port)
    log.info("Gateway listening on :%s", cfg.listen_port)
    background = [asyncio.create_task(manager.run())]

    if cfg.panel_password:
        from aiohttp import web
        from .web import build_app
        runner = web.AppRunner(build_app(cfg, manager))
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", cfg.panel_port).start()
        log.info("Web panel on :%s", cfg.panel_port)
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
