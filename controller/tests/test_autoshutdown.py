"""Automatic PC shutdown tests. Everything that could really switch the PC off is replaced by a fake."""
import asyncio
import unittest

from app import autoshutdown
from app.autoshutdown import AutoShutdown, countdown
from app.config import Config
from app.manager import PlayersOnline
from app.shared import HostingError


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += seconds


class FakeManager:
    def __init__(self, state="asleep"):
        self.state, self.maintenance, self.players = state, False, 0


class FakeHosting:
    def __init__(self, error=None):
        self.error, self.handovers = error, 0

    async def handover(self):
        self.handovers += 1
        if self.error:
            raise self.error


class Rig:
    """An AutoShutdown wired to fakes. idle (seconds without input) is whatever the test sets."""
    def __init__(self, hosting=None, decision="proceed", **cfg):
        self.cfg = Config(auto_shutdown_minutes=30, auto_shutdown_user_idle_minutes=10, auto_shutdown_warning_seconds=60,
                          auto_shutdown_drive_wait=120, **cfg)
        self.clock, self.manager, self.hosting = Clock(), FakeManager(), hosting
        self.idle, self.decision = 3600.0, decision
        self.warnings, self.shutdowns, self.exited = [], [], []

        async def confirm(seconds, should_cancel, message):
            self.warnings.append((seconds, message))
            return self.decision(should_cancel) if callable(self.decision) else self.decision

        def shutdown(delay, message, force=False):
            self.shutdowns.append((delay, force))
        self.auto = AutoShutdown(self.cfg, self.manager, hosting, lambda: self.exited.append(True),
                                 idle_fn=lambda: self.idle, confirm_fn=confirm, shutdown_fn=shutdown,
                                 clock=self.clock, sleep=self.clock.sleep)

    def advance(self, minutes):
        self.clock.now += minutes * 60


