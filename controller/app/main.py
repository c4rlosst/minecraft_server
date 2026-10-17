import asyncio
import logging
import os
import sys

from . import identity
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
    handlers = [logging.StreamHandler()] if sys.stderr is not None else []   # pythonw.exe can have no console at all
    log_file = os.environ.get("LOG_FILE")
    if log_file:   # also keep a log on disk, useful when the controller runs hidden at startup
        from logging.handlers import RotatingFileHandler
        os.makedirs(os.path.dirname(log_file) or ".", exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=1_000_000, backupCount=3, encoding="utf-8"))
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", handlers=handlers)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)   # one line per panel request is just noise


class App:
    """What the shutdown code needs to know about."""
    cfg = runtime = shared = hosting = playit = None


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
    if cfg.shared_dir or cfg.shared_remote:
        from .hosting import Hosting
        from .shared import HostingError, build_world, resumed_own_lock
        if cfg.backend != "process":
            log.error("Shared hosting needs the Windows (process) setup.")
            return
        shared, where = build_world(cfg)
        app.shared = shared
        log.info("Taking the hosting lock in %s ...", where)
        try:
            previous = await asyncio.to_thread(shared.acquire, cfg.takeover, cfg.claim_wait)
        except HostingError as exc:
            log.error("%s", exc)
            print(f"\n  CAN'T HOST RIGHT NOW: {exc}\n")
            return
        hosting = app.hosting = Hosting(shared, manager)
        if runtime.state(cfg.mc_container) == "running":
            hosting.mark_started()      # a server left running from earlier: upload it when it stops
        elif resumed_own_lock(previous) and shared.can_keep_local_world():
            hosting.mark_started()      # we never uploaded the last session: this PC's world is the newest, keep it
            log.info("Resumed this PC's own lock; keeping the local world (it is newer than the shared copy).")
        elif previous.get("version", 0) > 0:
            if resumed_own_lock(previous):
                log.warning("The last download from the shared folder never finished, so this PC's world is incomplete. "
                            "Downloading it again.")
            if cfg.backup_dir and os.path.isdir(os.path.join(cfg.server_dir, "world")):
                from .backups import make_backup     # safety copy of this PC's world before it is replaced
                await asyncio.to_thread(make_backup, cfg.server_dir, cfg.backup_dir, cfg.backups_keep)
            log.info("Downloading the latest world from the shared folder...")
            await asyncio.to_thread(shared.pull)
        try:                                    # the server's name / description / picture: the newest one wins
            await asyncio.to_thread(identity.sync, shared, cfg.server_dir)
        except Exception as exc:                # never worth blocking the host over
            log.warning("Couldn't sync the server name and picture (%s); using this PC's.", exc)
        if cfg.playit_exe:                      # only the lock holder runs the tunnel (both PCs share one agent identity)
            from .playit import Playit, PlayitDaemon
            if cfg.playit_secret_path:          # the desktop app: our own agent process, no Windows service needed
                app.playit = PlayitDaemon(cfg.playit_exe, cfg.playit_secret_path,
                                          os.path.join(cfg.state_dir, "playit.log"))
            else:
                app.playit = Playit(cfg.playit_exe)
            log.info("playit.gg agent: %s", await asyncio.to_thread(app.playit.start))
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
            try:
                await hosting.push_after_stop()
            except HostingError as exc:
                # not fatal: the world is safe on this PC, and the next upload / handover tries again and shows this
                log.error("The world was NOT uploaded to the shared storage: %s", exc)
    manager.after_stop = after_stop

    if identity.apply(cfg):
        log.info("Server name: %s%s", cfg.server_name, f" - {cfg.server_description}" if cfg.server_description else "")

    def before_start():
        if hosting:
            hosting.mark_started()
        if cfg.voice_host and cfg.server_dir:
            from . import voice
            voice.apply(cfg.server_dir, cfg.voice_host)
        identity.apply_to_server(cfg)               # the server list text of the real server, at every wake
    manager.before_start = before_start

    quit_event = asyncio.Event()                # set by an automatic shutdown of the PC, or the desktop app's Quit

    # ---- front doors ---------------------------------------------------------------------------------
    gateway = Gateway(cfg, manager)
    server = await asyncio.start_server(gateway.handle, cfg.listen_host, cfg.listen_port)
    log.info("Gateway listening on %s:%s (backend: %s)", cfg.listen_host, cfg.listen_port, cfg.backend)

    if cfg.panel_password:
        from aiohttp import web
        from .web import build_app
        runner = web.AppRunner(build_app(cfg, manager, hosting, on_quit=quit_event.set))
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

    if cfg.auto_shutdown_minutes > 0:
        if sys.platform != "win32":
            log.warning("AUTO_SHUTDOWN_MINUTES is set, but automatic shutdown only works on Windows.")
        else:
            from .autoshutdown import AutoShutdown
            background.append(asyncio.create_task(AutoShutdown(cfg, manager, hosting, quit_event.set).run()))
            log.info("Auto shutdown is on: PC powers off after %s min with nobody online and %s min of no keyboard/mouse "
                     "(with a %ss warning).", cfg.auto_shutdown_minutes, cfg.auto_shutdown_user_idle_minutes,
                     cfg.auto_shutdown_warning_seconds)

    async with server:
        waiters = [asyncio.create_task(quit_event.wait())]
        if hosting:
            waiters.append(asyncio.create_task(hosting.exit_event.wait()))   # a handover ends the controller
        await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)


def cleanup(app: App):
    """Ctrl+C / normal exit: when hosting, stop the server, upload the world and give the lock back."""
    shared, cfg, runtime = app.shared, app.cfg, app.runtime
    if app.playit:                               # stop the tunnel first: the next host's agent must be the only one
        try:
            app.playit.stop()
        except Exception:
            log.exception("could not stop playit")
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
