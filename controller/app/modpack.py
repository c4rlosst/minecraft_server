"""Install mods into the server folder from an upload: a single .jar, a .zip of jars, or a Modrinth .mrpack.

Everything here is blocking; callers run it in a thread. Replaced/removed jars are moved to <server>/mods-removed
so nothing is lost. Safety rules: only Fabric mods (fabric.mod.json), only whitelisted folders, no path tricks,
and .mrpack downloads come from known hosts and must match their published hash."""
import hashlib
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

log = logging.getLogger("modpack")

ALLOWED_DIRS = ("mods", "config", "defaultconfigs")
DOWNLOAD_HOSTS = {"cdn.modrinth.com", "github.com", "raw.githubusercontent.com", "gitlab.com"}
REDIRECT_HOSTS = DOWNLOAD_HOSTS | {"objects.githubusercontent.com", "release-assets.githubusercontent.com"}
MAX_FILE_BYTES = 256 * 1024 * 1024
JAR_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+\-() \[\]]*\.jar$")


class ModError(Exception):
    """A problem to show to the user (bad file, wrong loader, wrong Minecraft version...)."""


# ---- reading jars ------------------------------------------------------------------------------
def _fabric_info(zf: zipfile.ZipFile):
    try:
        data = json.loads(zf.read("fabric.mod.json").decode("utf-8-sig"))
    except (KeyError, ValueError):
        return None
    return {"id": data.get("id"), "name": data.get("name") or data.get("id"),
            "version": str(data.get("version", "")), "environment": data.get("environment", "*")}


def _wrong_loader(zf: zipfile.ZipFile):
    names = set(zf.namelist())
    if "META-INF/neoforge.mods.toml" in names:
        return "NeoForge"
    if "META-INF/mods.toml" in names:
        return "Forge"
    if "quilt.mod.json" in names:
        return "Quilt"
    return None


def jar_info(path: str):
    try:
        with zipfile.ZipFile(path) as zf:
            return _fabric_info(zf)
    except (zipfile.BadZipFile, OSError):
        return None


def list_mods(server_dir: str) -> list[dict]:
    mods_dir = os.path.join(server_dir, "mods")
    out = []
    for name in sorted(os.listdir(mods_dir), key=str.lower) if os.path.isdir(mods_dir) else []:
        if not name.lower().endswith(".jar"):
            continue
        info = jar_info(os.path.join(mods_dir, name)) or {}
        out.append({"file": name, "id": info.get("id"), "name": info.get("name") or name,
                    "version": info.get("version", ""), "environment": info.get("environment", "*"),
                    "size": os.path.getsize(os.path.join(mods_dir, name))})
    return out


# ---- file helpers ------------------------------------------------------------------------------
def _safe_jar_name(name: str) -> str:
    base = os.path.basename(name.replace("\\", "/"))
    if not JAR_NAME_RE.match(base):
        raise ModError(f"'{base}' isn't an acceptable file name (letters, numbers, . _ + - ( ) and a .jar ending).")
    return base


