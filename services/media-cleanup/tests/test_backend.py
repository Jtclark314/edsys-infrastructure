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
