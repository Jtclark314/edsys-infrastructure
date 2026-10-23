"""Isolated behavior checks: no real services, credentials, or media are used."""
from __future__ import annotations

import copy
import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import CleanupError, Engine, FileSystem  # noqa: E402
import engine as engine_module  # noqa: E402


class FakeBackend:
    def __init__(self):
        self.items = []
        self.playing = []
        self.queued = set()
        self.calls = []
        self.preflight_calls = 0
        self.excluded = set()
        self.plex_present = set()
        self.fail_arr_after_apply = False
        self.fail_plex = False
        self.verify_ok = True

    def catalog(self):
        return copy.deepcopy(self.items)

    def active_paths(self):
        return list(self.playing)

    def active_download_refs(self):
        return set(self.queued)

    def preflight(self, items):
        self.preflight_calls += 1

    def delete_arr(self, item):
        self.calls.append(("delete_arr", item["ref"]))
        self.items = [x for x in self.items if x["ref"] != item["ref"]]
        self.excluded.add(item["ref"])
        if self.fail_arr_after_apply:
            raise TimeoutError("Simulated lost response after the write was applied")

    def disable_collections(self, ids):
        self.calls.append(("disable_collections", list(ids)))

    def delete_plex(self, item):
        self.calls.append(("delete_plex", item["ref"]))
        if self.fail_plex:
            raise TimeoutError("Simulated uncertain Plex write")
        self.plex_present.difference_update(item["plex_keys"])

    def verify(self, items):
        remaining = {x["ref"] for x in self.items}
        return {
            "arr_removed": self.verify_ok and all(
                not x.get("arr_service") or x["ref"] not in remaining for x in items
            ),
            "plex_removed": self.verify_ok and all(
                not set(x["plex_keys"]).intersection(self.plex_present) for x in items
            ),
            "exclusions_verified": self.verify_ok and all(
                not x.get("arr_service") or x["ref"] in self.excluded for x in items
            ),
        }


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edsys-media-engine-test-")
        self.root = Path(self.temp.name)
        self.media = self.root / "media"
        for name in ["movies", "tv", "downloads"]:
            (self.media / name).mkdir(parents=True)
        self.backend = FakeBackend()
        self.now = [100000.0]
        self.fs = FileSystem(self.media, require_mount=False)
        self.engine = Engine(
            self.backend, self.fs, self.root / "state", clock=lambda: self.now[0]
        )

    def tearDown(self):
        self.temp.cleanup()

    def add_item(self, title="Alpha", year=2001, kind="movie", number=1, data=b"media" * 2000,
                 collection=None, make_file=True):
        service = "radarr" if kind == "movie" else "sonarr"
        folder = self.media / ("movies" if kind == "movie" else "tv") / f"{title} ({year})"
        item = {
            "ref": f"{kind}:{service}:{number}", "kind": kind, "title": title,
            "year": year, "root": str(folder), "arr_service": service,
            "arr_id": number, "external_id": number + 100,
            "plex_keys": [str(1000 + number)], "collection_id": collection,
            "collection_title": "Alpha Collection" if collection else None,
            "has_files": make_file, "estimated_bytes": len(data) if make_file else 0,
        }
        self.backend.items.append(item)
        self.backend.plex_present.update(item["plex_keys"])
        path = folder / ("Season 01/Alpha.S01E01.mkv" if kind == "show" else "movie.mkv")
        if make_file:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return item, path

    def plan(self, item, **options):
        return self.engine.plan([item["ref"]], **options)

    def execute(self, preview, **options):
        return self.engine.execute(preview["plan_id"], preview["selection_hash"],
                                   options.get("user_requested_delete", True))

    def test_plan_does_not_remove_media_or_write_to_backend(self):
        item, path = self.add_item()
        preview = self.plan(item)
        self.assertTrue(preview["ready"])
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)
        self.assertEqual("planned", self.engine.status(preview["plan_id"])["state"])
        self.assertEqual(0o700, (self.root / "state").stat().st_mode & 0o777)
        for child in (self.root / "state").iterdir():
            self.assertEqual(0o600, child.stat().st_mode & 0o777)

    def test_exact_selected_movie_deleted_and_unrelated_movie_preserved(self):
        item, selected = self.add_item()
        other, unrelated = self.add_item("Beta", number=2, data=b"other" * 3000)
        preview = self.plan(item)
        result = self.execute(preview)
        self.assertEqual("complete", result["state"])
        self.assertFalse(selected.exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual([other["ref"]], [x["ref"] for x in self.backend.items])
        self.assertIn(item["ref"], self.backend.excluded)
        self.assertNotIn(other["ref"], self.backend.excluded)
        self.assertEqual(1, result["titles_removed"])

    def test_hash_identical_renamed_download_copy_and_owned_sidecars_removed(self):
        item, source = self.add_item()
        folder = self.media / "downloads" / "Alpha.2001.1080p-GROUP"
        folder.mkdir()
        renamed = folder / "totally-renamed.mkv"
        renamed.write_bytes(source.read_bytes())
        sidecar = folder / "Subs" / "English.srt"
        sidecar.parent.mkdir()
        sidecar.write_text("Subtitle text")
        nfo = folder / "release.nfo"
        nfo.write_text("Release metadata")
        unrelated = self.media / "downloads" / "Beta.2001.mkv"
        unrelated.write_bytes(b"other content")
        preview = self.plan(item)
        self.assertEqual(3, preview["download_copy_and_sidecar_count"])
        self.assertEqual("complete", self.execute(preview)["state"])
        self.assertFalse(renamed.exists())
        self.assertFalse(sidecar.exists())
        self.assertFalse(nfo.exists())
        self.assertTrue(unrelated.exists())

    def test_sidecars_retained_if_matched_download_folder_contains_other_video(self):
        item, source = self.add_item()
        folder = self.media / "downloads" / "Alpha (2001)"
        folder.mkdir()
        duplicate = folder / "duplicate.mkv"
        duplicate.write_bytes(source.read_bytes())
        extra = folder / "different-movie.mp4"
        extra.write_bytes(b"not the selected film")
        sidecar = folder / "English.srt"
        sidecar.write_text("Could belong to the other video")
        preview = self.plan(item)
        self.assertEqual(1, preview["download_copy_and_sidecar_count"])
        self.assertTrue(preview["warnings"])
        self.assertEqual("complete", self.execute(preview)["state"])
        self.assertTrue(extra.exists())
        self.assertTrue(sidecar.exists())
        self.assertFalse(duplicate.exists())

    def test_same_title_staging_with_different_content_is_retained(self):
        item, source = self.add_item()
        folder = self.media / "downloads" / "Alpha (2001)"
        folder.mkdir()
        other = folder / "movie.mkv"
        other.write_bytes(b"x" * source.stat().st_size)
        preview = self.plan(item)
        self.assertEqual(0, preview["download_copy_and_sidecar_count"])
        self.assertEqual("complete", self.execute(preview)["state"])
        self.assertTrue(other.exists())

    def test_identical_staging_owned_by_unselected_title_is_preserved(self):
        item, source = self.add_item(title="Alpha", year=2001)
        beta, beta_source = self.add_item(
            title="Beta", year=2002, number=2, data=source.read_bytes()
        )
        folder = self.media / "downloads" / "Beta.2002.1080p-GROUP"
        folder.mkdir()
        renamed = folder / "renamed-identical-video.mkv"
        renamed.write_bytes(source.read_bytes())
        hardlink = folder / "renamed-identical-hardlink.mkv"
        os.link(source, hardlink)

        preview = self.plan(item)
        self.assertEqual(0, preview["download_copy_and_sidecar_count"])
        self.assertGreater(preview["retained_external_hardlink_bytes"], 0)
        result = self.execute(preview)
        self.assertEqual("complete", result["state"])
        self.assertFalse(source.exists())
        self.assertTrue(beta_source.exists())
        self.assertTrue(renamed.exists())
        self.assertTrue(hardlink.exists())
        self.assertIn(beta["ref"], [x["ref"] for x in self.backend.items])

    def test_new_staging_owner_after_preview_rejects_before_any_write(self):
        item, source = self.add_item(title="Alpha", year=2001)
        folder = self.media / "downloads" / "Beta.2002.1080p-GROUP"
        folder.mkdir()
        duplicate = folder / source.name
        duplicate.write_bytes(source.read_bytes())
        preview = self.plan(item)
        self.assertEqual(1, preview["download_copy_and_sidecar_count"])
        beta, beta_source = self.add_item(title="Beta", year=2002, number=2, data=b"other")
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(source.exists())
        self.assertTrue(duplicate.exists())
        self.assertTrue(beta_source.exists())
        self.assertEqual([], self.backend.calls)

    def test_new_sidecar_owner_after_preview_rejects_before_any_write(self):
        item, source = self.add_item(title="Alpha", year=2001)
        folder = self.media / "downloads" / "Alpha.2001.1080p-GROUP"
        folder.mkdir()
        duplicate = folder / "renamed-video.mkv"
        duplicate.write_bytes(source.read_bytes())
        foreign_folder = folder / "Beta.2002"
        foreign_folder.mkdir()
        sidecar = foreign_folder / "English.srt"
        sidecar.write_text("Subtitle with a different catalog owner")
        preview = self.plan(item)
        self.assertEqual(2, preview["download_copy_and_sidecar_count"])
        self.add_item(title="Beta", year=2002, number=2, make_file=False)
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(source.exists())
        self.assertTrue(duplicate.exists())
        self.assertTrue(sidecar.exists())
        self.assertEqual([], self.backend.calls)

    def test_already_known_unselected_sidecar_is_retained_at_discovery(self):
        item, source = self.add_item(title="Alpha", year=2001)
        self.add_item(title="Beta", year=2002, number=2, make_file=False)
        folder = self.media / "downloads" / "Alpha.2001.1080p-GROUP"
        folder.mkdir()
        duplicate = folder / "renamed-video.mkv"
        duplicate.write_bytes(source.read_bytes())
        foreign_folder = folder / "Beta.2002"
        foreign_folder.mkdir()
        sidecar = foreign_folder / "English.srt"
        sidecar.write_text("Subtitle belonging to the unselected Beta title")

        preview = self.plan(item)
        self.assertTrue(preview["ready"])
        self.assertEqual(1, preview["download_copy_and_sidecar_count"])
        self.assertEqual("complete", self.execute(preview)["state"])
        self.assertFalse(source.exists())
        self.assertFalse(duplicate.exists())
        self.assertTrue(sidecar.exists())

    def test_unreadable_known_unselected_staging_directory_is_pruned_before_entry(self):
        item, source = self.add_item(title="Alpha", year=2001)
        self.add_item(title="Harbor Watch", year=2011, kind="show", number=2,
                      make_file=False)
        blocked = (self.media / "downloads" / "usenet" / "complete" / "complete" / "tv"
                   / "Harbor.Watch.S04.1080p.Bluray.DTS-HD.x265-GROUP")
        blocked.mkdir(parents=True)
        retained = blocked / "unrelated.rar"
        retained.write_bytes(b"Existing unrelated archive")
        selected_folder = self.media / "downloads" / "Alpha.2001.1080p-GROUP"
        selected_folder.mkdir()
        selected_copy = selected_folder / source.name
        selected_copy.write_bytes(source.read_bytes())
        original_scandir = os.scandir
        attempts = []

        def deny_unselected(path):
            attempts.append(Path(path))
            if Path(path) == blocked:
                raise PermissionError(13, "Simulated unreadable unrelated directory", str(path))
            return original_scandir(path)

        with mock.patch.object(engine_module.os, "scandir", side_effect=deny_unselected):
            preview = self.plan(item)
            self.assertTrue(preview["ready"])
            self.assertEqual(1, preview["download_copy_and_sidecar_count"])
            self.assertEqual("complete", self.execute(preview)["state"])
        self.assertNotIn(blocked, attempts, "Known unselected staging must be pruned before enumeration")
        self.assertTrue(retained.exists())
        self.assertEqual(b"Existing unrelated archive", retained.read_bytes())
        self.assertFalse(source.exists())
        self.assertFalse(selected_copy.exists())

    def test_unreadable_unknown_staging_directory_still_blocks(self):
        item, source = self.add_item()
        blocked = self.media / "downloads" / "Unknown.2002"
        blocked.mkdir()
        (blocked / "unknown.rar").write_bytes(b"Unknown ownership")
        original_scandir = os.scandir
        attempted = []

        def deny_unknown(path):
            if Path(path) == blocked:
                attempted.append(Path(path))
                raise PermissionError(13, "Simulated unreadable unknown directory", str(path))
            return original_scandir(path)

        with mock.patch.object(engine_module.os, "scandir", side_effect=deny_unknown):
            with self.assertRaises(CleanupError):
                self.plan(item)
        self.assertEqual([blocked], attempted)
        self.assertTrue(source.exists())
        self.assertEqual([], self.backend.calls)

    def test_unreadable_selected_staging_directory_still_blocks(self):
        item, source = self.add_item(title="Alpha", year=2001)
        blocked = self.media / "downloads" / "Alpha.2001.1080p-GROUP"
        blocked.mkdir()
        (blocked / "selected.rar").write_bytes(b"Selected ownership")
        original_scandir = os.scandir
        attempted = []

        def deny_selected(path):
            if Path(path) == blocked:
                attempted.append(Path(path))
                raise PermissionError(13, "Simulated unreadable selected directory", str(path))
            return original_scandir(path)

        with mock.patch.object(engine_module.os, "scandir", side_effect=deny_selected):
            with self.assertRaises(CleanupError):
                self.plan(item)
        self.assertEqual([blocked], attempted)
        self.assertTrue(source.exists())
        self.assertEqual([], self.backend.calls)

    def test_library_walk_stays_strict_despite_unselected_named_subdirectory(self):
        item, source = self.add_item(title="Alpha", year=2001)
        self.add_item(title="Beta", year=2002, number=2, make_file=False)
        blocked = Path(item["root"]) / "Beta.2002"
        blocked.mkdir()
        (blocked / "unknown.rar").write_bytes(b"Inside selected library root")
        original_scandir = os.scandir

        def deny_library(path):
            if Path(path) == blocked:
                raise PermissionError(13, "Simulated unreadable library directory", str(path))
            return original_scandir(path)

        with mock.patch.object(engine_module.os, "scandir", side_effect=deny_library):
            with self.assertRaises(CleanupError):
                self.plan(item)
        self.assertTrue(source.exists())
        self.assertEqual([], self.backend.calls)

    def test_known_unselected_staging_symlink_is_not_hidden_by_pruning(self):
        item, source = self.add_item()
        self.add_item(title="Beta", year=2002, number=2, make_file=False)
        outside = self.root / "outside-staging"
        outside.mkdir()
        (outside / "keep.mkv").write_bytes(b"Unselected outside media")
        link = self.media / "downloads" / "Beta.2002"
        link.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(CleanupError):
            self.plan(item)
        self.assertTrue(source.exists())
        self.assertTrue((outside / "keep.mkv").exists())
        self.assertEqual([], self.backend.calls)

    def test_external_hardlink_is_retained_and_not_counted_as_reclaimable(self):
        item, source = self.add_item()
        outside = self.root / "keep-this-link.mkv"
        os.link(source, outside)
        blocks = source.stat().st_blocks * 512
        preview = self.plan(item)
        self.assertEqual(0, preview["expected_reclaimed_bytes"])
        self.assertEqual(blocks, preview["retained_external_hardlink_bytes"])
        result = self.execute(preview)
        self.assertEqual("complete", result["state"])
        self.assertTrue(outside.exists())
        self.assertEqual(0, result["expected_reclaimed_bytes"])

    def test_staging_hardlink_counted_once_and_both_names_removed(self):
        item, source = self.add_item()
        duplicate = self.media / "downloads" / "renamed.mkv"
        os.link(source, duplicate)
        allocation = source.stat().st_blocks * 512
        preview = self.plan(item)
        self.assertEqual(allocation, preview["expected_reclaimed_bytes"])
        result = self.execute(preview)
        self.assertEqual(allocation, result["allocated_bytes_unlinked"])
        self.assertFalse(source.exists())
        self.assertFalse(duplicate.exists())

    def test_copy_cleanup_can_be_omitted(self):
        item, source = self.add_item()
        duplicate = self.media / "downloads" / "copy.mkv"
        duplicate.write_bytes(source.read_bytes())
        preview = self.plan(item, include_download_copies=False)
        self.assertEqual(0, preview["download_copy_and_sidecar_count"])
        self.assertEqual("complete", self.execute(preview)["state"])
        self.assertTrue(duplicate.exists())

    def test_active_episode_blocks_whole_series_deletion(self):
        item, episode = self.add_item(kind="show")
        self.backend.playing = [str(episode)]
        preview = self.plan(item)
        self.assertFalse(preview["ready"])
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(episode.exists())
        self.assertEqual([], self.backend.calls)

    def test_playback_started_after_preview_blocks_execution(self):
        item, path = self.add_item()
        preview = self.plan(item)
        self.backend.playing = [str(path)]
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_active_download_blocks_execution(self):
        item, path = self.add_item()
        self.backend.queued.add(item["ref"])
        preview = self.plan(item)
        self.assertFalse(preview["ready"])
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_new_episode_after_preview_rejects_before_any_write(self):
        item, episode = self.add_item(kind="show")
        preview = self.plan(item)
        second = episode.with_name("Alpha.S01E02.mkv")
        second.write_bytes(b"new episode")
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(episode.exists())
        self.assertTrue(second.exists())
        self.assertEqual([], self.backend.calls)

    def test_selected_file_replaced_after_preview_rejects_before_any_write(self):
        item, path = self.add_item()
        preview = self.plan(item)
        replacement = path.with_suffix(".replacement")
        replacement.write_bytes(b"different inode")
        os.replace(replacement, path)
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertEqual(b"different inode", path.read_bytes())
        self.assertEqual([], self.backend.calls)

    def test_library_root_symlink_rejected(self):
        item, source = self.add_item()
        root = Path(item["root"])
        saved = self.root / "saved"
        root.rename(saved)
        root.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(CleanupError):
            self.plan(item)
        self.assertTrue((saved / source.name).exists())
        self.assertEqual([], self.backend.calls)

    def test_parent_symlink_swap_after_preview_rejected(self):
        item, path = self.add_item()
        preview = self.plan(item)
        original = Path(item["root"])
        saved = self.root / "saved"
        original.rename(saved)
        original.symlink_to(saved, target_is_directory=True)
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue((saved / path.name).exists())
        self.assertEqual([], self.backend.calls)

    def test_directory_outside_library_and_library_root_itself_rejected(self):
        item, source = self.add_item()
        for bad in [self.root / "outside", self.media / "movies"]:
            with self.subTest(path=bad):
                self.backend.items[0]["root"] = str(bad)
                with self.assertRaises(CleanupError):
                    self.plan(item)
        self.assertTrue(source.exists())
        self.assertEqual([], self.backend.calls)

    def test_shared_root_with_unselected_record_rejected_at_plan(self):
        item, path = self.add_item()
        other = copy.deepcopy(item)
        other["ref"], other["arr_id"] = "movie:radarr:2", 2
        self.backend.items.append(other)
        with self.assertRaises(CleanupError):
            self.plan(item)
        self.assertTrue(path.exists())

    def test_new_unselected_overlapping_record_after_preview_rejects_execution(self):
        item, path = self.add_item()
        preview = self.plan(item)
        other = copy.deepcopy(item)
        other["ref"], other["arr_id"] = "movie:radarr:2", 2
        self.backend.items.append(other)
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_ancestor_symlink_race_cannot_unlink_unselected_outside_hardlink(self):
        item, old = self.add_item()
        nested = old.parent / "sub" / "movie.mkv"
        nested.parent.mkdir()
        old.rename(nested)
        outside = self.root / "outside" / "sub"
        outside.mkdir(parents=True)
        outside_file = outside / "movie.mkv"
        os.link(nested, outside_file)
        record = self.fs.file(nested, "library")
        original_open = os.open
        swap_done = [False]

        def race_open(path, flags, *args, **kwargs):
            final_absolute_parent = Path(path) == nested.parent
            final_relative_component = Path(path) == Path("sub") and kwargs.get("dir_fd") is not None
            if not swap_done[0] and (final_absolute_parent or final_relative_component):
                swap_done[0] = True
                Path(item["root"]).rename(self.root / "saved-selected")
                Path(item["root"]).symlink_to(outside.parent, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(engine_module.os, "open", side_effect=race_open):
            try:
                self.fs.unlink(record)
            except CleanupError:
                pass
        self.assertTrue(swap_done[0], "The ancestor swap must execute for this race test to be meaningful")
        self.assertTrue(outside_file.exists(), "An ancestor symlink swap deleted an unselected outside link")

    def test_expired_plan_rejected_and_status_exposes_expiry(self):
        item, path = self.add_item()
        preview = self.plan(item)
        self.now[0] += 1801
        self.assertTrue(self.engine.status(preview["plan_id"])["expired"])
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_modified_signed_plan_rejected(self):
        item, path = self.add_item()
        preview = self.plan(item)
        plan_file = self.root / "state" / (preview["plan_id"] + ".plan.json")
        value = json.loads(plan_file.read_text())
        value["items"][0]["root"] = str(self.root)
        plan_file.write_text(json.dumps(value))
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_wrong_selection_hash_and_nonboolean_authorization_rejected(self):
        item, path = self.add_item()
        preview = self.plan(item)
        for value in [False, 1, "true", None]:
            with self.subTest(value=value), self.assertRaises(CleanupError):
                self.execute(preview, user_requested_delete=value)
        with self.assertRaises(CleanupError):
            self.engine.execute(preview["plan_id"], "0" * 64, True)
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def test_uncertain_write_is_not_repeated(self):
        item, source = self.add_item()
        preview = self.plan(item)
        self.backend.fail_arr_after_apply = True
        result = self.execute(preview)
        self.assertEqual("uncertain", result["state"])
        self.assertIn(item["ref"], self.backend.excluded)
        calls = list(self.backend.calls)
        self.assertTrue(source.exists())
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertEqual(calls, self.backend.calls)
        self.assertEqual("uncertain", self.engine.status(preview["plan_id"])["state"])

    def test_failed_final_verification_is_partial_and_not_repeated(self):
        item, path = self.add_item()
        preview = self.plan(item)
        self.backend.verify_ok = False
        result = self.execute(preview)
        self.assertEqual("partial", result["state"])
        self.assertFalse(path.exists())
        calls = list(self.backend.calls)
        with self.assertRaises(CleanupError):
            self.execute(preview)
        self.assertEqual(calls, self.backend.calls)

    def test_successful_execute_repeat_returns_result_without_new_writes(self):
        item, path = self.add_item()
        preview = self.plan(item)
        first = self.execute(preview)
        calls = list(self.backend.calls)
        self.now[0] += 1900
        second = self.execute(preview)
        self.assertEqual(first, second)
        self.assertEqual(calls, self.backend.calls)
        self.assertFalse(path.exists())

    def test_explicit_collection_expansion_only_adds_that_collection(self):
        item, source = self.add_item(collection=7)
        second, second_path = self.add_item("Second", number=2, collection=7)
        unrelated, other_path = self.add_item("Other", number=3, collection=8)
        preview = self.plan(item, include_collections=True)
        self.assertEqual(2, preview["title_count"])
        result = self.execute(preview)
        self.assertEqual("complete", result["state"])
        self.assertIn(("disable_collections", [7]), self.backend.calls)
        self.assertFalse(source.exists())
        self.assertFalse(second_path.exists())
        self.assertTrue(other_path.exists())
        self.assertIn(unrelated["ref"], [x["ref"] for x in self.backend.items])

    def test_missing_tracked_movie_is_removed_and_excluded_without_freed_bytes(self):
        item, source = self.add_item(make_file=False)
        preview = self.plan(item)
        self.assertEqual(0, preview["library_file_count"])
        self.assertEqual(0, preview["expected_reclaimed_bytes"])
        result = self.execute(preview)
        self.assertEqual("complete", result["state"])
        self.assertIn(item["ref"], self.backend.excluded)

    def test_find_preserves_remake_and_movie_show_ambiguity(self):
        one, _ = self.add_item(title="Alpha", year=2001)
        two, _ = self.add_item(title="Alpha", year=2020, number=2)
        show, _ = self.add_item(title="Alpha", year=2020, kind="show", number=3)
        result = self.engine.find(["Alpha"])
        self.assertEqual(3, result["results"][0]["matching_count"])
        self.assertTrue(result["selection_required"])
        result = self.engine.find(["Alpha (2020)"], kind="movie")
        self.assertEqual([two["ref"]], [x["ref"] for x in result["results"][0]["matches"]])

    def test_find_pagination_reports_all_matches_without_duplicates(self):
        expected = set()
        for number in range(1, 14):
            item, _ = self.add_item(
                title=f"Alpha Chapter {number:02}", year=2000 + number,
                number=number, make_file=False,
            )
            expected.add(item["ref"])
        first = self.engine.find(["Alpha"], kind="movie", offset=0)["results"][0]
        self.assertEqual(13, first["matching_count"])
        self.assertEqual(10, len(first["matches"]))
        self.assertTrue(first["truncated"])
        self.assertEqual(10, first["next_offset"])
        second = self.engine.find(["Alpha"], kind="movie", offset=10)["results"][0]
        self.assertEqual(13, second["matching_count"])
        self.assertEqual(3, len(second["matches"]))
        self.assertFalse(second["truncated"])
        self.assertIsNone(second["next_offset"])
        first_refs = {x["ref"] for x in first["matches"]}
        second_refs = {x["ref"] for x in second["matches"]}
        self.assertFalse(first_refs.intersection(second_refs))
        self.assertEqual(expected, first_refs | second_refs)
        self.assertEqual([], self.backend.calls)
        self.assertFalse(list(self.engine.store.root.glob("*.plan.json")))

    def test_find_rejects_invalid_pagination_offsets(self):
        self.add_item()
        for offset in [-1, True, False, 1.0, 0.0, 10**12, "10", None]:
            with self.subTest(offset=offset), self.assertRaises(CleanupError):
                self.engine.find(["Alpha"], offset=offset)
        self.assertEqual([], self.backend.calls)

    def test_invalid_references_and_option_booleans_rejected(self):
        item, path = self.add_item()
        for refs in [[], [True], ["../movie:radarr:1"], ["movie:radarr:0"],
                     [item["ref"], item["ref"]], ["movie:radarr:999"]]:
            with self.subTest(refs=refs), self.assertRaises(CleanupError):
                self.engine.plan(refs)
        for options in [{"include_collections": 1}, {"include_download_copies": "true"}]:
            with self.subTest(options=options), self.assertRaises(CleanupError):
                self.plan(item, **options)
        with self.assertRaises(CleanupError):
            self.engine.status("../outside")
        self.assertTrue(path.exists())
        self.assertEqual([], self.backend.calls)

    def interrupted_copy_removal(self, *, sidecar=False):
        """ARR and its Plex integration finished, but manifest unlink stopped."""
        item, source = self.add_item()
        folder = self.media / "downloads" / "Alpha.2001.1080p-GROUP"
        folder.mkdir()
        duplicate = folder / "renamed.mkv"
        duplicate.write_bytes(source.read_bytes())
        if sidecar:
            (folder / "English.srt").write_text("subtitles")
        preview = self.plan(item)
        native_delete = self.backend.delete_arr
        def remove_library(record):
            native_delete(record)
            source.unlink()
            self.backend.plex_present.difference_update(record["plex_keys"])
        normal_unlink = self.fs.unlink
        def stop_before_copy(f):
            if f["role"] != "library":
                raise CleanupError("Simulated stop before unchanged staging unlink")
            normal_unlink(f)
        with mock.patch.object(self.backend, "delete_arr", side_effect=remove_library), \
                mock.patch.object(self.fs, "unlink", side_effect=stop_before_copy):
            result = self.execute(preview)
        self.assertEqual("uncertain", result["state"])
        self.assertFalse(source.exists())
        self.assertTrue(duplicate.exists())
        return item, source, duplicate, preview

    def test_recovery_deletes_only_original_unchanged_copies_without_api_retries(self):
        item, source, duplicate, original = self.interrupted_copy_removal(sidecar=True)
        calls = list(self.backend.calls)
        unrelated = duplicate.parent / "new-unplanned.txt"
        unrelated.write_text("must stay")
        # The old preview may expire after its uncertain execution. Its signed
        # copy proof remains usable only through a newly guarded recovery plan.
        self.now[0] += 1900
        with mock.patch.object(self.fs, "copies", side_effect=AssertionError("must reuse original proof")):
            recovery = self.engine.recovery_plan(original["plan_id"])
            self.assertTrue(recovery["ready"])
            self.assertEqual(original["plan_id"], recovery["recovery_of"])
            self.assertEqual(0, recovery["library_file_count"])
            self.assertEqual(2, recovery["download_copy_and_sidecar_count"])
            result = self.execute(recovery)
        self.assertEqual("complete", result["state"])
        self.assertEqual(0, result["titles_removed"])
        self.assertEqual(2, result["download_files_removed"])
        self.assertFalse(duplicate.exists())
        self.assertFalse((duplicate.parent / "English.srt").exists())
        self.assertTrue(unrelated.exists())
        self.assertEqual(calls, self.backend.calls)
        self.assertEqual(result, self.execute(recovery))
        self.assertEqual(calls, self.backend.calls)
        with self.assertRaises(CleanupError):
            self.execute(original)

    def test_recovery_rejects_changed_copy_before_any_write(self):
        _, _, duplicate, original = self.interrupted_copy_removal()
        duplicate.write_bytes(b"x" * duplicate.stat().st_size)
        calls = list(self.backend.calls)
        with self.assertRaises(CleanupError):
            self.engine.recovery_plan(original["plan_id"])
        self.assertTrue(duplicate.exists())
        self.assertEqual(calls, self.backend.calls)

    def test_recovery_revalidates_changed_copy_after_preview(self):
        _, _, duplicate, original = self.interrupted_copy_removal()
        recovery = self.engine.recovery_plan(original["plan_id"])
        duplicate.write_bytes(b"different bytes")
        calls = list(self.backend.calls)
        with self.assertRaises(CleanupError):
            self.execute(recovery)
        self.assertTrue(duplicate.exists())
        self.assertEqual(calls, self.backend.calls)

    def test_recovery_rejects_new_staging_owner_at_plan_and_execution(self):
        item, _, duplicate, original = self.interrupted_copy_removal(sidecar=True)
        recovery = self.engine.recovery_plan(original["plan_id"])
        other = copy.deepcopy(item)
        other.update(ref="movie:radarr:2", arr_id=2, external_id=999,
                     root=str(self.media / "movies" / "Elsewhere (2001)"), plex_keys=[])
        self.backend.items.append(other)
        calls = list(self.backend.calls)
        with self.assertRaises(CleanupError):
            self.engine.recovery_plan(original["plan_id"])
        with self.assertRaises(CleanupError):
            self.execute(recovery)
        self.assertTrue(duplicate.exists())
        self.assertTrue((duplicate.parent / "English.srt").exists())
        self.assertEqual(calls, self.backend.calls)

    def test_recovery_rejects_metadata_recreated_under_new_identifier(self):
        item, _, duplicate, original = self.interrupted_copy_removal()
        recovery = self.engine.recovery_plan(original["plan_id"])
        other = copy.deepcopy(item)
        other.update(ref="movie:radarr:9", arr_id=9,
                     root=str(self.media / "movies" / "Different root (2001)"), plex_keys=[])
        self.backend.items.append(other)
        calls = list(self.backend.calls)
        with self.assertRaises(CleanupError):
            self.execute(recovery)
        self.assertTrue(duplicate.exists())
        self.assertEqual(calls, self.backend.calls)

    def test_recovery_rejects_library_arrival_and_unverified_absence(self):
        _, source, duplicate, original = self.interrupted_copy_removal()
        recovery = self.engine.recovery_plan(original["plan_id"])
        source.write_bytes(b"new library arrival")
        with self.assertRaises(CleanupError):
            self.execute(recovery)
        source.unlink()
        self.backend.verify_ok = False
        with self.assertRaises(CleanupError):
            self.engine.recovery_plan(original["plan_id"])
        self.assertTrue(duplicate.exists())

    def test_recovery_blocks_activity_without_retrying_api(self):
        item, _, duplicate, original = self.interrupted_copy_removal()
        recovery = self.engine.recovery_plan(original["plan_id"])
        calls = list(self.backend.calls)
        self.backend.playing = [str(duplicate)]
        with self.assertRaises(CleanupError):
            self.execute(recovery)
        self.backend.playing = []
        self.backend.queued.add(item["ref"])
        with self.assertRaises(CleanupError):
            self.engine.recovery_plan(original["plan_id"])
        self.assertTrue(duplicate.exists())
        self.assertEqual(calls, self.backend.calls)

    def test_recovery_recomputes_new_external_hardlink_retention(self):
        _, _, duplicate, original = self.interrupted_copy_removal()
        retained = self.root / "external-copy.mkv"
        os.link(duplicate, retained)
        recovery = self.engine.recovery_plan(original["plan_id"])
        self.assertEqual(0, recovery["expected_reclaimed_bytes"])
        self.assertGreater(recovery["retained_external_hardlink_bytes"], 0)
        self.assertEqual("complete", self.execute(recovery)["state"])
        self.assertFalse(duplicate.exists())
        self.assertTrue(retained.exists())

    def test_recovery_rejects_changed_physical_copy_even_when_virtual_file_is_unchanged(self):
        physical = self.root / "other-branch-copy.mkv"
        physical.write_bytes(b"media" * 2000)
        normal_physical = self.fs.physical
        def copies(path):
            rows = normal_physical(path)
            if Path(path).name == "renamed.mkv":
                rows.append(engine_module.fingerprint(physical))
            return rows
        with mock.patch.object(self.fs, "physical", side_effect=copies):
            _, _, duplicate, original = self.interrupted_copy_removal()
            physical.write_bytes(b"changed physical data")
            with self.assertRaises(CleanupError):
                self.engine.recovery_plan(original["plan_id"])
            self.assertTrue(duplicate.exists())
            self.assertTrue(physical.exists())

    def test_recovery_missing_virtual_copy_does_not_hide_unreadable_physical_copy(self):
        physical = self.root / "hidden-branch-copy.mkv"
        physical.write_bytes(b"media" * 2000)
        normal_physical = self.fs.physical
        def physical_copies(path):
            rows = normal_physical(path)
            if Path(path).name == "renamed.mkv":
                rows.append(engine_module.fingerprint(physical))
            return rows
        with mock.patch.object(self.fs, "physical", side_effect=physical_copies):
            _, _, duplicate, original = self.interrupted_copy_removal(sidecar=True)
        duplicate.unlink()
        normal_lstat = os.lstat
        for code in [errno.EACCES, errno.EIO, errno.ESTALE]:
            def inaccessible(path, *args, **kwargs):
                if Path(path) == physical:
                    raise OSError(code, "Simulated physical branch read failure")
                return normal_lstat(path, *args, **kwargs)
            with self.subTest(errno=code), mock.patch.object(engine_module.os, "lstat", side_effect=inaccessible):
                with self.assertRaises(OSError):
                    self.engine.recovery_plan(original["plan_id"])
        self.assertTrue(physical.exists())
        self.assertTrue((duplicate.parent / "English.srt").exists())
        self.assertEqual(1, len(self.backend.calls))

    def test_recovery_requires_explicit_settled_library_absence_even_if_tree_is_empty(self):
        _, source, duplicate, original = self.interrupted_copy_removal()
        source.write_bytes(b"stale or restored virtual source")
        with mock.patch.object(self.fs, "tree", return_value=[]):
            with self.assertRaisesRegex(CleanupError, "settled absent"):
                self.engine.recovery_plan(original["plan_id"])
        self.assertTrue(duplicate.exists())

    def test_recovery_revalidates_mount_and_original_signed_proof(self):
        _, _, duplicate, original = self.interrupted_copy_removal()
        recovery = self.engine.recovery_plan(original["plan_id"])
        mount = self.fs.mount_identity()
        mount["inode"] += 1
        with mock.patch.object(self.fs, "mount_identity", return_value=mount):
            with self.assertRaises(CleanupError):
                self.execute(recovery)
        source_path = self.engine.store.root / (original["plan_id"] + ".plan.json")
        value = json.loads(source_path.read_text())
        value["files"][-1]["mtime_ns"] += 1
        source_path.write_text(json.dumps(value))
        with self.assertRaises(CleanupError):
            self.execute(recovery)
        self.assertTrue(duplicate.exists())

    def test_recovery_requires_original_signed_started_proof(self):
        item, _ = self.add_item()
        not_started = self.plan(item)
        with self.assertRaises(CleanupError):
            self.engine.recovery_plan(not_started["plan_id"])
        _, _, duplicate, original = self.interrupted_copy_removal()
        path = self.engine.store.root / (original["plan_id"] + ".plan.json")
        value = json.loads(path.read_text())
        value["files"][-1]["size"] += 1
        path.write_text(json.dumps(value))
        with self.assertRaises(CleanupError):
            self.engine.recovery_plan(original["plan_id"])
        self.assertTrue(duplicate.exists())

    def test_partial_journal_has_space_before_and_safe_stop_telemetry(self):
        _, _, _, original = self.interrupted_copy_removal()
        journal = self.engine.store.read(original["plan_id"] + ".journal.json")
        self.assertIn("space_before", journal)
        self.assertIn("space_stopped", journal)
        self.assertIn("observed_available_bytes_delta", journal["result"])

    def test_space_before_is_durable_before_first_backend_mutation(self):
        item, _ = self.add_item()
        preview = self.plan(item, include_download_copies=False)
        native_delete = self.backend.delete_arr
        observed = []
        def inspect_then_delete(record):
            journal = self.engine.store.read(preview["plan_id"] + ".journal.json")
            self.assertEqual("running", journal["state"])
            self.assertTrue(journal["space_before"])
            self.assertEqual("attempting", journal["actions"][-1]["state"])
            observed.append(True)
            return native_delete(record)
        with mock.patch.object(self.backend, "delete_arr", side_effect=inspect_then_delete):
            self.assertEqual("complete", self.execute(preview)["state"])
        self.assertEqual([True], observed)


if __name__ == "__main__":
    unittest.main()
