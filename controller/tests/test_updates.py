"""The new-version notice, the low-disk check and the plain-words error sentence."""
import json
import os
import shutil
import tempfile
import unittest
from collections import namedtuple
from unittest import mock

from app import errors, updates
from app.desktop.home import Home


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self.payload if isinstance(self.payload, bytes) else json.dumps(self.payload).encode("utf-8")


class UpdateCheckTests(unittest.TestCase):
    def test_versions_compare_as_numbers_not_text(self):
        self.assertEqual(updates.parse_version("v1.0.10"), (1, 0, 10))
        self.assertTrue(updates.is_newer("v1.0.10", "1.0.9"))              # 10 is more than 9
        self.assertFalse(updates.is_newer("v1.0.6", "1.0.6"))
        self.assertFalse(updates.is_newer("v1.0.5", "1.0.6"))
        self.assertFalse(updates.is_newer("nightly", "1.0.6"))             # not a version: never nag

    def test_a_newer_release_is_reported_with_its_page(self):
        opener = lambda request, timeout=0: FakeResponse({"tag_name": "v1.0.7", "html_url": "https://example/releases/v1.0.7"})
        self.assertEqual(updates.check("1.0.6", opener), {"version": "1.0.7", "url": "https://example/releases/v1.0.7"})

    def test_nothing_is_said_when_up_to_date_offline_or_when_the_answer_is_garbage(self):
        same = lambda request, timeout=0: FakeResponse({"tag_name": "v1.0.6"})
        self.assertIsNone(updates.check("1.0.6", same))

        def offline(request, timeout=0):
            raise OSError("no internet")
        self.assertIsNone(updates.check("1.0.6", offline))
        self.assertIsNone(updates.check("1.0.6", lambda request, timeout=0: FakeResponse(b"<html>rate limited</html>")))


class DiskTests(unittest.TestCase):
    def test_a_nearly_full_disk_stops_hosting_with_a_plain_sentence(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Home(os.path.join(tmp.name, "pc"))
        os.makedirs(home.root)
        Usage = namedtuple("Usage", "total used free")
        with mock.patch.object(shutil, "disk_usage", return_value=Usage(500e9, 499e9, 0.8e9)):
            problem = home.disk_problem()
        self.assertIn("0.8 GB", problem)
        self.assertIn("Free up some space", problem)
        with mock.patch.object(shutil, "disk_usage", return_value=Usage(500e9, 100e9, 400e9)):
            self.assertEqual(home.disk_problem(), "")


class ExplainTests(unittest.TestCase):
    def test_known_errors_become_a_sentence_and_unknown_ones_are_left_alone(self):
        self.assertIn("refused the keys", errors.explain("Credential access key has length 2, should be 32"))
        self.assertIn("internet", errors.explain("dial tcp: lookup host: no such host"))
        self.assertEqual(errors.explain("The server crashed in a way nobody has seen"), "")


if __name__ == "__main__":
    unittest.main()
