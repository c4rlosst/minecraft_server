"""After the server stops, the local backup and the upload run together, and one failing never stops the other."""
import asyncio
import time
import unittest

from app.hosting import run_after_stop
from app.shared import HostingError


class FakeHosting:
    def __init__(self, seconds=0.0, error=None):
        self.seconds, self.error, self.pushed = seconds, error, 0

    async def push_after_stop(self):
        await asyncio.sleep(self.seconds)
        if self.error:
            raise self.error
        self.pushed += 1


def slow_backup(seconds, record, fail=False):
    async def backup():
        await asyncio.sleep(seconds)
        record.append("backup")
        if fail:
            raise OSError("disk full")
    return backup


class AfterStopTests(unittest.TestCase):
    def test_the_backup_and_the_upload_overlap_instead_of_queueing(self):
        record, hosting = [], FakeHosting(seconds=0.4)
        started = time.monotonic()
        asyncio.run(run_after_stop(slow_backup(0.4, record), hosting))
        took = time.monotonic() - started
        self.assertEqual((record, hosting.pushed), (["backup"], 1))
        self.assertLess(took, 0.7)                                       # not 0.8: they ran together

    def test_a_failed_upload_is_logged_and_the_backup_still_happens(self):
        record = []
        with self.assertLogs("hosting", level="ERROR") as logs:
            asyncio.run(run_after_stop(slow_backup(0.05, record), FakeHosting(error=HostingError("storage is down"))))
        self.assertEqual(record, ["backup"])
        self.assertIn("NOT uploaded", "\n".join(logs.output))

    def test_a_failed_backup_does_not_stop_the_upload(self):
        record, hosting = [], FakeHosting()
        with self.assertLogs("hosting", level="ERROR") as logs:
            asyncio.run(run_after_stop(slow_backup(0.05, record, fail=True), hosting))
        self.assertEqual(hosting.pushed, 1)
        self.assertIn("after-stop task failed", "\n".join(logs.output))

    def test_without_shared_hosting_or_without_a_backup_folder_each_half_still_works(self):
        record = []
        asyncio.run(run_after_stop(slow_backup(0.01, record), None))
        self.assertEqual(record, ["backup"])
        hosting = FakeHosting()
        asyncio.run(run_after_stop(None, hosting))
        self.assertEqual(hosting.pushed, 1)


if __name__ == "__main__":
    unittest.main()
