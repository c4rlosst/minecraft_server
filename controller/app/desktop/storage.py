"""The rclone settings for the shared storage (Backblaze B2 or Cloudflare R2), and reading them back from a config."""
import configparser

R2_HOST = ".r2.cloudflarestorage.com"


def r2_endpoint(account_id: str) -> str:
    return f"https://{account_id.strip()}{R2_HOST}"


def rclone_section(name: str, storage: dict) -> str:
    """The [name] block of an rclone.conf for this storage (b2 or r2)."""
    if storage["type"] == "b2":
        return f"[{name}]\ntype = b2\naccount = {storage['account']}\nkey = {storage['key']}\nhard_delete = true\n"
    if storage["type"] == "r2":
        # no_check_bucket: a key limited to one bucket may read and write files but not create buckets, and rclone would
        # otherwise try to create the bucket before uploading.
        return (f"[{name}]\ntype = s3\nprovider = Cloudflare\naccess_key_id = {storage['access_key']}\n"
                f"secret_access_key = {storage['secret_key']}\nendpoint = {r2_endpoint(storage['account_id'])}\n"
                "region = auto\nno_check_bucket = true\n")
    raise ValueError(f"unknown storage type {storage['type']!r}")


def describe(conf_text: str, name: str) -> dict:
    """What an rclone.conf says about [name]: {'type': 'b2' | 'r2' | 'other' | '', 'account_id': ...}."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(conf_text)
        section = parser[name]
    except (configparser.Error, KeyError):
        return {"type": ""}
    kind = section.get("type", "")
    if kind == "b2":
        return {"type": "b2"}
    endpoint = section.get("endpoint", "")
    if kind == "s3" and endpoint.startswith("https://") and endpoint.endswith(R2_HOST):
        return {"type": "r2", "account_id": endpoint[len("https://"):-len(R2_HOST)]}
    return {"type": "other"}
