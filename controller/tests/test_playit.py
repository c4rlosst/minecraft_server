"""playit agent handling for shared hosting (no real playit is started or stopped)."""
import subprocess
import tempfile
import unittest

from app.playit import Playit


class Result:
    def __init__(self, stdout=""):
        self.stdout = stdout


class Rig:
    def __init__(self, status_output="", which="C:\\playit.exe"):
        self.calls, self.popens, self.status_output = [], [], status_output
        self.proc = type("P", (), {"terminated": False, "poll": lambda s: None,
                                   "terminate": lambda s: setattr(s, "terminated", True)})()

        def run(cmd, **kw):
            self.calls.append(cmd[1:])
            return Result(self.status_output if cmd[1:] == ["status"] else "")

        def popen(cmd, **kw):
            self.popens.append(cmd)
            return self.proc
        self.playit = Playit("playit", run=run, popen=popen, which=lambda exe: which)


class PlayitTests(unittest.TestCase):
    def test_starts_the_agent_when_it_is_not_running(self):
        rig = Rig(status_output="playit service status:\n  Phase: stopped\n")
        self.assertEqual(rig.playit.start(), "started")
        self.assertEqual(rig.popens, [["C:\\playit.exe"]])

    def test_leaves_a_running_agent_alone(self):
        rig = Rig(status_output="playit service status:\n  Phase: running\n  PID: 5172\n")
        self.assertEqual(rig.playit.start(), "already running")
        self.assertEqual(rig.popens, [])

    def test_stop_stops_the_service_and_our_own_process(self):
        rig = Rig(status_output="Phase: stopped")
        rig.playit.start()
        rig.playit.stop()
        self.assertIn(["stop"], rig.calls)
        self.assertTrue(rig.proc.terminated)

    def test_not_installed_is_harmless(self):
        rig = Rig(which=None)
        self.assertEqual(rig.playit.start(), "not installed")
        rig.playit.stop()
        self.assertEqual((rig.calls, rig.popens), ([], []))

    def test_status_failure_counts_as_not_running(self):
        def broken(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, 15)
        playit = Playit("playit", run=broken, popen=lambda *a, **k: None, which=lambda e: "x")
        self.assertFalse(playit.is_running())


class CleanupStopsPlayitTests(unittest.TestCase):
    def test_cleanup_stops_playit_even_after_a_handover(self):
        from app.config import Config
        from app.main import App, cleanup
        from app.shared import SharedWorld
        stopped = []
        app = App()
        with tempfile.TemporaryDirectory() as tmp:
            app.cfg = Config()
            app.runtime = None
            app.shared = SharedWorld(tmp, tmp, "Carlos")        # no lock held: handover already released it
            app.playit = type("P", (), {"stop": lambda self: stopped.append(True)})()
            cleanup(app)
        self.assertEqual(stopped, [True])


if __name__ == "__main__":
    unittest.main()
