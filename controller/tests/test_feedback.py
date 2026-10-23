"""The plain-words feedback after a click: what Refresh found, why a greyed-out button can't be used, the bar's caption."""
import unittest

from app.desktop import feedback

NOW = 1_800_000_000.0


def state(version=13, status="idle", host=None, last_host="ChrisTzy", ago_seconds=5220):
    return {"version": version, "status": status, "host": host, "lastHost": last_host, "lastPush": NOW - ago_seconds}


class RefreshWordingTests(unittest.TestCase):
    def test_nothing_changed(self):
        kind, text = feedback.describe_refresh(state(), state(), None, "sai", now=NOW)
        self.assertEqual(kind, "ok")
        self.assertIn("Up to date: version 13", text)

    def test_a_new_version_says_who_uploaded_it_and_what_to_do(self):
        kind, text = feedback.describe_refresh(state(13), state(14, last_host="hypernacho", ago_seconds=120), None, "sai", now=NOW)
        self.assertEqual(kind, "ok")
        self.assertIn("version 14", text)
        self.assertIn("hypernacho", text)
        self.assertIn("Start hosting downloads it", text)

    def test_someone_else_started_hosting_is_a_warning(self):
        kind, text = feedback.describe_refresh(state(), state(status="hosting", host="Ethan"), None, "sai", now=NOW)
        self.assertEqual(kind, "warn")
        self.assertIn("Ethan is hosting", text)

    def test_hosting_became_free(self):
        kind, text = feedback.describe_refresh(state(status="hosting", host="Ethan"), state(), None, "sai", now=NOW)
        self.assertEqual(kind, "ok")
        self.assertIn("nobody is hosting", text)

    def test_the_first_look_and_the_errors(self):
        self.assertIn("Checked: version 13", feedback.describe_refresh(None, state(), None, "sai", now=NOW)[1])
        self.assertEqual(feedback.describe_refresh(state(), None, Exception("boom"), "sai", now=NOW)[0], "error")
        self.assertEqual(feedback.describe_refresh(None, None, None, "sai", now=NOW)[0], "warn")      # an empty storage


class BlockedWordingTests(unittest.TestCase):
    def test_every_greyed_out_button_says_why(self):
        for button in ("start", "stop", "panel"):
            self.assertTrue(feedback.blocked_reason(button, False, False, "", False))
        self.assertIn("already hosting", feedback.blocked_reason("start", True, True, "", True))
        self.assertIn("not hosting", feedback.blocked_reason("stop", False, False, "", False))
        self.assertIn("Start hosting first", feedback.blocked_reason("panel", False, False, "", False))
        self.assertIn("still getting ready", feedback.blocked_reason("panel", True, False, "", True))
        self.assertIn("Done", feedback.blocked_reason("stop", True, True, "Saving, uploading...", True))


class BarCaptionTests(unittest.TestCase):
    def test_a_transfer_shows_how_much_has_moved(self):
        label, fraction, detail, span, since = feedback.bar_text({"label": "Uploading the world", "fraction": 0.5,
                                                                 "bytes": 310 * 1048576, "files": 180})
        self.assertEqual((label, fraction, span), ("Uploading the world", 0.5, None))
        self.assertEqual(detail, "310.0 MB transferred  -  180 files")

    def test_a_step_without_numbers_pulses(self):
        label, fraction, _detail, span, since = feedback.bar_text(
            {"label": "Taking the hosting lock...", "fraction": None, "span": [0.0, 0.08], "stepStarted": 100.0})
        self.assertEqual((label, fraction, span, since), ("Taking the hosting lock...", None, (0.0, 0.08), 100.0))


class BarMotionTests(unittest.TestCase):
    def test_a_step_without_numbers_creeps_forward_inside_its_slice(self):
        values = [feedback.creep(0.0, 0.08, since=0.0, now=t) for t in (0, 1, 3, 6, 12, 60)]
        self.assertEqual(values, sorted(values))                                  # always moving forward
        self.assertEqual(values[0], 0.0)
        self.assertLess(values[-1], 0.08 + 1e-9)                                  # and never reaching the next step early
        self.assertGreater(values[3], 0.04)                                       # but not stuck at the start either

    def test_the_drawn_value_glides_to_the_real_one(self):
        shown, steps = 0.0, 0
        while shown < 0.99 and steps < 200:
            nxt = feedback.ease(shown, 1.0, 0.033)
            self.assertGreater(nxt, shown)                                        # every frame moves on
            shown, steps = nxt, steps + 1
        self.assertLess(steps, 60)                                                # about 1.5 seconds at 30 frames a second
        self.assertEqual(feedback.ease(0.5, 0.45, 0.033), 0.5)                    # a small step back is ignored
        self.assertEqual(feedback.ease(0.9, 0.0, 0.033), 0.0)                     # a new job starts from the beginning


if __name__ == "__main__":
    unittest.main()
