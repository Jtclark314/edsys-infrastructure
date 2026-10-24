"""Fixture-only transport, mapping and native-deletion guard checks."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import backend


def movie(**changes):
    result = {"ref": "movie:radarr:12", "kind": "movie", "title": "Example", "year": 2001,
              "root": "/mnt/media/movies/Example (2001)", "arr_service": "radarr", "arr_id": 12,
              "external_id": 99, "collection_id": None, "collection_title": None, "plex_keys": [],
              "estimated_bytes": 1, "has_files": True}
    result.update(changes)
    return result


class MappingTests(unittest.TestCase):
    def test_references_and_direct_roots(self):
        self.assertEqual(backend._item(movie())["arr_id"], 12)
        for changed in [movie(ref="movie:sonarr:12"), movie(arr_id=True), movie(root="/mnt/media/movies"),
                        movie(root="/mnt/media/movies/../tv/Example"), movie(root="/mnt/media/movies/Example/Season 1"),
                        movie(root="/mnt/media/movies/./Example"), movie(plex_keys=["../1"]),
                        movie(mapping_blocker="Multiple roots")]:
            with self.subTest(changed=changed), self.assertRaises(backend.BackendError):
                backend._item(changed)

    def test_exact_paths_do_not_join_equal_titles(self):
        raw = [{"kind": "movie", "arr_service": "radarr", "arr_id": 12, "external_id": 99,
                "title": "Example", "year": 2001, "container_root": "/movies/Example (2001)",
                "collection_id": None, "collection_title": None, "estimated_bytes": 10, "has_files": True}]
        sections = ET.fromstring('<MediaContainer><Directory key="1" type="movie"><Location path="/media/movies"/></Directory></MediaContainer>')
        listing = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie" title="Example" year="2001"><Media><Part file="/media/movies/Other Folder/film.mkv" size="10"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=raw), mock.patch.object(b, "_plex", side_effect=[(200, sections), (200, listing)]):
            results = b.catalog()
        self.assertEqual(results[0]["plex_keys"], [])
        self.assertEqual(results[1]["ref"], "movie:plex:900")
        self.assertEqual(results[1]["root"], "/mnt/media/movies/Other Folder")

    def test_unsupported_plex_part_marks_corresponding_arr_item_blocked(self):
        raw = [{"kind": "movie", "arr_service": "radarr", "arr_id": 12, "external_id": 99,
                "title": "Example", "year": 2001, "container_root": "/movies/Example (2001)"}]
        sections = ET.fromstring('<MediaContainer><Directory key="1" type="movie"><Location path="/media/movies"/></Directory></MediaContainer>')
        listing = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie" title="Example"><Guid id="tmdb://99"/><Media><Part file="/unapproved/film.mkv"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=raw), mock.patch.object(b, "_plex", side_effect=[(200, sections), (200, listing)]):
            results = b.catalog()
        self.assertTrue(results[0]["mapping_blocker"])

    def test_active_session_uses_metadata_when_part_paths_are_missing(self):
        sessions = ET.fromstring('<MediaContainer><Video ratingKey="123" type="episode"/></MediaContainer>')
        detail = ET.fromstring('<MediaContainer><Video><Media><Part file="/media/tv/Example/Season 1/S01E01.mkv"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(b, "_plex", side_effect=[(200, sessions), (200, detail)]):
            self.assertEqual(b.active_paths(), ["/mnt/media/tv/Example/Season 1/S01E01.mkv"])
        with mock.patch.object(b, "_plex", side_effect=[(200, sessions), (200, ET.fromstring('<MediaContainer/>'))]):
            with self.assertRaisesRegex(backend.BackendError, "preserved"):
                b.active_paths()

    def test_http_error_does_not_return_body_or_credential(self):
        prefs = mock.Mock()
        prefs.getroot.return_value.get.return_value = "private-example-credential"
        b = backend.LiveBackend()
        error = urllib.error.HTTPError("http://127.0.0.1:32400/library/metadata/1", 403, "sensitive reason", {}, io.BytesIO(b"sensitive upstream body"))
        with mock.patch.object(backend.ET, "parse", return_value=prefs), mock.patch.object(b._opener, "open", side_effect=error):
            with self.assertRaises(backend.BackendError) as caught:
                b._plex("/library/metadata/1", method="DELETE")
        self.assertTrue(caught.exception.uncertain)
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertNotIn("private-example", str(caught.exception))

    def test_verification_catches_a_recreated_plex_key(self):
        selected = movie(plex_keys=["123"])
        recreated = movie(ref="movie:plex:456", arr_service=None, arr_id=None, plex_keys=["456"])
        b = backend.LiveBackend()
        arr = [{"ref": selected["ref"], "arr_removed": True, "import_exclusion_verified": True}]
        with mock.patch.object(backend, "_run_remote", return_value=arr), mock.patch.object(b, "catalog", return_value=[recreated]), mock.patch.object(b, "_plex", return_value=(404, None)):
            verified = b.verify([selected])
        self.assertTrue(verified["arr_removed"])
        self.assertFalse(verified["plex_removed"])

    def test_movie_catalog_skips_tv_listing_and_show_descriptors(self):
        sections = ET.fromstring('<MediaContainer><Directory key="1" type="movie"><Location path="/media/movies"/></Directory><Directory key="2" type="show"><Location path="/media/tv"/></Directory></MediaContainer>')
        listing = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie" title="Example" year="2001"><Media><Part file="/media/movies/Example (2001)/film.mkv" size="10"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=[]) as remote, mock.patch.object(b, "_plex", side_effect=[(200, sections), (200, listing)]) as plex:
            results = b.catalog("movie")
        remote.assert_called_once_with({"operation": "catalog", "kind": "movie"})
        self.assertEqual(["/library/sections", "/library/sections/1/all?includeGuids=1"], [call.args[0] for call in plex.call_args_list])
        self.assertEqual(["movie:plex:900"], [item["ref"] for item in results])

    def test_same_root_preserves_different_plex_title_as_alias(self):
        raw = [{"kind": "movie", "arr_service": "radarr", "arr_id": 12, "external_id": 112,
                "title": "Canonical Title", "year": 2001,
                "container_root": "/movies/Canonical Title (2001)"}]
        sections = ET.fromstring('<MediaContainer><Directory key="1" type="movie"><Location path="/media/movies"/></Directory></MediaContainer>')
        listing = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie" title="Display Title" year="2001"><Guid id="tmdb://112"/><Media><Part file="/media/movies/Canonical Title (2001)/film.mkv" size="10"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=raw), mock.patch.object(b, "_plex", side_effect=[(200, sections), (200, listing)]):
            result = b.catalog("movie")
        self.assertEqual(1, len(result))
        self.assertEqual("Canonical Title", result[0]["title"])
        self.assertEqual(["Display Title"], result[0]["aliases"])
        self.assertEqual(["900"], result[0]["plex_keys"])
        self.assertEqual(112, result[0]["external_id"])

    def test_flat_plex_movie_maps_exact_file_instead_of_library_base(self):
        sections = ET.fromstring('<MediaContainer><Directory key="1" type="movie"><Location path="/media/movies"/></Directory></MediaContainer>')
        filename = "Example.2001.1080p.WEBRip.x265-GROUP.mkv"
        listing = ET.fromstring(f'<MediaContainer><Video ratingKey="901" type="movie" title="Example" year="2001"><Guid id="tmdb://113"/><Media><Part file="/media/movies/{filename}" size="10"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=[]), mock.patch.object(b, "_plex", side_effect=[(200, sections), (200, listing)]):
            result = b.catalog("movie")
        self.assertEqual(1, len(result))
        self.assertEqual("movie:plex:901", result[0]["ref"])
        self.assertEqual("/mnt/media/movies/" + filename, result[0]["root"])
        self.assertTrue(result[0]["standalone_file"])
        self.assertFalse(result[0]["mapping_blocker"])
        self.assertIsNone(result[0]["arr_service"])

    def test_targeted_verification_does_not_rebuild_catalog(self):
        selected = movie(plex_keys=["123"])
        arr = [{"ref": selected["ref"], "arr_removed": True, "import_exclusion_verified": True}]
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=arr), mock.patch.object(b, "catalog", side_effect=AssertionError("Unexpected full catalogue rebuild")), mock.patch.object(b, "_plex", return_value=(404, None)) as plex:
            verified = b.verify([selected], check_catalog=False)
        self.assertTrue(verified["arr_removed"])
        self.assertTrue(verified["plex_removed"])
        self.assertTrue(verified["exclusions_verified"])
        plex.assert_called_once_with("/library/metadata/123", missing=True)

    def test_invalid_scope_fails_before_any_transport(self):
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote") as remote, mock.patch.object(b, "_plex") as plex:
            for method in [b.catalog, b.active_download_refs]:
                with self.assertRaises(backend.BackendError):
                    method("music")
        remote.assert_not_called()
        plex.assert_not_called()


class StandaloneMovieTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="edsys-flat-movie-fixture-")
        self.root = Path(self.temp.name)
        self.movies = self.root / "movies"
        self.movies.mkdir()
        self.path = self.movies / "Example.2001.mkv"
        self.path.write_bytes(b"Fixture video")
        self.roots = mock.patch.object(backend, "MEDIA_ROOTS", {"movie": str(self.movies), "show": str(self.root / "tv")})
        self.roots.start()
        self.item = movie(ref="movie:plex:900", arr_service=None, arr_id=None, external_id=None,
                          root=str(self.path), plex_keys=["900"], standalone_file=True)

    def tearDown(self):
        self.roots.stop()
        self.temp.cleanup()

    def test_exact_standalone_file_is_valid_but_bases_and_other_kinds_are_not(self):
        self.assertIs(self.item, backend._item(self.item))
        for changed in [dict(self.item, root=str(self.movies)),
                        dict(self.item, root=str(self.movies / "notes.txt")),
                        dict(self.item, root=str(self.movies / "dir" / "film.mkv")),
                        dict(self.item, root=str(self.movies / ".." / "elsewhere.mkv")),
                        dict(self.item, ref="show:plex:900", kind="show"),
                        dict(self.item, ref="movie:radarr:12", arr_service="radarr", arr_id=12, external_id=99),
                        dict(self.item, standalone_file="true")]:
            with self.subTest(changed=changed), self.assertRaises(backend.BackendError):
                backend._item(changed)

    def test_flat_tv_and_nonvideo_plex_parts_remain_unsupported(self):
        for path, kind in [("/media/tv/Example.mkv", "show"),
                           ("/media/movies/notes.txt", "movie"),
                           ("/media/movies/../Example.mkv", "movie"),
                           ("/media/movies", "movie")]:
            with self.subTest(path=path), self.assertRaises(backend.BackendError):
                backend._part_root(path, kind)

    def test_standalone_symlink_or_directory_is_rejected(self):
        self.path.unlink()
        other = self.root / "outside.mkv"
        other.write_bytes(b"Unrelated fixture")
        self.path.symlink_to(other)
        with self.assertRaisesRegex(backend.BackendError, "symlink"):
            backend._item(self.item)
        self.path.unlink()
        self.path.mkdir()
        with self.assertRaisesRegex(backend.BackendError, "regular file"):
            backend._item(self.item)

    def test_flat_movie_playback_resolves_to_exact_file(self):
        sessions = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie"><Media><Part file="/media/movies/Example.2001.mkv"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(b, "_plex", return_value=(200, sessions)):
            self.assertEqual([str(self.path)], b.active_paths())

    def test_preflight_requires_the_exact_standalone_mapping(self):
        detail = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie"><Media><Part file="/media/movies/Example.2001.mkv"/></Media></Video></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(b, "_plex", return_value=(200, detail)):
            b._verify_plex_mapping(self.item)
        changed = ET.fromstring('<MediaContainer><Video ratingKey="900" type="movie"><Media><Part file="/media/movies/Other.2001.mkv"/></Media></Video></MediaContainer>')
        with mock.patch.object(b, "_plex", return_value=(200, changed)), self.assertRaises(backend.BackendError):
            b._verify_plex_mapping(self.item)

    def test_plex_delete_requires_file_absence_and_never_walks_library_base(self):
        b = backend.LiveBackend()
        with mock.patch.object(b, "_verify_plex_mapping"), mock.patch.object(b, "active_paths", return_value=[]), mock.patch.object(b, "_plex") as plex:
            with self.assertRaisesRegex(backend.BackendError, "remains"):
                b.delete_plex(self.item)
            plex.assert_not_called()
        self.path.unlink()  # Only the disposable fixture file is removed.
        with mock.patch.object(b, "_verify_plex_mapping"), mock.patch.object(b, "active_paths", return_value=[]), mock.patch.object(b, "_plex", side_effect=[(200, None), (200, None), (404, None)]) as plex, mock.patch.object(backend.os, "walk", side_effect=AssertionError("Unexpected directory traversal")):
            result = b.delete_plex(self.item)
        self.assertTrue(result["plex_removed"])
        self.assertTrue(self.movies.is_dir())
        self.assertEqual(["/library/metadata/900"] * 3, [call.args[0] for call in plex.call_args_list])
        self.assertEqual("DELETE", plex.call_args_list[1].kwargs["method"])


class MultipleMovieDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.primary = "/mnt/media/movies/Example (2001)"
        self.extra = "/mnt/media/movies/Example.2001.UHD.REMUX-GROUP"
        self.item = movie(root=self.primary, plex_keys=["900"], additional_roots=[self.extra])

    def raw(self, **changes):
        result = {"kind": "movie", "arr_service": "radarr", "arr_id": 12, "external_id": 99,
                  "title": "Example", "year": 2001, "container_root": "/movies/Example (2001)"}
        result.update(changes)
        return result

    def listing(self, year=2001, guid="tmdb://99", extra=None, additional_video=""):
        extra = extra or "/media/movies/Example.2001.UHD.REMUX-GROUP"
        return ET.fromstring(f'<MediaContainer><Video ratingKey="900" type="movie" title="Example" year="{year}"><Guid id="{guid}"/><Media><Part file="/media/movies/Example (2001)/film.mkv" size="10"/></Media><Media><Part file="{extra}/film.mkv" size="20"/></Media></Video>{additional_video}</MediaContainer>')

    def catalog(self, listing=None, raw=None):
        sections = ET.fromstring('<MediaContainer><Directory key="1" type="movie"><Location path="/media/movies"/></Directory></MediaContainer>')
        b = backend.LiveBackend()
        with mock.patch.object(backend, "_run_remote", return_value=raw if raw is not None else [self.raw()]), mock.patch.object(b, "_plex", side_effect=[(200, sections), (200, listing if listing is not None else self.listing())]):
            return b.catalog("movie")

    def test_catalog_accepts_confirmed_versions_for_one_managed_movie(self):
        result = self.catalog()
        self.assertEqual(1, len(result))
        self.assertEqual(self.primary, result[0]["root"])
        self.assertEqual([self.extra], result[0]["additional_roots"])
        self.assertEqual(["900"], result[0]["plex_keys"])
        self.assertFalse(result[0].get("mapping_blocker"))
        self.assertIs(result[0], backend._item(result[0]))

    def test_catalog_rejects_foreign_guid_year_or_directory_identity(self):
        for listing in [self.listing(guid="tmdb://100"), self.listing(guid="imdb://tt12345"),
                        self.listing(year=2002), self.listing(extra="/media/movies/Other (2001)"),
                        self.listing(extra="/media/movies/Example (2002)")]:
            with self.subTest(xml=ET.tostring(listing)):
                result = self.catalog(listing=listing)
                self.assertTrue(result[0].get("mapping_blocker"))
                self.assertNotIn("additional_roots", result[0])

    def test_catalog_rejects_another_arr_owner_or_shared_plex_root(self):
        other_arr = self.raw(arr_id=13, external_id=100, title="Other", container_root="/movies/Example.2001.UHD.REMUX-GROUP")
        result = self.catalog(raw=[self.raw(), other_arr])
        self.assertTrue(all(item.get("mapping_blocker") for item in result))
        shared = '<Video ratingKey="901" type="movie" title="Other" year="2001"><Guid id="tmdb://100"/><Media><Part file="/media/movies/Example.2001.UHD.REMUX-GROUP/other.mkv"/></Media></Video>'
        result = self.catalog(listing=self.listing(additional_video=shared))
        self.assertTrue(result[0].get("mapping_blocker"))
        self.assertNotIn("additional_roots", result[0])

    def test_catalog_rejects_unmanaged_or_flat_multiple_root_shapes(self):
        unmanaged = self.catalog(raw=[])
        self.assertTrue(unmanaged[0].get("mapping_blocker"))
        self.assertNotIn("additional_roots", unmanaged[0])
        listing = self.listing()
        listing.findall(".//Part")[1].set("file", "/media/movies/Example.2001.mkv")
        managed = self.catalog(listing=listing)
        self.assertTrue(managed[0].get("mapping_blocker"))
        self.assertNotIn("additional_roots", managed[0])

    def test_item_rejects_duplicate_unsupported_or_unbounded_additional_roots(self):
        self.assertIs(self.item, backend._item(self.item))
        for changed in [dict(self.item, additional_roots=[self.primary]),
                        dict(self.item, additional_roots=[self.extra, self.extra]),
                        dict(self.item, additional_roots=["/mnt/media/movies"]),
                        dict(self.item, additional_roots=[self.extra + "/Nested"]),
                        dict(self.item, additional_roots=["/mnt/media/movies/Other (2001)"]),
                        dict(self.item, additional_roots=["/mnt/media/movies/Example (2002)"]),
                        dict(self.item, additional_roots=[self.extra] * 9),
                        dict(self.item, additional_roots=self.extra),
                        dict(self.item, ref="movie:plex:900", arr_service=None, arr_id=None),
                        dict(self.item, ref="show:sonarr:12", kind="show", arr_service="sonarr"),
                        dict(self.item, standalone_file=True)]:
            with self.subTest(changed=changed), self.assertRaises(backend.BackendError):
                backend._item(changed)

    def test_preflight_rechecks_full_root_set_guid_and_year(self):
        b = backend.LiveBackend()
        with mock.patch.object(b, "_plex", return_value=(200, self.listing())) as plex:
            b._verify_plex_mapping(self.item)
        plex.assert_called_once_with("/library/metadata/900?includeGuids=1", missing=True)
        for listing in [self.listing(guid="tmdb://100"), self.listing(year=2002),
                        self.listing(extra="/media/movies/Example.2001.Other-Version")]:
            with self.subTest(xml=ET.tostring(listing)), mock.patch.object(b, "_plex", return_value=(200, listing)), self.assertRaises(backend.BackendError):
                b._verify_plex_mapping(self.item)

    def test_native_remote_selection_keeps_primary_relative_files_only(self):
        self.item["relative_files"] = [{"path": "film.mkv", "size": 10, "mtime_ns": 123}]
        selected = backend._remote_items([self.item])
        self.assertEqual("/movies/Example (2001)", selected[0]["container_root"])
        self.assertEqual(self.item["relative_files"], selected[0]["relative_files"])
        self.assertNotIn("additional_roots", selected[0])

    def test_playback_in_additional_root_blocks_both_native_deletes(self):
        b = backend.LiveBackend()
        for method in [b.delete_arr, b.delete_plex]:
            with self.subTest(method=method.__name__), mock.patch.object(b, "_verify_plex_mapping"), mock.patch.object(b, "active_paths", return_value=[self.extra + "/film.mkv"]), mock.patch.object(backend, "_run_remote") as remote, mock.patch.object(b, "_plex") as plex:
                with self.assertRaisesRegex(backend.BackendError, "playing"):
                    method(self.item)
                remote.assert_not_called()
                plex.assert_not_called()

    def test_plex_delete_refuses_files_remaining_in_any_additional_root(self):
        b = backend.LiveBackend()
        with mock.patch.object(b, "_verify_plex_mapping"), mock.patch.object(b, "active_paths", return_value=[]), mock.patch.object(backend.os.path, "lexists", side_effect=lambda path: path == self.extra), mock.patch.object(backend.os.path, "islink", return_value=False), mock.patch.object(backend.os.path, "isdir", return_value=True), mock.patch.object(backend.os, "walk", return_value=[(self.extra, [], ["film.mkv"])]), mock.patch.object(b, "_plex") as plex:
            with self.assertRaisesRegex(backend.BackendError, "files remain"):
                b.delete_plex(self.item)
            plex.assert_not_called()

    def test_verification_detects_a_recreated_record_on_an_additional_root(self):
        b = backend.LiveBackend()
        recreated = movie(ref="movie:plex:901", arr_service=None, arr_id=None, root=self.extra, plex_keys=["901"])
        arr = [{"ref": self.item["ref"], "arr_removed": True, "import_exclusion_verified": True}]
        with mock.patch.object(backend, "_run_remote", return_value=arr), mock.patch.object(b, "catalog", return_value=[recreated]), mock.patch.object(b, "_plex", return_value=(404, None)):
            verified = b.verify([self.item])
        self.assertTrue(verified["arr_removed"])
        self.assertFalse(verified["plex_removed"])

    def test_fresh_ownership_guard_blocks_new_arr_owner_of_extra_directory(self):
        b = backend.LiveBackend()
        foreign = movie(ref="movie:radarr:13", arr_id=13, external_id=100,
                        title="Foreign", root=self.extra, plex_keys=[])
        with mock.patch.object(b, "catalog", return_value=[self.item, foreign]) as catalog:
            with self.assertRaisesRegex(backend.BackendError, "Another library record"):
                b.check_library_ownership(self.item)
        catalog.assert_called_once_with("movie")

    def test_fresh_ownership_guard_blocks_foreign_plex_later_root(self):
        b = backend.LiveBackend()
        foreign = movie(ref="movie:plex:901", arr_service=None, arr_id=None, title="Foreign",
                        root="/mnt/media/movies/Foreign (2001)", plex_keys=["901"],
                        mapped_roots=["/mnt/media/movies/Foreign (2001)", self.extra])
        with mock.patch.object(b, "catalog", return_value=[self.item, foreign]), self.assertRaisesRegex(backend.BackendError, "Another library record"):
            b.check_library_ownership(self.item)

    def test_fresh_ownership_guard_preserves_foreign_managed_plex_later_root_after_arr_removal(self):
        foreign_arr = self.raw(arr_id=13, external_id=100, title="Foreign",
                               container_root="/movies/Foreign (2001)")
        foreign_video = ('<Video ratingKey="901" type="movie" title="Foreign" year="2001">'
                         '<Guid id="tmdb://100"/>'
                         '<Media><Part file="/media/movies/Foreign (2001)/own.mkv" size="10"/></Media>'
                         '<Media><Part file="/media/movies/Example.2001.UHD.REMUX-GROUP/other.mkv" size="20"/></Media>'
                         '</Video>')
        # Original ARR record is gone; its same-key Plex entry still exists.
        catalog = self.catalog(raw=[foreign_arr], listing=self.listing(additional_video=foreign_video))
        foreign = next(item for item in catalog if item["ref"] == "movie:radarr:13")
        self.assertTrue(foreign["mapping_blocker"])
        self.assertEqual([self.extra, "/mnt/media/movies/Foreign (2001)"], foreign["mapped_roots"])
        self.assertNotIn("additional_roots", foreign)
        b = backend.LiveBackend()
        with mock.patch.object(b, "catalog", return_value=catalog), self.assertRaisesRegex(backend.BackendError, "Another library record"):
            b.check_library_ownership(self.item)

    def test_fresh_ownership_guard_allows_expected_same_key_after_arr_removal(self):
        b = backend.LiveBackend()
        expected = movie(ref="movie:plex:900", arr_service=None, arr_id=None, external_id=None,
                         root=self.primary, plex_keys=["900"], mapped_roots=[self.primary, self.extra],
                         mapping_blocker="Plex record spans multiple library directories")
        with mock.patch.object(b, "catalog", return_value=[expected]):
            b.check_library_ownership(self.item)

    def test_fresh_ownership_guard_rejects_changed_same_arr_identity_or_root(self):
        b = backend.LiveBackend()
        for current in [dict(self.item, external_id=100),
                        dict(self.item, root="/mnt/media/movies/Example.2001.New-Root"),
                        dict(self.item, additional_roots=[]),
                        dict(self.item, mapping_blocker="New overlap")]:
            with self.subTest(current=current), mock.patch.object(b, "catalog", return_value=[current]), self.assertRaisesRegex(backend.BackendError, "mapping changed"):
                b.check_library_ownership(self.item)

    def test_fresh_ownership_guard_is_run_by_preflight_and_before_native_delete(self):
        b = backend.LiveBackend()
        for method in [lambda: b.preflight([self.item]), lambda: b.delete_arr(self.item)]:
            with mock.patch.object(b, "_verify_plex_mapping"), mock.patch.object(b, "active_paths", return_value=[]), mock.patch.object(b, "check_library_ownership", side_effect=backend.BackendError("New owner")) as ownership, mock.patch.object(backend, "_run_remote") as remote:
                with self.assertRaisesRegex(backend.BackendError, "New owner"):
                    method()
                ownership.assert_called_once_with(self.item)
                remote.assert_not_called()

    def test_single_directory_does_not_need_another_catalog_read(self):
        b = backend.LiveBackend()
        with mock.patch.object(b, "catalog") as catalog:
            b.check_library_ownership(movie())
        catalog.assert_not_called()


class RemoteScopeTests(unittest.TestCase):
    def setUp(self):
        self.ns = {}
        definitions = backend.REMOTE_HELPER.split("try:\n raw=sys.stdin.buffer.read", 1)[0]
        exec(compile(definitions, "<remote-scope-fixture>", "exec"), self.ns)

    def test_movie_catalog_never_calls_sonarr(self):
        def api(service, path, **kwargs):
            self.assertEqual("radarr", service)
            if path == "/collection":
                return 200, []
            self.assertEqual("/movie", path)
            return 200, [{"id": 12, "tmdbId": 99, "title": "Example", "year": 2001, "path": "/movies/Example (2001)"}]
        self.ns["api"] = api
        result = self.ns["execute"]({"operation": "catalog", "kind": "movie"})
        self.assertEqual(["movie"], [item["kind"] for item in result])

    def test_show_catalog_never_calls_radarr(self):
        def api(service, path, **kwargs):
            self.assertEqual(("sonarr", "/series"), (service, path))
            return 200, [{"id": 12, "tvdbId": 99, "title": "Example", "year": 2001, "path": "/tv/Example"}]
        self.ns["api"] = api
        result = self.ns["execute"]({"operation": "catalog", "kind": "show"})
        self.assertEqual(["show"], [item["kind"] for item in result])

    def test_movie_queue_reads_only_radarr(self):
        self.ns["queue_refs"] = mock.Mock(return_value=["movie:radarr:12"])
        self.assertEqual(["movie:radarr:12"], self.ns["execute"]({"operation": "active_downloads", "kind": "movie"}))
        self.ns["queue_refs"].assert_called_once_with("radarr")


class RemoteDeletionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "film.mkv"
        self.path.write_bytes(b"fixture movie")
        self.ns = {}
        # Execute definitions only: never the transport's stdin dispatcher.
        definitions = backend.REMOTE_HELPER.split("try:\n raw=sys.stdin.buffer.read", 1)[0]
        exec(compile(definitions, "<remote-adapter-fixture>", "exec"), self.ns)
        self.calls = []
        self.deleted = False
        self.recycle = ""
        self.other_records = []
        self.verify_clock = 0.0
        self.ns["time"] = types.SimpleNamespace(monotonic=lambda: self.verify_clock, sleep=self.advance_verify_clock)
        self.current = {"id": 12, "tmdbId": 99, "path": "/movies/Example (2001)", "hasFile": True}
        self.ns["api"] = self.api
        self.ns["container_path"] = lambda service, path: str(self.root)
        self.ns["queue_refs"] = lambda service: []
        s = self.path.stat()
        self.item = {"ref": "movie:radarr:12", "arr_service": "radarr", "arr_id": 12, "external_id": 99,
                     "container_root": self.current["path"], "relative_files": [{"path": "film.mkv", "size": s.st_size, "mtime_ns": s.st_mtime_ns}]}

    def tearDown(self):
        self.temp.cleanup()

    def advance_verify_clock(self, seconds):
        self.verify_clock += seconds

    def api(self, service, path, method="GET", body=None, query=None, missing=False, timeout=30):
        self.calls.append((service, path, method, query))
        if path == "/config/mediamanagement":
            return 200, {"recycleBin": self.recycle}
        if path in ("/movie", "/series"):
            return 200, ([] if self.deleted else [dict(self.current)]) + self.other_records
        if path in ("/exclusions", "/importlistexclusion"):
            return 200, [{"tmdbId": 99} if service == "radarr" else {"tvdbId": 99}] if self.deleted else []
        if path in ("/movie/12", "/series/12"):
            if method == "DELETE":
                self.deleted = True
                return 200, None
            return (404, None) if self.deleted else (200, dict(self.current))
        self.fail("Unexpected route " + path)

    def delete(self):
        return self.ns["execute"]({"operation": "delete", "items": [self.item]})

    def assert_no_write(self):
        self.assertFalse(any(x[2] != "GET" for x in self.calls))

    def test_native_delete_uses_exclusion_and_verifies_it(self):
        result = self.delete()
        self.assertTrue(result["import_exclusion_verified"])
        query = next(x[3] for x in self.calls if x[2] == "DELETE")
        self.assertEqual(query, {"deleteFiles": "true", "addImportExclusion": "true"})

    def test_whole_show_deletion_uses_sonarr_import_list_exclusion(self):
        self.item.update(ref="show:sonarr:12", arr_service="sonarr", container_root="/tv/Example")
        self.current.update(path="/tv/Example", tvdbId=99)
        result = self.delete()
        self.assertTrue(result["import_exclusion_verified"])
        query = next(x[3] for x in self.calls if x[2] == "DELETE")
        self.assertEqual(query, {"deleteFiles": "true", "addImportListExclusion": "true"})

    def test_delayed_import_exclusion_is_polled_without_repeating_delete(self):
        native_api = self.ns["api"]
        exclusion_reads = []
        def delayed(service, path, **kwargs):
            if self.deleted and path == "/exclusions":
                exclusion_reads.append(kwargs["timeout"])
                if len(exclusion_reads) < 3:
                    return 200, []
            return native_api(service, path, **kwargs)
        self.ns["api"] = delayed
        self.assertTrue(self.delete()["import_exclusion_verified"])
        self.assertEqual(len(exclusion_reads), 3)
        self.assertTrue(all(0 < timeout <= 10 for timeout in exclusion_reads))
        self.assertEqual(sum(x[2] == "DELETE" for x in self.calls), 1)

    def test_delayed_record_disappearance_is_polled_without_repeating_delete(self):
        native_api = self.ns["api"]
        record_reads = []
        def delayed(service, path, **kwargs):
            if self.deleted and path == "/movie/12" and kwargs.get("method", "GET") == "GET":
                record_reads.append(kwargs["timeout"])
                if len(record_reads) < 3:
                    return 200, dict(self.current)
            return native_api(service, path, **kwargs)
        self.ns["api"] = delayed
        self.assertTrue(self.delete()["import_exclusion_verified"])
        self.assertEqual(len(record_reads), 3)
        self.assertEqual(sum(x[2] == "DELETE" for x in self.calls), 1)

    def test_missing_exclusion_times_out_and_never_retries_delete(self):
        native_api = self.ns["api"]
        def never_appears(service, path, **kwargs):
            if self.deleted and path == "/exclusions":
                return 200, []
            return native_api(service, path, **kwargs)
        self.ns["api"] = never_appears
        with self.assertRaisesRegex(self.ns["Error"], "import exclusion.*within 10 seconds"):
            self.delete()
        self.assertEqual(self.verify_clock, 10.0)
        self.assertEqual(sum(x[2] == "DELETE" for x in self.calls), 1)

    def test_record_that_remains_times_out_and_never_retries_delete(self):
        native_api = self.ns["api"]
        def remains_visible(service, path, **kwargs):
            if self.deleted and path == "/movie/12" and kwargs.get("method", "GET") == "GET":
                return 200, dict(self.current)
            return native_api(service, path, **kwargs)
        self.ns["api"] = remains_visible
        with self.assertRaisesRegex(self.ns["Error"], "record disappearance.*within 10 seconds"):
            self.delete()
        self.assertEqual(self.verify_clock, 10.0)
        self.assertEqual(sum(x[2] == "DELETE" for x in self.calls), 1)

    def test_verification_http_error_fails_closed_without_poll_or_delete_retry(self):
        native_api = self.ns["api"]
        exclusion_reads = []
        def unavailable(service, path, **kwargs):
            if self.deleted and path == "/exclusions":
                exclusion_reads.append(path)
                raise self.ns["Error"]("radarr API returned HTTP 503")
            return native_api(service, path, **kwargs)
        self.ns["api"] = unavailable
        with self.assertRaisesRegex(self.ns["Error"], "HTTP 503"):
            self.delete()
        self.assertEqual(len(exclusion_reads), 1)
        self.assertEqual(sum(x[2] == "DELETE" for x in self.calls), 1)

    def test_missing_unavailable_title_removal_can_exclude_without_files(self):
        self.path.unlink()
        self.root.rmdir()
        self.current["hasFile"] = False
        self.item["relative_files"] = []
        self.assertTrue(self.delete()["import_exclusion_verified"])

    def test_new_file_after_preview_blocks_every_write(self):
        (self.root / "new-release.mkv").write_bytes(b"different")
        with self.assertRaisesRegex(self.ns["Error"], "changed after preview"):
            self.delete()
        self.assert_no_write()

    def test_modified_file_after_preview_blocks_every_write(self):
        self.path.write_bytes(b"new bytes")
        with self.assertRaisesRegex(self.ns["Error"], "changed after preview"):
            self.delete()
        self.assert_no_write()

    def test_symlink_blocks_every_write(self):
        (self.root / "outside-link").symlink_to("/etc/passwd")
        with self.assertRaisesRegex(self.ns["Error"], "nonregular"):
            self.delete()
        self.assert_no_write()

    def test_recycle_bin_blocks_every_write(self):
        self.recycle = "/recycle"
        with self.assertRaisesRegex(self.ns["Error"], "recycle bin"):
            self.delete()
        self.assert_no_write()

    def test_queue_entry_blocks_every_write(self):
        self.ns["queue_refs"] = lambda service: [self.item["ref"]]
        with self.assertRaisesRegex(self.ns["Error"], "queue entry"):
            self.delete()
        self.assert_no_write()

    def test_identity_drift_blocks_every_write(self):
        self.current["tmdbId"] = 123
        with self.assertRaisesRegex(self.ns["Error"], "identity or root changed"):
            self.delete()
        self.assert_no_write()

    def test_new_shared_root_record_blocks_every_write(self):
        self.other_records.append({"id": 13, "tmdbId": 100, "path": self.current["path"]})
        with self.assertRaisesRegex(self.ns["Error"], "overlaps"):
            self.delete()
        self.assert_no_write()

    def test_new_nested_unselected_root_blocks_every_write(self):
        self.other_records.append({"id": 13, "tmdbId": 100, "path": self.current["path"] + "/Extras"})
        with self.assertRaisesRegex(self.ns["Error"], "mapping"):
            self.delete()
        self.assert_no_write()

    def test_catalog_does_not_silently_hide_invalid_other_roots(self):
        current = dict(self.current)
        def catalog_api(service, path, **kwargs):
            if path == "/collection":
                return 200, []
            if path == "/movie":
                return 200, [current, {"id": 13, "tmdbId": 100, "path": current["path"] + "/Extras"}]
            self.fail("Unexpected catalogue route")
        self.ns["api"] = catalog_api
        with self.assertRaisesRegex(self.ns["Error"], "mapping"):
            self.ns["execute"]({"operation": "catalog"})

    def test_verification_detects_same_movie_recreated_under_another_arr_id(self):
        self.deleted = True
        self.other_records.append({"id": 13, "tmdbId": 99, "path": "/movies/Another Folder"})
        results = self.ns["execute"]({"operation": "verify", "items": [self.item]})
        self.assertFalse(results[0]["arr_removed"])
        self.assertTrue(results[0]["import_exclusion_verified"])

    def test_readonly_preflight_can_validate_the_full_inventory(self):
        result = self.ns["execute"]({"operation": "preflight", "items": [self.item]})
        self.assertEqual(result["checked"], [self.item["ref"]])
        self.assert_no_write()


class BoundedTransportTests(unittest.TestCase):
    def test_remote_stdout_cap(self):
        real_popen = subprocess.Popen
        def fixture(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import sys;sys.stdin.buffer.read();print('x'*2048)"], **kwargs)
        with mock.patch.object(backend, "MAX_BYTES", 1024), mock.patch.object(backend.subprocess, "Popen", side_effect=fixture):
            with self.assertRaisesRegex(backend.BackendError, "bounded limit"):
                backend._run_remote({"operation": "catalog"})

    def test_remote_timeout_is_uncertain_only_for_writes(self):
        real_popen = subprocess.Popen
        def fixture(*args, **kwargs):
            return real_popen([sys.executable, "-c", "import sys,time;sys.stdin.buffer.read();time.sleep(5)"], **kwargs)
        with mock.patch.object(backend, "SSH_TIMEOUT", 0.05), mock.patch.object(backend.subprocess, "Popen", side_effect=fixture):
            with self.assertRaises(backend.BackendError) as caught:
                backend._run_remote({"operation": "delete"})
        self.assertTrue(caught.exception.uncertain)


if __name__ == "__main__":
    unittest.main()
