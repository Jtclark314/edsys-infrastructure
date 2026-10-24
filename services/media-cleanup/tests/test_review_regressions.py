"""Regression cases found by independent review; disposable fixtures only."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import CleanupError
import test_workflow as workflow_fixtures
import workflow


class ReviewRegressionTests(unittest.TestCase):
    setUp = workflow_fixtures.WorkflowTests.setUp
    tearDown = workflow_fixtures.WorkflowTests.tearDown
    add_item = workflow_fixtures.WorkflowTests.add_item
    release = workflow_fixtures.WorkflowTests.release
    operation_id = workflow_fixtures.WorkflowTests.operation_id

    def test_unique_substring_requires_clarification_and_preserves_batch(self):
        _, sequel = self.add_item("Alpha II", year=2002)
        _, clear = self.add_item("Beta", number=2)
        result = self.deletion.run(["Alpha", "Beta"])
        self.assertEqual("needs_clarification", result["state"])
        self.assertEqual(["Alpha II (2002)"], result["ambiguous"][0]["matches"])
        self.assertEqual([], result["removed"])
        self.assertTrue(sequel.exists())
        self.assertTrue(clear.exists())
        self.assertEqual([], self.backend.calls)

    def test_year_without_title_rejects_before_any_media_write(self):
        _, unrelated = self.add_item("Unrelated", year=2001)
        with self.assertRaisesRegex(CleanupError, "not only a year"):
            self.deletion.run(["(2001)"])
        self.assertTrue(unrelated.exists())
        self.assertEqual([], self.backend.calls)

    def interrupt_after_plex_write(self):
        original = self.backend.delete_plex

        def interrupted(item):
            original(item)
            raise KeyboardInterrupt("Fixture crash after applied Plex deletion")

        with mock.patch.object(self.backend, "delete_plex", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.deletion.run(["Alpha"])
        return self.operation_id()

    def test_resume_retains_and_reports_new_library_arrival(self):
        selected, path = self.add_item()
        operation_id = self.interrupt_after_plex_write()
        arrival = path.parent / "new-arrival.mkv"
        arrival.write_bytes(b"New media outside original manifest")
        result = self.deletion.run(None, operation_id=operation_id)
        self.assertEqual("partial", result["state"])
        self.assertEqual([], result["removed"])
        self.assertTrue(arrival.exists())
        self.assertIn("New library files", result["failed"][0]["reason"])
        self.assertEqual(1, self.backend.calls.count(("delete_arr", selected["ref"])))
        self.assertEqual(1, self.backend.calls.count(("delete_plex", selected["ref"])))

    def test_resume_retains_and_reports_recreated_planned_download_path(self):
        self.add_item()
        folder = self.release()
        operation_id = self.interrupt_after_plex_write()
        replacement = folder / "encode.mkv"
        replacement.write_bytes(b"Replacement downloaded after deletion")
        result = self.deletion.run(None, operation_id=operation_id)
        self.assertEqual("partial", result["state"])
        self.assertEqual([], result["removed"])
        self.assertTrue(replacement.exists())
        self.assertIn("Files appeared after deletion", result["failed"][0]["reason"])

    def test_resume_retains_and_reports_new_file_in_owned_download_folder(self):
        self.add_item()
        folder = self.release()
        operation_id = self.interrupt_after_plex_write()
        arrival = folder / "new-arrival.rar"
        arrival.write_bytes(b"New archive outside original manifest")
        result = self.deletion.run(None, operation_id=operation_id)
        self.assertEqual("partial", result["state"])
        self.assertEqual([], result["removed"])
        self.assertTrue(arrival.exists())
        self.assertIn("New download files", result["failed"][0]["reason"])

    def test_title_containing_its_release_year_removes_owned_downloads(self):
        _, path = self.add_item("1984", year=1984)
        folder = self.release("1984.1984.1080p-GROUP")
        result = self.deletion.run(["1984 (1984)"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(["1984 (1984)"], result["removed"])
        self.assertFalse(path.exists())
        self.assertFalse(folder.exists())

    def test_native_library_deletion_with_stale_view_settles_without_second_write(self):
        selected, path = self.add_item()
        delete_arr = self.backend.delete_arr
        absent = workflow.strictly_absent
        clock = [0.0]
        observations = []

        def native_delete(item):
            delete_arr(item)
            path.unlink()  # The native service applied deletion in the fixture.

        def stale_once(candidate):
            observations.append(str(candidate))
            return False if len(observations) == 1 else absent(candidate)

        def advance_clock(seconds):
            clock[0] += seconds

        with mock.patch.object(self.backend, "delete_arr", side_effect=native_delete), \
                mock.patch.object(self.fs, "unlink", side_effect=CleanupError("Fixture stale FUSE view")) as unlink, \
                mock.patch.object(workflow, "strictly_absent", side_effect=stale_once), \
                mock.patch.object(workflow.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(workflow.time, "sleep", side_effect=advance_clock) as sleep:
            result = self.deletion.run(["Alpha"])

        self.assertEqual("complete", result["state"])
        self.assertEqual(["Alpha (2001)"], result["removed"])
        self.assertFalse(path.exists())
        self.assertEqual(1, unlink.call_count)
        self.assertEqual(1, sleep.call_count)
        self.assertGreaterEqual(len(observations), 3)
        self.assertEqual(1, self.backend.calls.count(("delete_arr", selected["ref"])))
        self.assertEqual(1, self.backend.calls.count(("delete_plex", selected["ref"])))


if __name__ == "__main__":
    unittest.main()
