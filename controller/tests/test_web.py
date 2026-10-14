"""Panel API tests. Needs aiohttp (installed in controller/.venv); skipped without it."""
import asyncio
import io
import json
import os
import tempfile
import unittest
import zipfile

try:
    import aiohttp
    from aiohttp.test_utils import TestClient, TestServer
    from app.web import build_app
except ImportError:  # pragma: no cover
    aiohttp = None

from app.config import Config
from app.manager import Manager


class FakeRuntime:
    def __init__(self):
        self.status = "exited"

    def state(self, name):
        return self.status

    def start(self, name):
        self.status = "running"

    def stop(self, name, grace=120):
        self.status = "exited"

    def logs(self, name, tail=200):
        return "line1\nline2"


def jar_bytes(mod_id="cool", version="1.0"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("fabric.mod.json", json.dumps({"id": mod_id, "name": mod_id.title(), "version": version}))
    return buf.getvalue()


@unittest.skipIf(aiohttp is None, "aiohttp not installed")
class PanelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server_dir = os.path.join(self.tmp.name, "server")
        os.makedirs(os.path.join(self.server_dir, "mods"))
        self.cfg = Config(panel_password="s3cret", server_dir=self.server_dir, mc_version="26.3",
                          state_dir=os.path.join(self.tmp.name, "state"),
                          whitelist_path=os.path.join(self.server_dir, "whitelist.json"))
        self.runtime = FakeRuntime()
        self.manager = Manager(self.cfg, self.runtime)

    def run_client(self, scenario):
        async def go():
            async with TestClient(TestServer(build_app(self.cfg, self.manager))) as client:
                return await scenario(client)
        return asyncio.run(go())

    async def login(self, client):
        resp = await client.post("/api/login", json={"password": "s3cret"})
        self.assertEqual(resp.status, 200)

    def upload(self, client, files):
        form = aiohttp.FormData()
        for name, data in files:
            form.add_field("file", data, filename=name, content_type="application/octet-stream")
        return client.post("/api/mods/upload", data=form)

    def test_login_is_required_and_wrong_password_rejected(self):
        async def scenario(client):
            self.assertEqual((await client.get("/api/status")).status, 401)
            self.assertEqual((await client.get("/api/mods")).status, 401)
            self.assertEqual((await client.post("/api/login", json={"password": "nope"})).status, 401)
            public = await (await client.get("/api/public")).json()
            self.assertIn("state", public)
            await self.login(client)
            status = await (await client.get("/api/status")).json()
            return status
        status = self.run_client(scenario)
        self.assertEqual(status["state"], "asleep")
        self.assertTrue(status["mods_enabled"])

    def test_lockout_after_repeated_wrong_passwords(self):
        async def scenario(client):
            codes = [(await client.post("/api/login", json={"password": "bad"})).status for _ in range(6)]
            right = (await client.post("/api/login", json={"password": "s3cret"})).status
            return codes, right
        codes, right = self.run_client(scenario)
        self.assertEqual(codes[:5], [401] * 5)
        self.assertEqual(codes[5], 429)
        self.assertEqual(right, 429)

    def test_upload_list_replace_remove(self):
        async def scenario(client):
            await self.login(client)
            r1 = await (await self.upload(client, [("cool-1.0.jar", jar_bytes("cool", "1.0"))])).json()
            r2 = await (await self.upload(client, [("cool-2.0.jar", jar_bytes("cool", "2.0"))])).json()
            listing = await (await client.get("/api/mods")).json()
            removed = await client.delete("/api/mods/cool-2.0.jar")
            after = await (await client.get("/api/mods")).json()
            return r1, r2, listing, removed.status, after
        r1, r2, listing, removed_status, after = self.run_client(scenario)
        self.assertTrue(r1["results"][0]["ok"])
        self.assertEqual(r2["results"][0]["replaced"], ["cool-1.0.jar"])
        self.assertEqual([m["file"] for m in listing["mods"]], ["cool-2.0.jar"])
        self.assertTrue(listing["editable"])
        self.assertEqual(removed_status, 200)
        self.assertEqual(after["mods"], [])
        self.assertFalse(self.manager.maintenance)

    def test_bad_files_get_a_clear_error_and_good_ones_still_install(self):
        async def scenario(client):
            await self.login(client)
            resp = await self.upload(client, [("notes.txt", b"hello"), ("good.jar", jar_bytes("good")), ("bad.jar", b"not a zip")])
            return await resp.json()
        results = self.run_client(scenario)["results"]
        by_file = {r["file"]: r for r in results}
        self.assertFalse(by_file["notes.txt"]["ok"])
        self.assertTrue(by_file["good.jar"]["ok"])
        self.assertFalse(by_file["bad.jar"]["ok"])
        self.assertIn("valid .jar", by_file["bad.jar"]["error"])

    def test_mods_cannot_change_while_the_server_is_awake(self):
        self.runtime.status = "running"      # container up, port not open: "starting"

        async def scenario(client):
            await self.login(client)
            await self.manager.refresh()
            up = await self.upload(client, [("a.jar", jar_bytes("a"))])
            rm = await client.delete("/api/mods/a.jar")
            return up.status, rm.status, (await up.json())["error"]
        up_status, rm_status, message = self.run_client(scenario)
        self.assertEqual((up_status, rm_status), (409, 409))
        self.assertIn("Stop the server", message)
        self.assertEqual(os.listdir(os.path.join(self.server_dir, "mods")), [])

    def test_stop_is_refused_while_players_are_online_unless_forced(self):
        self.runtime.status = "running"
        self.manager.ping = lambda *a, **k: asyncio.sleep(0, {"players": {"online": 2, "max": 10,
                                                                          "sample": [{"name": "Alex"}, {"name": "Sam"}]}})

        async def scenario(client):
            await self.login(client)
            await self.manager.refresh()                       # server is up with 2 players
            refused = await client.post("/api/stop")
            body = await refused.json()
            state_after_refusal = self.manager.state
            forced = await client.post("/api/stop?force=1")
            await self.manager._stop_task
            return refused.status, body, state_after_refusal, forced.status
        status, body, state_after, forced_status = self.run_client(scenario)
        self.assertEqual(status, 409)
        self.assertTrue(body["can_force"])
        self.assertIn("Alex", body["error"])
        self.assertEqual(state_after, "online")                # still running after the refusal
        self.assertEqual(forced_status, 200)

    def test_server_cannot_start_while_mods_are_changing(self):
        async def go():
            self.assertTrue(await self.manager.begin_maintenance())
            self.assertFalse(await self.manager.start())          # a joining friend can't wake it mid-upload
            self.assertFalse(await self.manager.begin_maintenance())
            self.manager.end_maintenance()
            return await self.manager.start()
        self.assertTrue(asyncio.run(go()))

    def test_not_available_without_a_server_folder(self):
        self.cfg.server_dir = ""                                 # Docker backend

        async def scenario(client):
            await self.login(client)
            return (await client.get("/api/mods")).status, (await self.upload(client, [("a.jar", b"x")])).status
        self.assertEqual(self.run_client(scenario), (501, 501))

    def test_whitelist_endpoint_reports_offline_mode(self):
        self.cfg.account_mode = "offline"
        manager = Manager(self.cfg, self.runtime)

        async def go():
            async with TestClient(TestServer(build_app(self.cfg, manager))) as client:
                await self.login(client)
                resp = await client.post("/api/whitelist", json={"op": "add", "name": "Alex"})
                return resp.status, (await resp.json())["error"]
        status, message = asyncio.run(go())
        self.assertEqual(status, 400)
        self.assertIn("offline mode", message)


if __name__ == "__main__":
    unittest.main()
