"""The main window in a real (hidden) Tk window: the progress bar's life from start to finish, and that dynamic text always
fits its box. Nothing here touches the network or the real SMP Host folder."""
import os
import tempfile
import time
import tkinter
import unittest

from app import progress
from app.desktop import gui, pixel
from app.desktop import invite as invites
from app.desktop.home import Home

NOW = time.time()


def pump(root, seconds):
    end = time.time() + seconds
    while time.time() < end:
        root.update()
        time.sleep(0.02)


class WindowFlowTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Home(os.path.join(self.tmp.name, "pc"))
        env = {"RCON_PASSWORD": "x", "SERVER_NAME": "JAPAX", "PUBLIC_ADDRESS": "play.example.net"}
        storage = {"type": "r2", "bucket": "b", "account_id": "abc", "access_key": "AK", "secret_key": "SK"}
        home.apply_invite(invites.read(invites.make(env, "", storage)), "Ethan", total_gb=16)
        self.patches = [(gui.App, name, getattr(gui.App, name)) for name in ("refresh_shared", "poll_controller", "tick")]
        for _cls, name, _old in self.patches:
            setattr(gui.App, name, lambda self, *a, **k: None)          # no network, no background polling
        self.addCleanup(lambda: [setattr(c, n, o) for c, n, o in self.patches])
        self.app = gui.App(self.root, home, pixel.register_fonts())
        self.app.controller.running = lambda: False
        self.addCleanup(self.root.destroy)

    def tick(self):
        self.app.progress_tick()
        if self.app.progress_job is not None:
            self.root.after_cancel(self.app.progress_job)
            self.app.progress_job = None

    def test_the_bar_follows_a_job_and_finishes_at_100_then_the_players_row_returns(self):
        app = self.app
        job = progress.Progress(app.progress_path, min_interval=0)
        job.start_journey("start")
        job.begin("download", "Downloading the world", {"world": 100})
        job.stage("world")
        job.transfer({"bytes": 60, "totalBytes": 100, "transfers": 3})
        self.tick()
        self.assertTrue(app.bar.visible)
        self.assertIn("Downloading", app.bar.canvas.itemcget(app.bar.label.main, "text"))
        self.assertEqual(app.scene.canvas.itemcget(app.slot_bgs[0], "state"), "hidden")      # the bar replaces the players row
        pump(self.root, 1.0)
        self.assertGreater(app.bar.target, 0.5)
        job.end_journey()                                                                   # the controller says: all done
        app.bar.show("x", 0.95)                                                             # (the bar had got to the end)
        pump(self.root, 0.3)
        self.tick()
        pump(self.root, 2.5)
        self.assertFalse(app.bar.visible)                                                   # glided to 100 and went away
        self.tick()
        self.assertEqual(app.scene.canvas.itemcget(app.slot_bgs[0], "state"), "normal")

    def test_a_job_that_stops_early_hides_the_bar_at_once_instead_of_pretending_to_finish(self):
        app = self.app
        job = progress.Progress(app.progress_path, min_interval=0)
        job.start_journey("start")
        job.note("Taking the hosting lock...", step="lock")
        self.tick()
        self.assertTrue(app.bar.visible)
        job.end_journey()                                                                   # e.g. the lock was refused
        self.tick()
        self.assertFalse(app.bar.visible)

    def test_while_getting_ready_the_bar_creeps_and_never_goes_back_when_the_real_job_appears(self):
        app = self.app
        app.controller.running = lambda: True
        self.tick()
        self.assertTrue(app.bar.visible)
        pump(self.root, 0.8)
        before = app.bar.shown
        job = progress.Progress(app.progress_path, min_interval=0)
        job.start_journey("start")
        job.note("Taking the hosting lock...", step="lock")
        self.tick()
        pump(self.root, 0.5)
        self.assertGreaterEqual(app.bar.shown, before)

    def test_a_long_host_name_and_a_long_error_fit_their_boxes(self):
        app, sc = self.app, self.app.scene
        app.shared_state = {"status": "hosting", "host": "ChrisTzy was here aint simp", "lastHost": "ChrisTzy was here aint simp",
                            "version": 24, "lastPush": NOW - 720, "lastSeen": NOW}
        app.render()
        text = app.shared_txt.canvas.itemcget(app.shared_txt.main, "text")
        measure = sc.measure(12)
        self.assertLessEqual(len(text.split("\n")), 4)
        self.assertTrue(all(measure(line) <= 360 for line in text.split("\n")))
        light = app.light_txt.canvas.itemcget(app.light_txt.main, "text")
        self.assertLessEqual(sc.measure(10)(light), 230)
        app.shared_state = None
        app.shared_error = "operation error S3: ListObjectsV2, https response error StatusCode: 400, Credential access key has length 2, should be 32 " * 4
        app.render()
        text = app.shared_txt.canvas.itemcget(app.shared_txt.main, "text")
        self.assertTrue(text.startswith("The storage refused the keys"))
        self.assertLessEqual(len(text.split("\n")), 4)
        self.assertTrue(all(measure(line) <= 360 for line in text.split("\n")))
        logged = app.log_box.get("1.0", "end")
        self.assertIn("Credential access key", logged)                                       # the full text went to the log...
        app.render()
        self.assertEqual(app.log_box.get("1.0", "end").count("Credential access key"), logged.count("Credential access key"))   # ...once


if __name__ == "__main__":
    unittest.main()
