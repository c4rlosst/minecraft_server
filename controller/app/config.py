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
    mc_memory: str = "6G"
    backup_dir: str = ""
    backups_keep: int = 14
    discord_token: str = ""
    discord_guild_id: int = 0
    discord_role_id: int = 0
    discord_channel_id: int = 0

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
            panel_password=env("PANEL_PASSWORD", ""),
            panel_host=env("PANEL_HOST", "0.0.0.0"),
            panel_port=_int("PANEL_PORT", 8080),
            trust_proxy=env("TRUST_PROXY", "") == "1",
            whitelist_path=env("WHITELIST_PATH", "/data/whitelist.json"),
            state_dir=env("STATE_DIR", "/state"),
            docker_socket=env("DOCKER_SOCKET", "/var/run/docker.sock"),
            server_dir=env("SERVER_DIR", ""),
            java_path=env("JAVA_PATH", "") or "java",
            mc_memory=env("MC_MEMORY", "6G"),
            backup_dir=env("BACKUP_DIR", ""),
            backups_keep=_int("BACKUPS_KEEP", 14),
            discord_token=env("DISCORD_TOKEN", ""),
            discord_guild_id=_int("DISCORD_GUILD_ID", 0),
            discord_role_id=_int("DISCORD_ROLE_ID", 0),
            discord_channel_id=_int("DISCORD_CHANNEL_ID", 0),
        )
