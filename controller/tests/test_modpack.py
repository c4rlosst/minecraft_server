"""Mod installer tests (standard library only)."""
import hashlib
import json
import os
import tempfile
import unittest
import zipfile

from app import modpack
from app.modpack import ModError


def make_jar(path, mod_id="examplemod", version="1.0.0", environment="*", loader="fabric"):
    with zipfile.ZipFile(path, "w") as zf:
        if loader == "fabric":
            zf.writestr("fabric.mod.json", json.dumps({"id": mod_id, "name": mod_id.title(), "version": version,
                                                       "environment": environment}))
        elif loader == "forge":
            zf.writestr("META-INF/mods.toml", "modLoader='javafml'")
        zf.writestr("data.txt", "x")
    return path


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = os.path.join(self.tmp.name, "server")
        os.makedirs(os.path.join(self.server, "mods"))
        self.src = os.path.join(self.tmp.name, "src")
        os.makedirs(self.src)

    def mods(self):
        return sorted(os.listdir(os.path.join(self.server, "mods")))

    # ---- single jars
    def test_install_fabric_jar_and_list_it(self):
        jar = make_jar(os.path.join(self.src, "cool-1.0.jar"), "cool", "1.0")
        result = modpack.install_any(jar, "cool-1.0.jar", self.server)
        self.assertEqual(self.mods(), ["cool-1.0.jar"])
        self.assertIn("Cool 1.0", result["installed"][0])
        listed = modpack.list_mods(self.server)
        self.assertEqual((listed[0]["id"], listed[0]["version"]), ("cool", "1.0"))

    def test_new_version_of_same_mod_replaces_old_one_and_keeps_a_copy(self):
        modpack.install_any(make_jar(os.path.join(self.src, "cool-1.0.jar"), "cool", "1.0"), "cool-1.0.jar", self.server)
        result = modpack.install_any(make_jar(os.path.join(self.src, "cool-2.0.jar"), "cool", "2.0"), "cool-2.0.jar", self.server)
        self.assertEqual(self.mods(), ["cool-2.0.jar"])
        self.assertEqual(result["replaced"], ["cool-1.0.jar"])
        self.assertEqual(len(os.listdir(os.path.join(self.server, "mods-removed"))), 1)

    def test_forge_and_non_mod_jars_are_rejected_with_a_clear_reason(self):
        forge = make_jar(os.path.join(self.src, "forge.jar"), loader="forge")
        with self.assertRaises(ModError) as ctx:
            modpack.install_any(forge, "forge.jar", self.server)
        self.assertIn("Forge", str(ctx.exception))
        plain = make_jar(os.path.join(self.src, "plain.jar"), loader="none")
        with self.assertRaises(ModError) as ctx:
            modpack.install_any(plain, "plain.jar", self.server)
        self.assertIn("Fabric", str(ctx.exception))
        junk = os.path.join(self.src, "junk.jar")
        open(junk, "w").write("not a zip")
        with self.assertRaises(ModError):
            modpack.install_any(junk, "junk.jar", self.server)
        self.assertEqual(self.mods(), [])

    def test_client_only_mod_is_skipped(self):
        jar = make_jar(os.path.join(self.src, "shaders.jar"), "shaders", environment="client")
        result = modpack.install_any(jar, "shaders.jar", self.server)
        self.assertEqual(self.mods(), [])
        self.assertIn("client-only", result["skipped"][0])

    def test_path_tricks_in_file_names_are_neutralised_or_refused(self):
        jar = make_jar(os.path.join(self.src, "evil.jar"), "evil")
        modpack.install_any(jar, "..\\..\\evil.jar", self.server)       # only the base name is used
        self.assertEqual(self.mods(), ["evil.jar"])
        with self.assertRaises(ModError):
            modpack.install_any(jar, "bad;name.jar", self.server)
        with self.assertRaises(ModError):
            modpack.install_any(jar, "notamod.exe", self.server)
        with self.assertRaises(ModError):
            modpack.remove_mod(self.server, "..\\server.properties")

    def test_remove_moves_to_mods_removed(self):
        modpack.install_any(make_jar(os.path.join(self.src, "a.jar"), "a"), "a.jar", self.server)
        modpack.remove_mod(self.server, "a.jar")
        self.assertEqual(self.mods(), [])
        self.assertEqual(len(os.listdir(os.path.join(self.server, "mods-removed"))), 1)
        with self.assertRaises(ModError):
            modpack.remove_mod(self.server, "a.jar")

    # ---- zip
    def test_zip_with_jars_in_folders_installs_valid_ones_and_reports_bad_ones(self):
        zpath = os.path.join(self.src, "pack.zip")
        a, b, c = (make_jar(os.path.join(self.src, n), i) for n, i in (("a.jar", "a"), ("b.jar", "b"), ("c.jar", "c"))
                   )
        bad = make_jar(os.path.join(self.src, "forge.jar"), loader="forge")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.write(a, "mods/a.jar")
            zf.write(b, "deep/er/b.jar")
            zf.write(bad, "mods/forge.jar")
            zf.writestr("../../escape.jar", open(c, "rb").read())   # traversal attempt: only the base name is used
        result = modpack.install_any(zpath, "pack.zip", self.server)
        self.assertEqual(self.mods(), ["a.jar", "b.jar", "escape.jar"])
        self.assertEqual(len(result["warnings"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape.jar")))

    def test_zip_without_jars_is_an_error(self):
        zpath = os.path.join(self.src, "empty.zip")
        with zipfile.ZipFile(zpath, "w") as zf:
            zf.writestr("readme.txt", "hi")
        with self.assertRaises(ModError):
            modpack.install_any(zpath, "empty.zip", self.server)

    # ---- mrpack
    def build_pack(self, files, deps=None, overrides=None, name="pack.mrpack"):
        path = os.path.join(self.src, name)
        index = {"formatVersion": 1, "game": "minecraft", "name": "Test",
                 "dependencies": deps or {"minecraft": "26.3", "fabric-loader": "0.19.5"}, "files": files}
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("modrinth.index.json", json.dumps(index))
            for rel, content in (overrides or {}).items():
                zf.writestr(rel, content)
        return path

    def entry(self, path, data, url="https://cdn.modrinth.com/data/x/versions/y/file.jar", env=None, sha512=None):
        e = {"path": path, "downloads": [url], "hashes": {"sha512": sha512 or hashlib.sha512(data).hexdigest()}}
        if env:
            e["env"] = env
        return e

    def jar_bytes(self, mod_id):
        p = make_jar(os.path.join(self.src, mod_id + ".tmp"), mod_id)
        return open(p, "rb").read()

    def test_mrpack_downloads_verifies_and_installs(self):
        data = {"one": self.jar_bytes("one"), "two": self.jar_bytes("two")}
        urls = {}
        files = []
        for k, v in data.items():
            url = f"https://cdn.modrinth.com/data/{k}/{k}.jar"
            urls[url] = v
            files.append(self.entry(f"mods/{k}.jar", v, url))
        files.append(self.entry("mods/clientonly.jar", b"zz", "https://cdn.modrinth.com/data/c/c.jar",
                                env={"client": "required", "server": "unsupported"}))
        pack = self.build_pack(files, overrides={"overrides/config/one.toml": "a=1", "overrides/shaderpacks/x.zip": "no"})
        result = modpack.install_any(pack, "pack.mrpack", self.server, "26.3", fetch=lambda url: urls[url])
        self.assertEqual(self.mods(), ["one.jar", "two.jar"])
        self.assertEqual(open(os.path.join(self.server, "config", "one.toml")).read(), "a=1")
        self.assertFalse(os.path.exists(os.path.join(self.server, "shaderpacks")))
        self.assertTrue(any("client-only" in s for s in result["skipped"]))
        self.assertTrue(any("shaderpacks" in w for w in result["warnings"]))

    def test_mrpack_rejects_hash_mismatch_host_loader_version_and_traversal(self):
        good = self.jar_bytes("one")
        cases = {
            "hash": self.build_pack([self.entry("mods/one.jar", good, sha512="00" * 64)], name="h.mrpack"),
            "host": self.build_pack([self.entry("mods/one.jar", good, url="https://evil.example.com/one.jar")], name="o.mrpack"),
            "loader": self.build_pack([], deps={"minecraft": "26.3", "forge": "1"}, name="l.mrpack"),
            "version": self.build_pack([self.entry("mods/one.jar", good)], deps={"minecraft": "1.20.1", "fabric-loader": "1"}, name="v.mrpack"),
            "path": self.build_pack([self.entry("../../evil.jar", good)], name="p.mrpack"),
            "dir": self.build_pack([self.entry("scripts/run.bat", good)], name="d.mrpack"),
        }
        for label, pack in cases.items():
            with self.subTest(label), self.assertRaises(ModError):
                modpack.install_any(pack, os.path.basename(pack), self.server, "26.3", fetch=lambda url: good)
        self.assertEqual(self.mods(), [])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "evil.jar")))

    def test_zip_containing_an_index_is_treated_as_a_pack(self):
        good = self.jar_bytes("one")
        pack = self.build_pack([self.entry("mods/one.jar", good)], name="renamed.zip")
        modpack.install_any(pack, "renamed.zip", self.server, "26.3", fetch=lambda url: good)
        self.assertEqual(self.mods(), ["one.jar"])


if __name__ == "__main__":
    unittest.main()
