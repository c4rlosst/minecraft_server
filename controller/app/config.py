import os
from dataclasses import dataclass


def _int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    return int(value) if value else default


@dataclass
class Config:
    backend: str = "docker"            # "docker" (cloud/) or "process" (Windows, runs Java directly)
    mc_container: str = "smp-mc"
    mc_host: str = "mc"
    mc_port: int = 25565
    rcon_port: int = 25575
    rcon_password: str = ""
    listen_host: str = "0.0.0.0"
    listen_port: int = 25565
    idle_minutes: int = 10
    startup_timeout: int = 900
    stop_grace: int = 120
    poll_seconds: int = 5
    server_name: str = "Our SMP"
    public_address: str = ""
    ops: str = ""
    account_mode: str = "online"       # "offline": controller writes whitelist/ops by offline UUID (see offline.py)
    panel_password: str = ""
    panel_host: str = "0.0.0.0"
    panel_port: int = 8080
    trust_proxy: bool = False
    whitelist_path: str = "/data/whitelist.json"
    state_dir: str = "/state"
    docker_socket: str = "/var/run/docker.sock"
    # process backend
    server_dir: str = ""
    java_path: str = "java"
    mc_version: str = ""
    mc_memory: str = "6G"
    backup_dir: str = ""
    backups_keep: int = 14
    discord_token: str = ""
    discord_guild_id: int = 0
    discord_role_id: int = 0
    discord_channel_id: int = 0
    discord_blocked_ids: str = ""      # comma separated Discord user IDs who may not use the commands
    # shared hosting (two or more people take turns hosting through a shared folder, e.g. Google Drive)
    shared_dir: str = ""
    host_name: str = ""
    takeover: bool = False             # take over a lock left by a crashed host (loses its un-uploaded progress)
    claim_wait: int = 20               # seconds to wait after claiming the lock, to catch a simultaneous claim
    voice_host: str = ""               # "auto" (use this PC's public IP), "off", "host:port", or "" (don't touch)
    # switch the PC off when nobody is on the server and you're away (0 = off). See autoshutdown.py
    auto_shutdown_minutes: int = 0             # server asleep (nobody online) for this long
    auto_shutdown_user_idle_minutes: int = 10  # ...and no keyboard/mouse for this long
    auto_shutdown_warning_seconds: int = 60    # on-screen warning with a Cancel button
    auto_shutdown_drive_wait: int = 120        # shared hosting: seconds Windows waits so Drive can finish syncing
    auto_shutdown_force: bool = False          # True closes programs without asking (may lose unsaved work)

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ.get
        return cls(
            backend=env("BACKEND", "docker"),
            mc_container=env("MC_CONTAINER", "smp-mc"),
            mc_host=env("MC_HOST", "mc"),
            mc_port=_int("MC_PORT", 25565),
            rcon_port=_int("RCON_PORT", 25575),
            rcon_password=env("RCON_PASSWORD", ""),
            listen_host=env("LISTEN_HOST", "0.0.0.0"),
            listen_port=_int("LISTEN_PORT", 25565),
            idle_minutes=_int("IDLE_MINUTES", 10),
            startup_timeout=_int("STARTUP_TIMEOUT", 900),
            stop_grace=_int("STOP_GRACE", 120),
            poll_seconds=_int("POLL_SECONDS", 5),
            server_name=env("SERVER_NAME", "Our SMP"),
            public_address=env("PUBLIC_ADDRESS", ""),
            ops=env("OPS", ""),
            account_mode=env("ACCOUNT_MODE", "online").strip().lower(),
            panel_password=env("PANEL_PASSWORD", ""),
            panel_host=env("PANEL_HOST", "0.0.0.0"),
            panel_port=_int("PANEL_PORT", 8080),
            trust_proxy=env("TRUST_PROXY", "") == "1",
            whitelist_path=env("WHITELIST_PATH", "/data/whitelist.json"),
            state_dir=env("STATE_DIR", "/state"),
            docker_socket=env("DOCKER_SOCKET", "/var/run/docker.sock"),
            server_dir=env("SERVER_DIR", ""),
            java_path=env("JAVA_PATH", "") or "java",
            mc_version=env("MC_VERSION", "").strip(),
            mc_memory=env("MC_MEMORY", "6G"),
            backup_dir=env("BACKUP_DIR", ""),
            backups_keep=_int("BACKUPS_KEEP", 14),
            discord_token=env("DISCORD_TOKEN", ""),
            discord_guild_id=_int("DISCORD_GUILD_ID", 0),
            discord_role_id=_int("DISCORD_ROLE_ID", 0),
            discord_channel_id=_int("DISCORD_CHANNEL_ID", 0),
            discord_blocked_ids=env("DISCORD_BLOCKED_IDS", ""),
            shared_dir=env("SHARED_DIR", "").strip(),
            host_name=env("HOST_NAME", "").strip(),
            takeover=env("TAKEOVER", "") == "1",
            claim_wait=_int("CLAIM_WAIT", 20),
            voice_host=env("VOICE_HOST", "").strip(),
            auto_shutdown_minutes=_int("AUTO_SHUTDOWN_MINUTES", 0),
            auto_shutdown_user_idle_minutes=_int("AUTO_SHUTDOWN_USER_IDLE_MINUTES", 10),
            auto_shutdown_warning_seconds=_int("AUTO_SHUTDOWN_WARNING_SECONDS", 60),
            auto_shutdown_drive_wait=_int("AUTO_SHUTDOWN_DRIVE_WAIT", 120),
            auto_shutdown_force=env("AUTO_SHUTDOWN_FORCE", "") == "1",
        )