class StepTests(unittest.TestCase):
    def go(self, coro):
        return asyncio.run(coro)

    def test_off_by_default(self):
        rig = Rig()
        rig.cfg.auto_shutdown_minutes = 0
        self.go(rig.auto.step())
        rig.advance(600)
        self.go(rig.auto.step())
        self.assertEqual((rig.warnings, rig.shutdowns), ([], []))

    def test_waits_for_the_server_to_be_quiet_for_long_enough(self):
        rig = Rig()
        self.go(rig.auto.step())                  # server just noticed asleep: the quiet timer starts
        rig.advance(29)
        self.go(rig.auto.step())
        self.assertEqual(rig.warnings, [])        # 29 min is not 30
        rig.advance(2)
        self.go(rig.auto.step())
        self.assertEqual(len(rig.warnings), 1)
        self.assertEqual(rig.shutdowns, [(15, False)])
        self.assertEqual(rig.exited, [True])

    def test_never_while_you_are_at_the_pc(self):
        rig = Rig()
        self.go(rig.auto.step())
        rig.advance(60)
        rig.idle = 30                              # you touched the mouse 30 seconds ago
        self.go(rig.auto.step())
        self.assertEqual((rig.warnings, rig.shutdowns), ([], []))
        rig.idle = 11 * 60                         # you walked away
        self.go(rig.auto.step())
        self.assertEqual(len(rig.warnings), 1)

    def test_players_online_reset_the_timer(self):
        rig = Rig()
        self.go(rig.auto.step())
        rig.advance(25)
        rig.manager.state, rig.manager.players = "online", 1       # a friend joined
        self.go(rig.auto.step())
        rig.manager.state, rig.manager.players = "asleep", 0       # ...and later it went to sleep
        rig.advance(29)
        self.go(rig.auto.step())
        self.assertEqual(rig.warnings, [])                         # only 29 min since they were last on
        rig.advance(2)
        self.go(rig.auto.step())
        self.assertEqual(len(rig.warnings), 1)

    def test_same_number_as_the_server_means_right_after_the_server_sleeps(self):
        rig = Rig()
        rig.cfg.auto_shutdown_minutes = 10                         # same as the server's IDLE_MINUTES
        rig.manager.state, rig.manager.players = "online", 2
        self.go(rig.auto.step())                                   # last player seen at t=0
        rig.advance(1)
        rig.manager.players = 0                                    # everyone leaves at t=1 min
        self.go(rig.auto.step())
        rig.advance(10)
        rig.manager.state = "online"                               # still awake: the server hasn't slept yet
        self.go(rig.auto.step())
        self.assertEqual(rig.warnings, [])                         # the server must be asleep before the PC may go
        rig.manager.state = "asleep"                               # the server sleeps after its own 10 minutes
        self.go(rig.auto.step())
        self.assertEqual(len(rig.warnings), 1)                     # PC warning appears right away, no second 10 minutes
        self.assertEqual(rig.shutdowns, [(15, False)])

    def test_a_server_that_is_starting_counts_as_busy(self):
        rig = Rig()
        rig.advance(29)
        rig.manager.state = "starting"
        self.go(rig.auto.step())
        rig.manager.state = "asleep"
        rig.advance(2)
        self.go(rig.auto.step())
        self.assertEqual(rig.warnings, [])                         # someone woke it 2 minutes ago

    def test_cancelling_the_warning_keeps_the_pc_on_and_waits_a_full_period_again(self):
        rig = Rig(decision="cancel")
        self.go(rig.auto.step())
        rig.advance(31)
        self.go(rig.auto.step())
        self.assertEqual((len(rig.warnings), rig.shutdowns), (1, []))
        rig.advance(20)
        self.go(rig.auto.step())
        self.assertEqual(len(rig.warnings), 1)     # not asked again after 20 min
        rig.advance(11)
        self.go(rig.auto.step())
        self.assertEqual(len(rig.warnings), 2)

    def test_the_warning_cancels_itself_if_someone_joins_or_you_come_back(self):
        seen = []

        def decide(should_cancel):
            seen.append(should_cancel())
            rig.manager.state = "online"            # a friend joins during the countdown
            seen.append(should_cancel())
            return "cancel"
        rig = Rig(decision=decide)
        self.go(rig.auto.step())
        rig.advance(31)
        self.go(rig.auto.step())
        self.assertEqual(seen, [False, True])
        self.assertEqual(rig.shutdowns, [])

        rig2 = Rig(decision=lambda should_cancel: "proceed" if not should_cancel() else "cancel")
        self.go(rig2.auto.step())
        rig2.advance(31)
        rig2.idle = 100                              # you moved the mouse after the warning appeared
        original = rig2.auto.idle_fn
        calls = {"n": 0}

        def idle():                                  # first call: away; during the countdown: back
            calls["n"] += 1
            return 3600.0 if calls["n"] == 1 else 2.0
        rig2.auto.idle_fn = idle
        self.go(rig2.auto.step())
        self.assertEqual(rig2.shutdowns, [])

    def test_hosting_hands_over_first_and_lets_drive_sync(self):
        hosting = FakeHosting()
        rig = Rig(hosting=hosting)
        self.go(rig.auto.step())
        rig.advance(31)
        self.go(rig.auto.step())
        self.assertEqual(hosting.handovers, 1)
        self.assertEqual(rig.shutdowns, [(120, False)])      # Windows waits 2 min for Drive
        self.assertEqual(rig.exited, [True])

    def test_no_shutdown_if_the_handover_is_refused(self):
        for error in (PlayersOnline(1, ["Alex"]), HostingError("lost the lock")):
            rig = Rig(hosting=FakeHosting(error))
            self.go(rig.auto.step())
            rig.advance(31)
            self.go(rig.auto.step())
            self.assertEqual((rig.shutdowns, rig.exited), ([], []), error)

    def test_force_setting_is_passed_through(self):
        rig = Rig(auto_shutdown_force=True)
        self.go(rig.auto.step())
        rig.advance(31)
        self.go(rig.auto.step())
        self.assertEqual(rig.shutdowns, [(15, True)])


class CountdownTests(unittest.TestCase):
    class Box:
        result = None

    def run_countdown(self, seconds, should_cancel=lambda: False, box=None):
        clock = Clock()
        return asyncio.run(countdown(seconds, should_cancel, box or self.Box(), sleep=clock.sleep, clock=clock))

    def test_time_runs_out_means_proceed(self):
        self.assertEqual(self.run_countdown(60), "proceed")

    def test_buttons_and_cancel_condition(self):
        box = self.Box()
        box.result = 1
        self.assertEqual(self.run_countdown(60, box=box), "proceed")      # Shut down now
        box.result = 2
        self.assertEqual(self.run_countdown(60, box=box), "cancel")       # Cancel
        self.assertEqual(self.run_countdown(60, should_cancel=lambda: True), "cancel")


class WindowsHelperTests(unittest.TestCase):
    def test_idle_seconds_is_a_sane_number(self):
        value = autoshutdown.user_idle_seconds()
        self.assertGreaterEqual(value, 0)
        self.assertLess(value, 60 * 60 * 24 * 40)

    def test_shutdown_command_is_built_correctly_without_running_it(self):
        captured = []
        original = autoshutdown.subprocess.run
        autoshutdown.subprocess.run = lambda cmd, **kw: captured.append(cmd)
        try:
            autoshutdown.windows_shutdown(90, "bye", force=False)
            autoshutdown.windows_shutdown(5, "bye", force=True)
        finally:
            autoshutdown.subprocess.run = original
        self.assertEqual(captured[0], ["shutdown", "/s", "/t", "90", "/c", "bye"])
        self.assertEqual(captured[1][-1], "/f")


if __name__ == "__main__":
    unittest.main()
