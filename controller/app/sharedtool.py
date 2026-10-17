"""Look at, or seed, the shared world by hand (run through shared-world.ps1):

  status   who is hosting, which upload is stored, and whether what is stored matches the record
  upload   put THIS PC's world into the shared storage (e.g. the very first time). Close the controller first."""
import sys
import time

from .config import Config
from .shared import STALE_SECONDS, HostingError, build_world, manifest

CONTROLLER_ALIVE_SECONDS = 120


def _ago(seconds: float) -> str:
    seconds = int(max(seconds, 0))
    if seconds < 90:
        return f"{seconds} s ago"
    if seconds < 5400:
        return f"{seconds // 60} min ago"
    return f"{seconds // 3600} h ago"


def _stored_manifest(world):
    if hasattr(world, "_remote_manifest"):
        return world._remote_manifest()
    return manifest(world.data_dir)


def describe(world, now=None) -> str:
    now = now or time.time()
    state = world.read_state()
    if not state:
        return "Nothing is stored there yet: a brand new shared world."
    lines = []
    if state.get("status") == "hosting":
        age = now - float(state.get("lastSeen", 0))
        alive = "alive" if age < STALE_SECONDS else "NOT responding (crashed or closed?)"
        lines.append(f"Hosting:   {state.get('host')} ({alive}, last heartbeat {_ago(age)})")
    else:
        lines.append(f"Hosting:   nobody (last host: {state.get('lastHost') or 'unknown'})")
    if state.get("pushing"):
        lines.append(f"WARNING:   {state.get('pushingBy') or 'someone'}'s last upload never finished")
    lines.append(f"Upload:    version {state.get('version', 0)}"
                 + (f", {_ago(now - float(state['lastPush']))}" if state.get("lastPush") else ""))
    count, nbytes = _stored_manifest(world)
    match = (count, nbytes) == (state.get("count"), state.get("bytes"))
    lines.append(f"Stored:    {count} files, {nbytes / 1048576:.1f} MB"
                 + ("  (matches the record)" if match or not state.get("version")
                    else f"  (DIFFERENT from the record: {state.get('count')} files, "
                         f"{(state.get('bytes') or 0) / 1048576:.1f} MB)"))
    return "\n".join(lines)


def upload(world, claim_wait: float) -> str:
    state = world.read_state() or {}
    if state.get("status") == "hosting" and time.time() - float(state.get("lastSeen", 0)) < CONTROLLER_ALIVE_SECONDS:
        raise HostingError(f"{state.get('host')} looks like it is hosting right now (a controller is running). Close "
                           "that controller first, so two things never write to the shared world at once.")
    world.acquire(False, claim_wait)
    try:
        count, nbytes = world.push()
    finally:
        world.release()
    return f"Uploaded: {count} files, {nbytes / 1048576:.1f} MB. Nobody is hosting now."


def main(argv=None, cfg=None) -> int:
    argv = sys.argv if argv is None else argv
    action = argv[1] if len(argv) > 1 else "status"
    cfg = cfg or Config.from_env()
    world, where = build_world(cfg)
    if world is None:
        print("Shared hosting is off: set SHARED_REMOTE (or SHARED_DIR) in controller.env first.")
        return 2
    try:
        if action == "status":
            print(f"Shared world in {where}\n{describe(world)}")
        elif action == "upload":
            if cfg.server_dir and _server_running(cfg):
                raise HostingError("The Minecraft server is running on this PC. Close the controller first.")
            print(upload(world, cfg.claim_wait))
        else:
            print(f"Unknown action '{action}'. Use: status | upload")
            return 2
    except HostingError as exc:
        print(f"\n  {exc}\n")
        return 1
    return 0


def _server_running(cfg) -> bool:
    from .main import make_runtime
    return make_runtime(cfg).state(cfg.mc_container) == "running"


if __name__ == "__main__":
    sys.exit(main())
