"""Offline contract checks; no production sources, Docker, SSH or backups."""

import importlib.util
import copy
import io
import json
from pathlib import Path
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1] / "edsys-image-originals.py"
SPEC = importlib.util.spec_from_file_location("image_originals", SOURCE)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)


class OriginalsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.images = self.base / "images"
        self.images.mkdir(mode=0o700)
        (self.images / "one.jpg").write_bytes(b"original one")
        (self.images / "duplicate.jpg").write_bytes(b"original one")
        (self.images / "original.mp4").write_bytes(b"original video")
        self.root = self.base / "stage"
        self.args = SimpleNamespace(stage_root=str(self.root), image_root=str(self.images),
                                    ssh_target="fixture", ssh_key="fixture", known_hosts="fixture")
        m.END = time.monotonic() + 900

    def images_archive(self, path):
        before = m.inventory({"images": self.images})
        with path.open("wb") as f:
            archive = m.Archive(f, "images", {"files": len(before)})
            for name, value in sorted(before.items()):
                archive.add(name, Path(value["source"]), value)
            archive.finish()

    def fake_ssh(self, _args, output=None, preflight=False):
        if preflight:
            return json.dumps({"assets": 1, "original_bytes": 16,
                               "database_bytes": 16}).encode()
        archive = m.Archive(output, "immich", {"assets": 1})
        for name in ("database.dump", "roles.sql"):
            archive.add(name, self.images / "one.jpg")
        archive.json("recovery-config.json", {"fixture": True})
        archive.json("checkpoint.json", {"assets": [{"id": "fixture"}]})
        archive.add("originals/upload/original.mp4", self.images / "original.mp4")
        archive.finish()
        return b""

    def successful_stage(self):
        with mock.patch.object(m, "ssh_run", side_effect=self.fake_ssh):
            return m.stage(self.args)

    def test_images_roundtrip_preserves_duplicate_paths_and_original_video(self):
        path = self.base / "images.tar"
        self.images_archive(path)
        proof = m.verify_archive(path, "images")
        self.assertEqual(proof["members"], 3)
        with tarfile.open(path) as archive:
            self.assertIn("images/original.mp4", archive.getnames())
        self.assertEqual(len(list(self.images.iterdir())), 3)

    def test_source_file_symlink_refused(self):
        (self.images / "link.jpg").symlink_to(self.images / "one.jpg")
        with self.assertRaisesRegex(m.Refused, "nonregular_original"):
            m.inventory({"images": self.images})

    def test_source_directory_symlink_refused(self):
        (self.images / "directory").symlink_to(self.base, target_is_directory=True)
        with self.assertRaisesRegex(m.Refused, "symlink_original_directory"):
            m.inventory({"images": self.images})

    def test_source_changed_since_inventory_refused(self):
        p = self.images / "one.jpg"
        before = m.metadata(p)
        p.write_bytes(b"changed")
        archive = m.Archive(io.BytesIO(), "images", {})
        with self.assertRaisesRegex(m.Refused, "source_changed_before_copy"):
            archive.add("images/one.jpg", p, before)
        archive.tar.close()

    def test_source_mutated_during_read_refused(self):
        p = self.images / "one.jpg"
        original_read = m.HashReader.read
        def changed(reader, size=-1):
            data = original_read(reader, size)
            p.write_bytes(b"mutated after read")
            return data
        archive = m.Archive(io.BytesIO(), "images", {})
        with mock.patch.object(m.HashReader, "read", changed):
            with self.assertRaisesRegex(m.Refused, "source_changed_during_copy"):
                archive.add("images/one.jpg", p)
        archive.tar.close()

    def test_tar_payload_corruption_refused(self):
        path = self.base / "images.tar"
        self.images_archive(path)
        with tarfile.open(path) as archive:
            offset = archive.getmembers()[0].offset_data
        with path.open("r+b") as f:
            f.seek(offset)
            f.write(b"X")
        with self.assertRaisesRegex(m.Refused, "archive_hash_mismatch"):
            m.verify_archive(path, "images")

    def test_traversal_member_refused_without_extraction(self):
        path = self.base / "bad.tar"
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("../../outside")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(m.Refused, "unsafe_archive_member"):
            m.verify_archive(path, "images")
        self.assertFalse((self.base.parent / "outside").exists())

    def test_incomplete_remote_stream_refused(self):
        path = self.base / "bad.tar"
        with tarfile.open(path, "w") as archive:
            info = tarfile.TarInfo("database.dump")
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
        with self.assertRaisesRegex(m.Refused, "missing_or_inconsistent_manifest"):
            m.verify_archive(path, "immich")

    def test_wrong_archive_kind_refused(self):
        path = self.base / "images.tar"
        self.images_archive(path)
        with self.assertRaisesRegex(m.Refused, "missing_or_inconsistent_manifest"):
            m.verify_archive(path, "immich")

    def test_immich_requires_dump_roles_configuration_and_checkpoint(self):
        path = self.base / "bad.tar"
        with path.open("wb") as f:
            archive = m.Archive(f, "immich", {"assets": 1})
            archive.add("only-original.jpg", self.images / "one.jpg")
            archive.finish()
        with self.assertRaisesRegex(m.Refused, "required_recovery_member_missing"):
            m.verify_archive(path, "immich")

    def test_complete_stage_independently_verifies_both_archives(self):
        result = self.successful_stage()
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(m.verify_stage(self.root)["immich_assets"], 1)
        self.assertEqual(m.verify_stage(self.root)["images"], 3)
        self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_failed_refresh_preserves_current_and_reports_failed_freshness(self):
        self.successful_stage()
        pointer = (self.root / "CURRENT.json").read_bytes()
        def fail(_args, output=None, preflight=False):
            if preflight:
                return self.fake_ssh(_args, preflight=True)
            output.write(b"partial stream")
            raise m.Refused("fixture_remote_failure")
        with mock.patch.object(m, "ssh_run", side_effect=fail):
            with self.assertRaises(m.Refused):
                m.stage(self.args)
        self.assertEqual(pointer, (self.root / "CURRENT.json").read_bytes())
        health = json.loads((self.root / "status.json").read_text())
        self.assertEqual(health["status"], "failed")
        self.assertIsNotNone(health["last_success_utc"])
        self.assertEqual(list((self.root / ".incoming").iterdir()), [])
        self.assertEqual(m.verify_stage(self.root)["images"], 3)

    def test_concurrent_local_source_change_blocks_publication(self):
        self.successful_stage()
        pointer = (self.root / "CURRENT.json").read_bytes()
        def changed(args, output=None, preflight=False):
            result = self.fake_ssh(args, output, preflight)
            if not preflight:
                (self.images / "new-upload.jpg").write_bytes(b"new original")
            return result
        with mock.patch.object(m, "ssh_run", side_effect=changed):
            with self.assertRaisesRegex(m.Refused, "image_tree_changed_before_publication"):
                m.stage(self.args)
        self.assertEqual(pointer, (self.root / "CURRENT.json").read_bytes())

    def test_empty_source_cannot_replace_last_good(self):
        self.successful_stage()
        pointer = (self.root / "CURRENT.json").read_bytes()
        for p in self.images.iterdir():
            p.unlink()
        with mock.patch.object(m, "ssh_run") as ssh:
            with self.assertRaisesRegex(m.Refused, "empty_image_source_requires_review"):
                m.stage(self.args)
        ssh.assert_not_called()
        self.assertEqual(pointer, (self.root / "CURRENT.json").read_bytes())

    def test_disk_reserve_failure_precedes_copy_and_publication(self):
        with mock.patch.object(m, "ssh_run", side_effect=self.fake_ssh):
            with mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=0)):
                with self.assertRaisesRegex(m.Refused, "insufficient_space"):
                    m.stage(self.args)
        self.assertFalse((self.root / "CURRENT.json").exists())

    def test_staging_retains_two_generations_without_deleting_originals(self):
        results = [self.successful_stage() for _ in range(3)]
        names = {p.name for p in (self.root / "generations").iterdir()}
        self.assertEqual(names, {r["generation"] for r in results[-2:]})
        self.assertEqual(len(list(self.images.iterdir())), 3)

    def test_unsafe_old_generation_does_not_change_current(self):
        self.successful_stage()
        pointer = (self.root / "CURRENT.json").read_bytes()
        bad = self.root / "generations" / "20260101T000000Z-000000000001"
        bad.symlink_to(self.images, target_is_directory=True)
        with mock.patch.object(m, "ssh_run", side_effect=self.fake_ssh):
            with self.assertRaisesRegex(m.Refused, "symlink_path"):
                m.stage(self.args)
        self.assertEqual(pointer, (self.root / "CURRENT.json").read_bytes())
        self.assertEqual(len(list(self.images.iterdir())), 3)

    def test_current_pointer_cannot_escape_generation_root(self):
        self.root.mkdir(mode=0o700)
        (self.root / "CURRENT.json").write_text(json.dumps({"current": "../../outside"}))
        with self.assertRaisesRegex(m.Refused, "invalid_current_pointer"):
            m.read_current(self.root)

    def test_shared_exported_snapshot_dump_and_reference_guard(self):
        upload = self.base / "immich"
        for directory in ("upload", "library", "profile"):
            (upload / directory).mkdir(parents=True)
        original = upload / "upload" / "one.mp4"
        original.write_bytes(b"original video")
        legacy = self.base / "legacy"
        legacy.mkdir()
        assets = [{"id": "fixture", "path": "/usr/src/app/upload/upload/one.mp4", "type": "VIDEO"}]
        rows = [{"Name": "/immich-server", "Image": "sha256:fixture", "Config": {},
                 "Mounts": [{"Type": "bind", "Source": str(upload),
                              "Destination": "/usr/src/app/upload"}]}]
        state = {"open": True, "snapshot_dump_used": False}
        class Snapshot:
            snapshot = "00000003-0000004A-1"
            def __init__(self, _db):
                pass
            def line(self, sql):
                if "pg_database_size" in sql:
                    return "16"
                if sql == m.LIBRARIES_SQL:
                    return "0"
                if sql == m.ASSETS_SQL:
                    return json.dumps(assets)
                return "1"
            def close(self):
                state["open"] = False
        def command(args, output=None, **_kwargs):
            if "pg_dump" in args:
                self.assertTrue(state["open"])
                self.assertEqual(args[args.index("--snapshot") + 1], Snapshot.snapshot)
                state["snapshot_dump_used"] = True
                output.write(b"custom dump fixture")
            if "pg_dumpall" in args:
                return b"roles fixture"
            if m.ASSETS_SQL in args:
                return json.dumps(assets).encode()
            if m.LIBRARIES_SQL in args:
                return b"0"
            return b""
        path = self.base / "immich.tar"
        with mock.patch.object(m, "IMMICH_ROOT", upload), \
                mock.patch.object(m, "LEGACY_LIBRARY", legacy), \
                mock.patch.object(m, "docker_inspect", return_value=(rows, {"user": "fixture", "database": "fixture"})), \
                mock.patch.object(m, "DatabaseSnapshot", Snapshot), \
                mock.patch.object(m, "application_config", return_value={"fixture": True}), \
                mock.patch.object(m, "command", side_effect=command):
            with path.open("wb") as output:
                m.remote_worker(output)
        self.assertTrue(state["snapshot_dump_used"])
        self.assertFalse(state["open"])
        self.assertEqual(m.verify_archive(path, "immich")["details"]["assets"], 1)

    def test_remote_reference_outside_reviewed_mounts_refused(self):
        rows = [{"Name": "/immich-server", "Mounts": []}]
        with self.assertRaisesRegex(m.Refused, "unmapped_original_reference"):
            m.reference_files(rows, [{"path": "/unexpected/private.jpg"}])

    def test_archive_size_bound_enforced(self):
        with mock.patch.object(m, "MAX_ARCHIVE", 1):
            with self.assertRaisesRegex(m.Refused, "archive_size_limit"):
                m.BoundedWriter(io.BytesIO()).write(b"too large")

    def test_command_capture_does_not_expose_raw_failure_output(self):
        with self.assertRaisesRegex(m.Refused, "^read_command_failed$"):
            m.command(["/bin/sh", "-c", "printf private >&2; exit 7"])

    def test_snapshot_initialization_failure_closes_process_and_pipes(self):
        process = mock.Mock()
        process.poll.return_value = None
        process.stdin, process.stdout = io.BytesIO(), io.BytesIO(b"not a snapshot\n")
        with mock.patch.object(m.subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(m.Refused, "invalid_database_snapshot"):
                m.DatabaseSnapshot({"user": "fixture", "database": "fixture"})
        process.wait.assert_called_once()
        self.assertTrue(process.stdin.closed)
        self.assertTrue(process.stdout.closed)

    def test_remote_diagnostics_allow_only_fixed_identifiers(self):
        self.assertEqual(m.remote_reason(b'{"status":"failed","reason":"postgres_dump_failed"}\n'),
                         'remote_postgres_dump_failed')
        for content in [b'private raw stderr', b'{"status":"failed","reason":"private/path"}',
                        b'{"status":"failed","reason":"https://private.example/secret"}',
                        b'[]', b'{"reason":42}', b'{"status":"failed","reason":null}']:
            self.assertEqual(m.remote_reason(content), 'remote_collection_failed')

    def test_mount_response_order_is_ignored_but_configuration_changes_are_not(self):
        original = [{"Name": "/server", "Image": "sha256:fixture", "Config": {"Env": ["KEY=value"]},
                     "Mounts": [{"Destination": "/data", "Source": "/originals", "RW": True},
                                {"Destination": "/other", "Source": "/other", "RW": False}]}]
        reordered = copy.deepcopy(original)
        reordered[0]["Mounts"].reverse()
        self.assertEqual(m.configuration_snapshot(original), m.configuration_snapshot(reordered))
        reordered[0]["Mounts"][0]["RW"] = True
        self.assertNotEqual(m.configuration_snapshot(original), m.configuration_snapshot(reordered))
        changed_env = copy.deepcopy(original)
        changed_env[0]["Config"]["Env"] = ["KEY=changed"]
        self.assertNotEqual(m.configuration_snapshot(original), m.configuration_snapshot(changed_env))


if __name__ == "__main__":
    unittest.main()
