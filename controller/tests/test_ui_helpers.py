"""The look of the window: text that must fit its box, errors in plain words, the history rows, the version, and the progress
bar gliding in a real Tk window."""
import os
import re
import time
import unittest

from app import errors, sessions
from app.desktop import textfit
from app.shared import cap_message
from app.version import VERSION

TEN = lambda s: len(s) * 10          # a font where every character is 10 pixels wide


class FitTextTests(unittest.TestCase):
    def test_text_that_fits_is_left_alone(self):
        self.assertEqual(textfit.fit_text("Last host: sai", TEN, 200, 1), "Last host: sai")

    def test_long_text_is_wrapped_to_the_width(self):
        wrapped = textfit.fit_text("Press Start hosting when it's your turn.", TEN, 200, 3)
        self.assertEqual(wrapped, "Press Start hosting\nwhen it's your turn.")
        self.assertTrue(all(len(line) * 10 <= 200 for line in wrapped.split("\n")))

    def test_more_lines_than_the_box_has_ends_with_dots_and_still_fits(self):
        text = "The free daily limit has been used up so it refuses requests until its counters reset at 8:00 AM"
        cut = textfit.fit_text(text, TEN, 200, 2)
        lines = cut.split("\n")
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[-1].endswith("..."))
        self.assertTrue(all(len(line) * 10 <= 200 for line in lines))

    def test_a_long_name_becomes_one_line_with_dots(self):
        one = textfit.one_line("Last host: ChrisTzy was here aint simp", TEN, 330)
        self.assertEqual(one.count("\n"), 0)
        self.assertTrue(one.endswith("...") and len(one) * 10 <= 330)

    def test_a_word_longer_than_the_box_is_broken_and_line_breaks_are_kept(self):
        self.assertEqual(textfit.fit_text("A" * 25, TEN, 100, 5), "A" * 10 + "\n" + "A" * 10 + "\n" + "A" * 5)
        self.assertEqual(textfit.fit_text("one\ntwo", TEN, 200, 2), "one\ntwo")
        self.assertEqual(textfit.fit_text("", TEN, 200, 2), "")


class ErrorWordsTests(unittest.TestCase):
    def test_the_daily_limit_says_when_it_resets(self):
        head, detail = errors.summarize(cap_message())
        self.assertEqual(head, "The free daily limit is used up")
        self.assertIn("Your world is safe", detail)
        self.assertRegex(detail, r"resets at \d{1,2}:\d{2} ?[AP]M")

    def test_refused_keys_a_missing_bucket_and_no_internet_each_get_their_own_words(self):
        keys = errors.summarize("operation error S3: ListObjectsV2, StatusCode: 400, Credential access key has length 2, should be 32")
        self.assertEqual(keys[0], "The storage refused the keys")
        self.assertEqual(errors.summarize("NoSuchBucket: The specified bucket does not exist")[0], "The bucket doesn't exist")
        self.assertEqual(errors.summarize("dial tcp: lookup x.r2.cloudflarestorage.com: no such host")[0], "Can't reach the internet")

    def test_anything_else_keeps_the_first_sentence(self):
        head, detail = errors.summarize("Something odd happened. Then more text. And more.")
        self.assertEqual(head, "Can't reach the shared world")
        self.assertEqual(detail, "Something odd happened.")
        self.assertEqual(errors.summarize("")[0], "Can't reach the shared world")


class HistoryRowTests(unittest.TestCase):
    def test_a_row_has_columns_and_marks_a_missing_stop(self):
        start = time.mktime((2026, 10, 23, 2, 13, 0, 0, 0, -1))
        entry = {"host": "ChrisTzy was here aint simp", "start": start, "end": start + 3 * 3600 + 53 * 60, "ended": sessions.NO_STOP}
        row = sessions.row(entry)
        self.assertTrue(row.startswith("ChrisTzy was he...  "))
        self.assertIn("2:13 AM-6:06 AM", row)
        self.assertIn("3 h 53 min", row)
        self.assertTrue(row.endswith("  !"))
        stopped = dict(entry, ended="stopped")
        self.assertFalse(sessions.row(stopped).endswith("!"))
        running = {"host": "sai", "start": start, "end": None}
        self.assertIn("-now", sessions.row(running, now=start + 600))
        self.assertEqual(len(sessions.row(stopped).split("  ")[0]), 18)       # the host column is a fixed width


class VersionTests(unittest.TestCase):
    def test_the_app_version_matches_the_installer(self):
        iss = os.path.join(os.path.dirname(__file__), "..", "..", "desktop", "installer.iss")
        with open(iss, encoding="utf-8") as f:
            found = re.search(r'#define AppVersion "([^"]+)"', f.read())
        self.assertEqual(VERSION, found.group(1))


class ProgressBarMotionTests(unittest.TestCase):
    """The real bar in a real (hidden) window: it glides to the real value instead of jumping, and goes away at 100."""

    def setUp(self):
        import tkinter
        try:
            self.root = tkinter.Tk()
        except tkinter.TclError:
            self.skipTest("no display")
        self.root.withdraw()
        from app.desktop import feedback, gui
        self.bar = feedback.ProgressBar(gui.Scene(self.root, "Segoe UI", 400, 200), 20, 60, 360)

    def tearDown(self):
        self.root.destroy()

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.02)

    def test_it_glides_up_and_shows_the_percent(self):
        self.bar.show("Downloading the world", 1.0)
        seen = []
        for _ in range(12):
            self.pump(0.05)
            seen.append(self.bar.shown)
        self.assertEqual(seen, sorted(seen))
        self.assertGreater(seen[0], 0.0)                       # already moving...
        self.assertLess(seen[0], 0.9)                          # ...but not jumped straight to the end
        self.pump(1.5)
        self.assertGreater(self.bar.shown, 0.99)
        self.assertEqual(self.bar.canvas.itemcget(self.bar.percent.main, "text"), "100%")

    def test_a_step_without_numbers_creeps_and_a_finished_job_glides_to_100_then_hides(self):
        self.bar.show("Taking the hosting lock...", None, "", (0.0, 0.08), time.time())
        self.pump(0.4)
        first = self.bar.shown
        self.pump(0.6)
        self.assertGreater(self.bar.shown, first)               # it keeps moving though nothing was measured
        self.assertLess(self.bar.shown, 0.08 + 1e-6)            # and stays inside its slice
        self.bar.show("Starting the tunnel", 0.9)
        self.pump(1.0)
        self.bar.complete()
        self.pump(2.2)
        self.assertFalse(self.bar.visible)


if __name__ == "__main__":
    unittest.main()
