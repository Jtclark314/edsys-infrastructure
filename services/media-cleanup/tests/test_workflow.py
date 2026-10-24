"""One-call behavior against disposable media and fixture services only."""
from __future__ import annotations

import builtins
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_engine as fixtures
from backend import BackendError
from engine import CleanupError, Engine, FileSystem
from workflow import Deletion
import workflow


class ScopedBackend(fixtures.FakeBackend):
    def __init__(self):
        super().__init__()
        self.catalog_calls = []
        self.fail_arr_before = set()
        self.fail_plex_after = False
        self.owner_conflict = False

    def catalog(self, kind="any"):
        self.catalog_calls.append(kind)
        return [copy.deepcopy(x) for x in self.items if kind == "any" or x["kind"] == kind]

    def active_download_refs(self, kind="any"):
        return {ref for ref in self.queued if kind == "any" or ref.startswith(kind + ":")}

    def check_library_ownership(self, item):
        if self.owner_conflict:
            raise BackendError("Another title now owns the additional library folder")

    def verify(self, items, *, check_catalog=True):
        return super().verify(items)

    def delete_arr(self, item):
        if item["ref"] in self.fail_arr_before:
            self.calls.append(("delete_arr", item["ref"]))
            raise BackendError("Fixture service unavailable before deletion")
        return super().delete_arr(item)

    def delete_plex(self, item):
        result = super().delete_plex(item)
        if self.fail_plex_after:
            raise TimeoutError("Fixture response lost after Plex removal")
        return result


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edsys-media-workflow-test-")
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        for name in ("movies", "tv", "downloads"):
            (self.media / name).mkdir(parents=True)
        self.backend = ScopedBackend()
        self.fs = FileSystem(self.media, require_mount=False)
        self.engine = Engine(self.backend, self.fs, self.root / "state")
        self.deletion = Deletion(self.engine)

    def tearDown(self):
        self.temp.cleanup()

    def add_item(self, *args, **kwargs):
        return fixtures.EngineTests.add_item(self, *args, **kwargs)

    def release(self, name="Alpha.2001.1080p-GROUP", files=None):
        folder = self.media / "downloads" / name
        for relative, data in (files or {"encode.mkv": b"different encoding"}).items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return folder

    def operation_id(self):
        records = list((self.root / "state").glob("*.delete.json"))
        self.assertEqual(1, len(records))
        return json.loads(records[0].read_text())["manifest"]["operation_id"]

    def test_exact_title_deletes_in_one_call_and_reads_movie_catalog_once(self):
        selected, path = self.add_item()
        other, other_path = self.add_item("Beta", number=2)
        result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(["Alpha (2001)"], result["removed"])
        self.assertFalse(path.exists())
        self.assertTrue(other_path.exists())
        self.assertIn(selected["ref"], self.backend.excluded)
        self.assertNotIn(other["ref"], self.backend.excluded)
        self.assertEqual(["movie"], self.backend.catalog_calls)

    def test_remake_ambiguity_leaves_entire_batch_untouched(self):
        _, original = self.add_item("Alpha", year=2001)
        _, remake = self.add_item("Alpha", year=2020, number=2)
        _, clear = self.add_item("Beta", number=3)
        result = self.deletion.run(["Alpha", "Beta"])
        self.assertEqual("needs_clarification", result["state"])
        self.assertEqual(["Alpha (2001)", "Alpha (2020)"], result["ambiguous"][0]["matches"])
        self.assertTrue(all(path.exists() for path in (original, remake, clear)))
        self.assertEqual([], self.backend.calls)

    def test_year_resolves_remake(self):
        _, original = self.add_item("Alpha", year=2001)
        _, remake = self.add_item("Alpha", year=2020, number=2)
        result = self.deletion.run(["Alpha (2020)"])
        self.assertEqual(["Alpha (2020)"], result["removed"])
        self.assertTrue(original.exists())
        self.assertFalse(remake.exists())

    def test_plex_alias_resolves_title_and_associated_download_release(self):
        selected, path = self.add_item("Canonical Title")
        selected["aliases"] = ["Plex Display Title"]
        folder = self.release("Plex.Display.Title.2001.1080p-GROUP")
        other, other_path = self.add_item("Plex Display Title II", number=2)
        result = self.deletion.run(["Plex Display Title (2001)"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(["Canonical Title (2001)"], result["removed"])
        self.assertFalse(path.exists())
        self.assertFalse(folder.exists())
        self.assertTrue(other_path.exists())
        self.assertNotIn(other["ref"], self.backend.excluded)

    def test_alias_collision_requires_clarification_without_deletion(self):
        selected, first = self.add_item("Canonical Title")
        selected["aliases"] = ["Plex Display Title"]
        _, second = self.add_item("Plex Display Title", number=2)
        result = self.deletion.run(["Plex Display Title (2001)"])
        self.assertEqual("needs_clarification", result["state"])
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        self.assertEqual([], self.backend.calls)

    def standalone_movie(self):
        item, path = self.add_item()
        target = self.media / "movies" / "Alpha.2001.mkv"
        path.rename(target)
        path.parent.rmdir()
        item.update(root=str(target), ref="movie:plex:1001", arr_service=None, arr_id=None, standalone_file=True)
        return item, target

    def test_direct_movie_removes_exact_file_and_sidecars_preserving_library(self):
        _, target = self.standalone_movie()
        sidecars = [target.with_suffix(suffix) for suffix in (".nfo", ".jpg", ".en.srt", ".eng.forced.srt")]
        retained = [target.parent / name for name in ("Alpha.2001.Sequel.jpg", "Alpha.2001.Other.Movie.jpg", "Beta.2001.mkv")]
        for path in sidecars + retained:
            path.write_bytes(b"fixture")
        result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(5, result["files_removed"])
        self.assertFalse(any(path.exists() for path in [target, *sidecars]))
        self.assertTrue(all(path.exists() for path in retained))
        self.assertTrue(target.parent.is_dir())
        self.assertFalse(any(call[0] == "delete_arr" for call in self.backend.calls))

    def test_direct_movie_new_sidecar_is_retained_and_reported(self):
        _, target = self.standalone_movie()
        arrived = target.with_suffix(".fr.srt")
        unlink = self.fs.unlink
        def new_sidecar(file):
            unlink(file)
            arrived.write_bytes(b"new arrival")
        with mock.patch.object(self.fs, "unlink", side_effect=new_sidecar):
            result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("partial", result["state"])
        self.assertEqual([], result["removed"])
        self.assertTrue(arrived.exists())
        self.assertFalse(any(call[0] == "delete_plex" for call in self.backend.calls))

    def test_direct_movie_symlink_sidecar_blocks_without_removing_target(self):
        _, target = self.standalone_movie()
        outside = self.root / "outside.srt"
        outside.write_bytes(b"keep")
        target.with_suffix(".srt").symlink_to(outside)
        result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("partial", result["state"])
        self.assertTrue(target.exists())
        self.assertTrue(outside.exists())
        self.assertEqual([], self.backend.calls)

    def test_direct_file_rejects_series_managed_and_library_base_targets(self):
        item, target = self.standalone_movie()
        for changed in ({"kind": "show", "root": str(self.media / "tv" / "Alpha.mkv")},
                        {"arr_service": "radarr"}, {"root": str(target.parent)},
                        {"root": str(target.with_suffix(".txt"))}):
            with self.subTest(changed=changed), self.assertRaises(CleanupError):
                self.fs.validate_item({**item, **changed})
        self.assertTrue(target.exists())

    def test_legacy_plan_direct_file_uses_clear_one_step_error(self):
        item, target = self.standalone_movie()
        with self.assertRaisesRegex(CleanupError, "one-step delete"):
            self.engine.plan([item["ref"]])
        self.assertTrue(target.exists())
        self.assertEqual([], self.backend.calls)

    def duplicate_movie_versions(self):
        item, primary = self.add_item()
        folder = self.media / "movies" / "Alpha.2001.BluRay.REMUX"
        folder.mkdir()
        additional = folder / "alternate.mkv"
        additional.write_bytes(b"different encoding")
        item["additional_roots"] = [str(folder)]
        return item, primary, additional

    def test_confirmed_duplicate_versions_remove_both_folders_with_primary_arr_inventory(self):
        item, primary, additional = self.duplicate_movie_versions()
        _, retained = self.add_item("Beta", number=2)
        result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(2, result["files_removed"])
        self.assertFalse(primary.parent.exists())
        self.assertFalse(additional.parent.exists())
        self.assertTrue(retained.exists())
        record = self.engine.store.read(self.operation_id() + ".delete.json")
        self.assertEqual(["movie.mkv"], [row["path"] for row in record["manifest"]["entries"][0]["item"]["relative_files"]])
        self.assertEqual(1, self.backend.calls.count(("delete_arr", item["ref"])))

    def test_additional_movie_folder_owned_by_another_title_is_retained(self):
        item, primary, additional = self.duplicate_movie_versions()
        _, other = self.add_item("Beta", number=2)
        item["additional_roots"].append(str(other.parent))
        result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("partial", result["state"])
        self.assertTrue(all(path.exists() for path in (primary, additional, other)))
        self.assertEqual([], self.backend.calls)

    def test_playing_additional_version_blocks_the_whole_movie(self):
        _, primary, additional = self.duplicate_movie_versions()
        self.backend.playing = [str(additional)]
        result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("partial", result["state"])
        self.assertTrue(primary.exists())
        self.assertTrue(additional.exists())
        self.assertEqual([], self.backend.calls)

    def test_new_file_in_additional_folder_prevents_completion_and_plex_removal(self):
        _, _, additional = self.duplicate_movie_versions()
        arrived = additional.parent / "late.mkv"
        unlink = self.fs.unlink
        def new_file(file):
            unlink(file)
            arrived.write_bytes(b"new arrival")
        with mock.patch.object(self.fs, "unlink", side_effect=new_file):
            result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("partial", result["state"])
        self.assertTrue(arrived.exists())
        self.assertFalse(any(call[0] == "delete_plex" for call in self.backend.calls))

    def test_new_additional_folder_owner_after_arr_write_blocks_local_cleanup(self):
        _, primary, additional = self.duplicate_movie_versions()
        delete_arr = self.backend.delete_arr
        def claim_folder(item):
            result = delete_arr(item)
            self.backend.owner_conflict = True
            return result
        with mock.patch.object(self.backend, "delete_arr", side_effect=claim_folder):
            result = self.deletion.run(["Alpha (2001)"])
        self.assertEqual("partial", result["state"])
        self.assertTrue(additional.exists())
        self.assertTrue(primary.exists())
        self.assertFalse(any(call[0] == "delete_plex" for call in self.backend.calls))
        resumed = self.deletion.run(None, operation_id=result["operation_id"])
        self.assertEqual("partial", resumed["state"])
        self.assertTrue(additional.exists())
        self.assertEqual(1, sum(call[0] == "delete_arr" for call in self.backend.calls))

    def test_additional_folder_shape_and_legacy_plans_are_bounded(self):
        item, primary, additional = self.duplicate_movie_versions()
        for changed in ({"additional_roots": [str(primary.parent)]},
                        {"additional_roots": [str(self.media / 'movies')]},
                        {"additional_roots": str(additional.parent)},
                        {"arr_service": None}, {"kind": "show"}, {"standalone_file": True}):
            with self.subTest(changed=changed), self.assertRaises(CleanupError):
                self.fs.validate_item({**item, **changed})
        with self.assertRaisesRegex(CleanupError, "one-step delete"):
            self.engine.plan([item["ref"]])
        self.assertTrue(primary.exists())
        self.assertTrue(additional.exists())

    def test_partial_title_never_substitutes_the_only_available_sequel(self):
        _, sequel = self.add_item("Alpha II", year=2002)
        result = self.deletion.run(["Alpha"])
        self.assertEqual("needs_clarification", result["state"])
        self.assertEqual(["Alpha II (2002)"], result["ambiguous"][0]["matches"])
        self.assertEqual([], result["removed"])
        self.assertTrue(sequel.exists())
        self.assertEqual([], self.backend.calls)

    def test_year_without_title_is_rejected_without_deletion(self):
        _, path = self.add_item()
        with self.assertRaises(CleanupError):
            self.deletion.run(["(2001)"])
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_single_title_never_expands_collection(self):
        _, selected = self.add_item(collection=7)
        _, sequel = self.add_item("Alpha II", year=2002, number=2, collection=7)
        result = self.deletion.run(["Alpha"])
        self.assertEqual(["Alpha (2001)"], result["removed"])
        self.assertFalse(selected.exists())
        self.assertTrue(sequel.exists())
        self.assertFalse(any(call[0] == "disable_collections" for call in self.backend.calls))

    def test_explicit_collection_removes_all_owned_titles(self):
        _, first = self.add_item(collection=7)
        _, sequel = self.add_item("Alpha II", year=2002, number=2, collection=7)
        result = self.deletion.run(["Alpha Collection"], include_collections=True)
        self.assertEqual("complete", result["state"])
        self.assertEqual({"Alpha (2001)", "Alpha II (2002)"}, set(result["removed"]))
        self.assertFalse(first.exists())
        self.assertFalse(sequel.exists())
        self.assertIn(("disable_collections", [7]), self.backend.calls)

    def test_missing_title_is_reported_without_a_question(self):
        _, path = self.add_item()
        result = self.deletion.run(["Not In This Library"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(["Not In This Library"], result["not_found"])
        self.assertEqual([], result["removed"])
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_dry_run_never_changes_media_or_backend(self):
        _, path = self.add_item()
        folder = self.release()
        result = self.deletion.run(["Alpha"], dry_run=True)
        self.assertEqual("dry_run", result["state"])
        self.assertFalse(result["deletion_performed"])
        self.assertEqual(1, result["library_files"])
        self.assertEqual(1, result["download_files"])
        self.assertTrue(path.exists())
        self.assertTrue((folder / "encode.mkv").exists())
        self.assertEqual([], self.backend.calls)
        self.assertEqual([], list((self.root / "state").glob("*.delete.json")))

    def test_release_encodes_archives_and_subtitles_removed_without_content_reads(self):
        _, path = self.add_item()
        folder = self.release(files={"encode.mkv": b"new encoding", "archive.part1.rar": b"archive", "Subs/English.srt": b"subtitles"})
        original_open = builtins.open
        def no_media_content(file, mode="r", *args, **kwargs):
            if not isinstance(file, int) and Path(file).is_relative_to(self.media) and "r" in mode:
                self.fail("One-step cleanup read media contents")
            return original_open(file, mode, *args, **kwargs)
        with mock.patch("builtins.open", side_effect=no_media_content), mock.patch.object(self.fs, "copies", side_effect=AssertionError("Legacy full-file matching called")):
            result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertEqual(4, result["files_removed"])
        self.assertFalse(path.exists())
        self.assertFalse(folder.exists())

    def test_unreadable_unrelated_download_does_not_block_title(self):
        _, path = self.add_item()
        blocked = self.release("Unknown Folder")
        original_scandir = os.scandir
        def scandir(folder):
            if Path(folder) == blocked:
                raise PermissionError("Fixture unrelated unreadable directory")
            return original_scandir(folder)
        with mock.patch.object(workflow.os, "scandir", side_effect=scandir):
            result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertFalse(path.exists())
        self.assertTrue((blocked / "encode.mkv").exists())

    def test_unselected_and_mixed_title_downloads_are_retained(self):
        _, path = self.add_item()
        _, other_path = self.add_item("Beta", year=2002, number=2)
        other = self.release("Beta.2002.1080p-GROUP")
        mixed = self.release(files={"Alpha.2001.mkv": b"selected", "Beta.2002.mkv": b"other film"})
        result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertFalse(path.exists())
        self.assertTrue(other_path.exists())
        self.assertTrue((other / "encode.mkv").exists())
        self.assertTrue((mixed / "Alpha.2001.mkv").exists())
        self.assertTrue((mixed / "Beta.2002.mkv").exists())
        self.assertTrue(result["warnings"])

    def test_playing_and_queued_titles_are_skipped(self):
        first, playing = self.add_item()
        second, queued = self.add_item("Beta", number=2)
        self.backend.playing = [str(playing)]
        self.backend.queued = {second["ref"]}
        result = self.deletion.run(["Alpha", "Beta"])
        self.assertEqual("partial", result["state"])
        self.assertEqual(2, len(result["skipped"]))
        self.assertTrue(playing.exists())
        self.assertTrue(queued.exists())
        self.assertEqual([], self.backend.calls)

    def test_one_title_failure_does_not_block_independent_title(self):
        broken, broken_path = self.add_item()
        _, good_path = self.add_item("Beta", number=2)
        self.backend.fail_arr_before.add(broken["ref"])
        result = self.deletion.run(["Alpha", "Beta"])
        self.assertEqual("partial", result["state"])
        self.assertEqual(["Beta (2001)"], result["removed"])
        self.assertEqual("Alpha (2001)", result["failed"][0]["title"])
        self.assertTrue(broken_path.exists())
        self.assertFalse(good_path.exists())

    def test_lost_arr_response_after_apply_finishes_without_resending_delete(self):
        selected, path = self.add_item()
        self.backend.fail_arr_after_apply = True
        result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertFalse(path.exists())
        self.assertEqual(1, self.backend.calls.count(("delete_arr", selected["ref"])))

    def test_lost_plex_response_after_apply_finishes_without_resending_delete(self):
        selected, path = self.add_item()
        self.backend.fail_plex_after = True
        result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertFalse(path.exists())
        self.assertEqual(1, self.backend.calls.count(("delete_plex", selected["ref"])))

    def test_interrupted_applied_arr_delete_is_resumed_without_resend(self):
        selected, path = self.add_item()
        delete = self.backend.delete_arr
        def interrupt_after_apply(item):
            delete(item)
            raise KeyboardInterrupt("Fixture process interruption")
        with mock.patch.object(self.backend, "delete_arr", side_effect=interrupt_after_apply), self.assertRaises(KeyboardInterrupt):
            self.deletion.run(["Alpha"])
        result = self.deletion.run(None, operation_id=self.operation_id())
        self.assertEqual("complete", result["state"])
        self.assertFalse(path.exists())
        self.assertEqual(1, self.backend.calls.count(("delete_arr", selected["ref"])))

    def test_interrupted_unconfirmed_arr_delete_is_never_resubmitted(self):
        selected, path = self.add_item()
        def interrupt_before_apply(item):
            self.backend.calls.append(("delete_arr", item["ref"]))
            raise KeyboardInterrupt("Fixture response outcome unavailable")
        with mock.patch.object(self.backend, "delete_arr", side_effect=interrupt_before_apply), self.assertRaises(KeyboardInterrupt):
            self.deletion.run(["Alpha"])
        result = self.deletion.run(None, operation_id=self.operation_id())
        self.assertEqual("partial", result["state"])
        self.assertEqual([], result["removed"])
        self.assertTrue(path.exists())
        self.assertEqual(1, self.backend.calls.count(("delete_arr", selected["ref"])))

    def test_resuming_does_not_delete_a_replaced_download_file(self):
        selected, _ = self.add_item()
        folder = self.release()
        download = folder / "encode.mkv"
        delete = self.backend.delete_arr
        def interrupt_after_apply(item):
            delete(item)
            raise KeyboardInterrupt("Fixture process interruption")
        with mock.patch.object(self.backend, "delete_arr", side_effect=interrupt_after_apply), self.assertRaises(KeyboardInterrupt):
            self.deletion.run(["Alpha"])
        download.unlink()
        download.write_bytes(b"Replacement download must survive")
        result = self.deletion.run(None, operation_id=self.operation_id())
        self.assertEqual("partial", result["state"])
        self.assertTrue(download.exists())
        self.assertEqual(b"Replacement download must survive", download.read_bytes())
        self.assertEqual(1, self.backend.calls.count(("delete_arr", selected["ref"])))

    def test_unknown_external_hardlink_survives_selected_title_deletion(self):
        _, path = self.add_item()
        retained = self.media / "downloads" / "Unknown Filename.mkv"
        os.link(path, retained)
        result = self.deletion.run(["Alpha"])
        self.assertEqual("complete", result["state"])
        self.assertFalse(path.exists())
        self.assertTrue(retained.exists())

    def test_completed_operation_returns_recorded_result_without_service_writes(self):
        self.add_item()
        original = self.deletion.run(["Alpha"])
        calls = list(self.backend.calls)
        catalogs = list(self.backend.catalog_calls)
        repeated = self.deletion.run(None, operation_id=original["operation_id"])
        self.assertEqual(original, repeated)
        self.assertEqual(calls, self.backend.calls)
        self.assertEqual(catalogs, self.backend.catalog_calls)

    def test_new_library_arrival_is_left_in_place_and_prevents_plex_deletion(self):
        selected, path = self.add_item()
        new_file = path.parent / "new-arrival.mkv"
        delete = self.backend.delete_arr
        def arrive_after_dispatch(item):
            delete(item)
            new_file.write_bytes(b"Not in the deletion manifest")
        with mock.patch.object(self.backend, "delete_arr", side_effect=arrive_after_dispatch):
            result = self.deletion.run(["Alpha"])
        self.assertEqual("partial", result["state"])
        self.assertFalse(path.exists())
        self.assertTrue(new_file.exists())
        self.assertNotIn(("delete_plex", selected["ref"]), self.backend.calls)


if __name__ == "__main__":
    unittest.main()
