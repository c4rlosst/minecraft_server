"""Checkpoints: while the server runs, the world is saved to the shared storage every few minutes, so a crash costs minutes
instead of the whole session. Folders stand in for the cloud; a fake RCON records what the server was told."""
import asyncio
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace

from app.hosting import Hosting
from app.rclone import Rclone
from app.shared import RcloneWorld, SharedWorld


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class FakeManager:
    def __init__(self, fail_commands=()):
        self.state, self.players, self.last_active = "online", 2, None
        self.clock = time.monotonic
        self.cfg = SimpleNamespace(mc_host="127.0.0.1", rcon_port=25575, rcon_password="x")
        self.sent, self.fail = [], list(fail_commands)

    async def rcon(self, host, port, password, command, timeout=5.0):
        self.sent.append(command)
        if command in self.fail:
            self.fail.remove(command)
            raise RuntimeError("rcon down")
        return "ok"


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cloud = os.path.join(self.tmp.name, "cloud")
        self.clock = [1_800_000_000.0]

    def world(self, name):
        server = os.path.join(self.tmp.name, name, "server")
        write(os.path.join(server, "world", "level.dat"), f"{name}-start")
        write(os.path.join(server, "world", "playerdata", "p0.dat"), "p0")
        return RcloneWorld(self.cloud, server, name, Rclone("rclone"), clock=lambda: self.clock[0], sleep=lambda s: None)

    def hosting(self, world, manager=None):
        hosting = Hosting(world, manager or FakeManager())
        hosting.snapshot_dir = os.path.join(self.tmp.name, world.host, "snapshot")
        return hosting

    def run_async(self, coro):
        return asyncio.run(coro)

    def test_a_checkpoint_saves_the_running_world_and_a_crash_loses_only_what_came_after(self):
        a = self.world("A")
        a.acquire(claim_wait=0)
        a.push()                                                                   # version 1
        hosting = self.hosting(a)
        write(os.path.join(a.server_dir, "world", "level.dat"), "A-after-1h")      # an hour of play...
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "saved")
        self.assertEqual(hosting.manager.sent, ["save-off", "save-all flush", "save-on"])
        write(os.path.join(a.server_dir, "world", "level.dat"), "A-lost-in-the-crash")   # ...then the PC dies
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "A-after-1h")
        self.assertEqual(json.loads(read(os.path.join(self.cloud, "state.json")))["version"], 2)
        history = os.path.join(self.cloud, "history")
        self.assertEqual(len(os.listdir(history)) if os.path.isdir(history) else 0, 0)   # a checkpoint keeps no undo copies
        # A's lock goes silent; B takes over and starts from the checkpoint
        self.clock[0] += 3600
        b = self.world("B")
        b.acquire(takeover=True, claim_wait=0)
        b.pull()
        self.assertEqual(read(os.path.join(b.server_dir, "world", "level.dat")), "A-after-1h")

    def test_saving_is_turned_back_on_even_when_the_copy_fails(self):
        a = self.world("A")
        a.acquire(claim_wait=0)
        a.push()
        hosting = self.hosting(a)

        def broken(dest):
            raise RuntimeError("disk full")
        a.snapshot = broken
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "failed")
        self.assertEqual(hosting.manager.sent, ["save-off", "save-all flush", "save-on"])

    def test_a_failed_save_on_is_retried(self):
        a = self.world("A")
        a.acquire(claim_wait=0)
        a.push()
        hosting = self.hosting(a, FakeManager(fail_commands=["save-on"]))
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "saved")
        self.assertEqual(hosting.manager.sent.count("save-on"), 2)

    def test_it_waits_when_the_server_is_not_running_or_nobody_played(self):
        a = self.world("A")
        a.acquire(claim_wait=0)
        a.push()
        manager = FakeManager()
        hosting = self.hosting(a, manager)
        manager.state = "starting"
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "busy")
        manager.state, manager.players, manager.last_active = "online", 0, manager.clock() - 7200
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "idle")
        self.assertEqual(manager.sent, [])                                          # the server was never touched

    def test_a_synced_folder_world_has_no_checkpoints(self):
        folder = os.path.join(self.tmp.name, "drive")
        os.makedirs(folder)
        world = SharedWorld(folder, os.path.join(self.tmp.name, "A", "server"), "A", clock=lambda: self.clock[0], sleep=lambda s: None)
        hosting = self.hosting(world)
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "unsupported")

    def test_a_checkpoint_never_overwrites_a_newer_upload(self):
        a, b = self.world("A"), self.world("B")
        a.acquire(claim_wait=0)
        a.push()
        a.release()
        b.acquire(claim_wait=0)
        b.pull()
        write(os.path.join(b.server_dir, "world", "level.dat"), "B-v2")
        b.push()
        b.release()                                                                 # version 2
        stale = RcloneWorld(self.cloud, a.server_dir, "A", Rclone("rclone"), clock=lambda: self.clock[0], sleep=lambda s: None)
        stale.acquire(claim_wait=0)
        stale.mark_local_version(1)                                                 # A's copy is based on version 1
        hosting = self.hosting(stale)
        self.assertEqual(self.run_async(hosting.checkpoint_now()), "failed")
        self.assertEqual(read(os.path.join(self.cloud, "data", "world", "level.dat")), "B-v2")


if __name__ == "__main__":
    unittest.main()
