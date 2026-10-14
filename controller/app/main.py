import asyncio
import logging
import os
import platform

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


class App:
    """What the shutdown code needs to know about."""
    cfg = runtime = shared = hosting = None


async def main(app: App):
    setup_logging()
    cfg = app.cfg = Config.from_env()
    if not cfg.rcon_password:
        log.warning("RCON_PASSWORD is empty; whitelist changes and graceful stops will fail.")
    runtime = app.runtime = make_runtime(cfg)
    manager = Manager(cfg, runtime)
    background = [asyncio.create_task(manager.run())]
    hosting = None

    # ---- shared hosting: take the lock and fetch the latest world before anything else -------------
    if cfg.shared_dir:
        from .hosting import Hosting
        from .shared import HostingError, SharedWorld
        if cfg.backend != "process":
            log.error("Shared hosting needs the Windows (process) setup.")
            return
        cfg.host_name = cfg.host_name or platform.node()
        shared = app.shared = SharedWorld(cfg.shared_dir, cfg.server_dir, cfg.host_name)
        log.info("Taking the hosting lock in %s ...", cfg.shared_dir)
        try:
            previous = await asyncio.to_thread(shared.acquire, cfg.takeover, cfg.claim_wait)
        except HostingError as exc:
            log.error("%s", exc)
            print(f"\n  CAN'T HOST RIGHT NOW: {exc}\n")
            return
        hosting = app.hosting = Hosting(shared, manager)
        if runtime.state(cfg.mc_container) == "running":
            hosting.mark_started()      # a server left running from earlier: upload it when it stops
        elif previous.get("version", 0) > 0:
            if cfg.backup_dir and os.path.isdir(os.path.join(cfg.server_dir, "world")):
                from .backups import make_backup     # safety copy of this PC's world before it is replaced
                await asyncio.to_thread(make_backup, cfg.server_dir, cfg.backup_dir, cfg.backups_keep)
            log.info("Downloading the latest world from the shared folder...")
            await asyncio.to_thread(shared.pull)
        manager.host_info = {"shared": True, "host": cfg.host_name}
        background.append(asyncio.create_task(hosting.heartbeat_loop()))
        log.info("%s is now hosting.", cfg.host_name)

    # ---- hooks around the Minecraft server ----------------------------------------------------------
    backup = None
    if cfg.backend == "process" and cfg.backup_dir:
        from .backups import make_backup

        async def backup():
            await asyncio.to_thread(make_backup, cfg.server_dir, cfg.backup_dir, cfg.backups_keep)

    async def after_stop():
        if backup:
            await backup()
        if hosting:
            await hosting.push_after_stop()
    manager.after_stop = after_stop

    if hosting or cfg.voice_host:
        from . import voice

        def before_start():
            if hosting:
                hosting.mark_started()
            if cfg.voice_host and cfg.server_dir:
                voice.apply(cfg.server_dir, cfg.voice_host)
        manager.before_start = before_start

    # ---- front doors ---------------------------------------------------------------------------------
    gateway = Gateway(cfg, manager)
    server = await asyncio.start_server(gateway.handle, cfg.listen_host, cfg.listen_port)
    log.info("Gateway listening on %s:%s (backend: %s)", cfg.listen_host, cfg.listen_port, cfg.backend)

    if cfg.panel_password:
        from aiohttp import web
        from .web import build_app
        runner = web.AppRunner(build_app(cfg, manager, hosting))
        await runner.setup()
        await web.TCPSite(runner, cfg.panel_host, cfg.panel_port).start()
        log.info("Web panel on %s:%s", cfg.panel_host, cfg.panel_port)
    else:
        log.warning("PANEL_PASSWORD not set: web panel disabled.")

    if cfg.discord_token:
        from .bot import run_bot
        background.append(asyncio.create_task(run_bot(cfg, manager, hosting)))
    else:
        log.info("DISCORD_TOKEN not set: Discord bot disabled.")

    async with server:
        if hosting:
            await hosting.exit_event.wait()     # a handover ends the controller
        else:
            await server.serve_forever()


def cleanup(app: App):
    """Ctrl+C / normal exit: when hosting, stop the server, upload the world and give the lock back."""
    shared, cfg, runtime = app.shared, app.cfg, app.runtime
    if not shared or not shared.token:
        return
    try:
        changed = bool(app.hosting and app.hosting.dirty)
        if runtime.state(cfg.mc_container) == "running":
            print("\nStopping the Minecraft server and saving the world...")
            runtime.stop(cfg.mc_container, cfg.stop_grace)
            changed = True
            if cfg.backup_dir:
                from .backups import make_backup
                make_backup(cfg.server_dir, cfg.backup_dir, cfg.backups_keep)
        if changed:
            print("Uploading the world to the shared folder (don't close this window)...")
            shared.push()
            print("Uploaded. Let Google Drive finish syncing before you turn off the PC.")
    except Exception:
        log.exception("Could not upload the world while shutting down. The lock is released anyway; "
                      "the next host will get the last successful upload.")
    finally:
        shared.release()


def run():
    app = App()
    try:
        asyncio.run(main(app))
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        cleanup(app)


if __name__ == "__main__":
    run()