def _retire(server_dir: str, path: str) -> str:
    removed = os.path.join(server_dir, "mods-removed")
    os.makedirs(removed, exist_ok=True)
    target = os.path.join(removed, f"{os.path.basename(path)}.{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.move(path, target)
    return os.path.basename(path)


def _safe_rel(path: str) -> str:
    """Normalise a path from inside a pack and make sure it stays inside an allowed server folder."""
    p = path.replace("\\", "/")
    parts = [x for x in p.split("/") if x not in ("", ".")]
    if not parts or p.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", parts[0]):
        raise ModError(f"Unsafe path in the pack: {path}")
    if parts[0] not in ALLOWED_DIRS:
        raise ModError(f"'{path}' is outside the folders mods may use ({', '.join(ALLOWED_DIRS)}).")
    return "/".join(parts)


# ---- installing one jar ------------------------------------------------------------------------
def install_jar(src_path: str, filename: str, server_dir: str) -> dict:
    name = _safe_jar_name(filename)
    try:
        with zipfile.ZipFile(src_path) as zf:
            info = _fabric_info(zf)
            other = _wrong_loader(zf)
    except zipfile.BadZipFile:
        raise ModError(f"'{name}' isn't a valid .jar file.")
    if info is None:
        if other:
            raise ModError(f"'{name}' is a {other} mod, but this server runs Fabric.")
        raise ModError(f"'{name}' isn't a Fabric mod (no fabric.mod.json inside).")
    if info["environment"] == "client":
        return {"skipped": f"{name}: client-only mod, not needed on the server"}

    mods_dir = os.path.join(server_dir, "mods")
    os.makedirs(mods_dir, exist_ok=True)
    replaced = []
    for existing in os.listdir(mods_dir):          # same mod id = an update, two copies would crash Fabric
        existing_path = os.path.join(mods_dir, existing)
        if existing.lower().endswith(".jar") and existing != name and info["id"]:
            other_info = jar_info(existing_path)
            if other_info and other_info["id"] == info["id"]:
                replaced.append(_retire(server_dir, existing_path))
    dest = os.path.join(mods_dir, name)
    if os.path.exists(dest):
        replaced.append(_retire(server_dir, dest))
    shutil.copyfile(src_path, dest)
    return {"installed": f"{info['name']} {info['version']} ({name})", "replaced": replaced}


def _merge(total: dict, part: dict):
    if part.get("installed"):
        total["installed"].append(part["installed"])
    if part.get("skipped"):
        total["skipped"].append(part["skipped"])
    total["replaced"].extend(part.get("replaced", []))


def _new_result() -> dict:
    return {"installed": [], "replaced": [], "skipped": [], "warnings": []}


# ---- .zip of jars ------------------------------------------------------------------------------
def install_zip(src_path: str, server_dir: str, mc_version: str = "", fetch=None) -> dict:
    result = _new_result()
    try:
        zf = zipfile.ZipFile(src_path)
    except zipfile.BadZipFile:
        raise ModError("That isn't a valid .zip file.")
    with zf:
        if "modrinth.index.json" in zf.namelist():
            return install_mrpack(src_path, server_dir, mc_version, fetch)
        jars = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".jar")
                and "__MACOSX" not in i.filename]
        if not jars:
            raise ModError("No .jar files found inside the zip.")
        with tempfile.TemporaryDirectory() as tmp:
            for item in jars:
                if item.file_size > MAX_FILE_BYTES:
                    result["warnings"].append(f"{item.filename}: too large, skipped")
                    continue
                tmp_path = os.path.join(tmp, "mod.jar")
                with zf.open(item) as src, open(tmp_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                try:
                    _merge(result, install_jar(tmp_path, item.filename, server_dir))
                except ModError as exc:
                    result["warnings"].append(str(exc))
    if not result["installed"] and not result["skipped"]:
        raise ModError("; ".join(result["warnings"]) or "Nothing could be installed from that zip.")
    return result


# ---- Modrinth .mrpack --------------------------------------------------------------------------
class _RedirectGuard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        if parsed.scheme != "https" or parsed.hostname not in REDIRECT_HOSTS:
            raise ModError(f"Download redirected to a host that isn't allowed: {parsed.hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def default_fetch(url: str, limit: int = MAX_FILE_BYTES) -> bytes:
    opener = urllib.request.build_opener(_RedirectGuard)
    request = urllib.request.Request(url, headers={"User-Agent": "minecraft_server-smp/1.0"})
    try:
        with opener.open(request, timeout=60) as response:
            data = response.read(limit + 1)
    except (urllib.error.URLError, OSError) as exc:
        raise ModError(f"Download failed ({url}): {exc}")
    if len(data) > limit:
        raise ModError(f"Download too large: {url}")
    return data


def install_mrpack(src_path: str, server_dir: str, mc_version: str = "", fetch=None) -> dict:
    fetch = fetch or default_fetch
    result = _new_result()
    try:
        zf = zipfile.ZipFile(src_path)
    except zipfile.BadZipFile:
        raise ModError("That isn't a valid .mrpack file.")
    with zf, tempfile.TemporaryDirectory() as tmp:
        try:
            index = json.loads(zf.read("modrinth.index.json").decode("utf-8-sig"))
        except (KeyError, ValueError):
            raise ModError("This .mrpack has no readable modrinth.index.json.")
        deps = index.get("dependencies", {})
        if "fabric-loader" not in deps:
            loader = next((k for k in deps if k != "minecraft"), "another loader")
            raise ModError(f"This pack needs {loader}, but this server runs Fabric.")
        pack_mc = deps.get("minecraft")
        if mc_version and pack_mc and pack_mc != mc_version:
            raise ModError(f"This pack is for Minecraft {pack_mc}, but the server runs {mc_version}.")
        if not mc_version:
            result["warnings"].append("Server Minecraft version unknown (MC_VERSION), couldn't check the pack matches.")

        for entry in index.get("files", []):
            path = entry.get("path", "")
            if (entry.get("env") or {}).get("server") == "unsupported":
                result["skipped"].append(f"{os.path.basename(path)}: client-only, not needed on the server")
                continue
            rel = _safe_rel(path)
            url = (entry.get("downloads") or [""])[0]
            parsed = urllib.parse.urlparse(url)
            if parsed.scheme != "https" or parsed.hostname not in DOWNLOAD_HOSTS:
                raise ModError(f"{path}: download host not allowed ({parsed.hostname}).")
            hashes = entry.get("hashes") or {}
            data = fetch(url)
            if hashes.get("sha512"):
                ok = hashlib.sha512(data).hexdigest() == hashes["sha512"].lower()
            elif hashes.get("sha1"):
                ok = hashlib.sha1(data).hexdigest() == hashes["sha1"].lower()
            else:
                ok = False
            if not ok:
                raise ModError(f"{path}: the download doesn't match its published hash, refusing to install it.")
            _place(result, rel, data, server_dir, tmp)

        for prefix in ("overrides/", "server-overrides/"):
            for item in zf.infolist():
                if item.is_dir() or not item.filename.startswith(prefix):
                    continue
                try:
                    rel = _safe_rel(item.filename[len(prefix):])
                except ModError:
                    result["warnings"].append(f"{item.filename}: skipped (outside allowed folders)")
                    continue
                if item.file_size > MAX_FILE_BYTES:
                    result["warnings"].append(f"{item.filename}: too large, skipped")
                    continue
                _place(result, rel, zf.read(item), server_dir, tmp)
    if not result["installed"] and not result["skipped"]:
        raise ModError("The pack didn't contain anything to install.")
    return result


def _place(result: dict, rel: str, data: bytes, server_dir: str, tmp: str):
    if rel.startswith("mods/") and rel.lower().endswith(".jar"):
        tmp_path = os.path.join(tmp, "mod.jar")
        with open(tmp_path, "wb") as f:
            f.write(data)
        try:
            _merge(result, install_jar(tmp_path, rel, server_dir))
        except ModError as exc:
            result["warnings"].append(str(exc))
        return
    dest = os.path.join(server_dir, *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    result["installed"].append(f"config file {rel}")


# ---- entry points used by the panel ------------------------------------------------------------
def install_any(src_path: str, filename: str, server_dir: str, mc_version: str = "", fetch=None) -> dict:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".jar":
        result = _new_result()
        _merge(result, install_jar(src_path, filename, server_dir))
        return result
    if ext == ".mrpack":
        return install_mrpack(src_path, server_dir, mc_version, fetch)
    if ext == ".zip":
        return install_zip(src_path, server_dir, mc_version, fetch)
    raise ModError(f"'{filename}': upload a .jar, a .zip of jars, or a Modrinth .mrpack.")


def remove_mod(server_dir: str, filename: str) -> str:
    name = _safe_jar_name(filename)
    path = os.path.join(server_dir, "mods", name)
    if not os.path.isfile(path):
        raise ModError(f"'{name}' isn't installed.")
    _retire(server_dir, path)
    return name
