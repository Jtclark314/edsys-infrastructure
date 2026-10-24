"""Offline failure-contract checks; never touches Docker, media, SSH or Restic."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parents[1] / "edsys-immich-local.py"
SPEC = importlib.util.spec_from_file_location("immich_local", SOURCE)
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
MEDIA_UUID = "11111111-1111-4111-8111-111111111111"
NVME_UUID = "22222222-2222-4222-8222-222222222222"


class LocalImmichTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.media = self.base / "media"
        self.upload = self.media / "upload"
        self.internal = self.base / "internal"
        for path in [self.upload / "upload", self.upload / "library", self.upload / "profile",
                     self.internal / "immich/config"]:
            path.mkdir(parents=True, exist_ok=True)
        self.file = self.upload / "upload/photo.jpg"
        self.file.write_bytes(b"original")
        for name, value in [("MEDIA_ROOT", self.media), ("UPLOAD", self.upload),
                            ("LEGACY", self.media / "legacy-library"), ("INTERNAL_ROOT", self.internal)]:
            patch = mock.patch.object(m, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.rows = [{"Name": "/immich-server", "Mounts": [{"Type": "bind",
                      "Source": str(self.upload), "Destination": "/usr/src/app/upload"}]}]

    def test_expected_mount_uuid(self):
        value = {"filesystems": [{"target": str(self.media), "uuid": MEDIA_UUID, "fstype": "ext4"}]}
        with mock.patch.object(m, "command", return_value=json.dumps(value).encode()):
            m.mounted_uuid(self.media, MEDIA_UUID)

    def test_wrong_mount_uuid_refuses(self):
        value = {"filesystems": [{"target": str(self.media), "uuid": "wrong", "fstype": "ext4"}]}
        with mock.patch.object(m, "command", return_value=json.dumps(value).encode()):
            with self.assertRaisesRegex(m.Refused, "required_storage_uuid_missing"):
                m.mounted_uuid(self.media, MEDIA_UUID)

    def test_parent_mount_does_not_count_as_exact_mount(self):
        value = {"filesystems": [{"target": str(self.base), "uuid": MEDIA_UUID, "fstype": "ext4"}]}
        with mock.patch.object(m, "command", return_value=json.dumps(value).encode()):
            with self.assertRaises(m.Refused):
                m.mounted_uuid(self.media, MEDIA_UUID)

    def test_symlink_ancestor_refuses(self):
        link = self.base / "link"
        link.symlink_to(self.upload, target_is_directory=True)
        with self.assertRaisesRegex(m.Refused, "symlink_path"):
            m.safe(link / "upload/photo.jpg")

    def test_maps_original_inside_approved_root(self):
        self.assertEqual(self.file, m.map_reference("/usr/src/app/upload/upload/photo.jpg", self.rows))

    def test_traversal_reference_refuses(self):
        with self.assertRaisesRegex(m.Refused, "unexpected_original_reference"):
            m.map_reference("/usr/src/app/upload/upload/../profile/photo.jpg", self.rows)

    def test_derivative_cannot_satisfy_original_reference(self):
        (self.upload / "thumbs").mkdir()
        (self.upload / "thumbs/photo.jpg").write_bytes(b"thumbnail")
        with self.assertRaisesRegex(m.Refused, "reference_is_not_an_original"):
            m.map_reference("/usr/src/app/upload/thumbs/photo.jpg", self.rows)

    def test_unapproved_bind_source_refuses(self):
        rows = [{"Name": "/immich-server", "Mounts": [{"Type": "bind", "Source": str(self.base),
                 "Destination": "/usr/src/app/upload"}]}]
        with self.assertRaisesRegex(m.Refused, "unapproved_original_root"):
            m.map_reference("/usr/src/app/upload/upload/photo.jpg", rows)

    def test_missing_original_refuses(self):
        with self.assertRaisesRegex(m.Refused, "referenced_original_missing"):
            m.map_reference("/usr/src/app/upload/upload/missing.jpg", self.rows)

    def test_snapshot_must_contain_every_reference(self):
        refs = {"/photo.jpg": {"size": 8}, "/movie.mov": {"size": 12}}
        lines = [json.dumps({"path": "/photo.jpg", "type": "file", "size": 8})]
        with self.assertRaisesRegex(m.Refused, "snapshot_original_reference_missing"):
            m.verify_snapshot_entries(lines, refs)

    def test_snapshot_directory_cannot_substitute_for_file(self):
        with self.assertRaisesRegex(m.Refused, "snapshot_original_size_mismatch"):
            m.verify_snapshot_entries([json.dumps({"path": "/photo.jpg", "type": "dir", "size": 8})],
                                      {"/photo.jpg": {"size": 8}})

    def test_snapshot_file_size_must_match_checkpoint(self):
        with self.assertRaisesRegex(m.Refused, "snapshot_original_size_mismatch"):
            m.verify_snapshot_entries([json.dumps({"path": "/photo.jpg", "type": "file", "size": 7})],
                                      {"/photo.jpg": {"size": 8}})

    def test_newer_unreferenced_assets_do_not_invalidate_checkpoint(self):
        lines = [json.dumps({"path": "/photo.jpg", "type": "file", "size": 8}),
                 json.dumps({"path": "/new.jpg", "type": "file", "size": 10})]
        m.verify_snapshot_entries(lines, {"/photo.jpg": {"size": 8}})

    def test_generation_hash_detects_changed_database(self):
        root = self.base / "stage"
        generation = "20261024T203600Z-abcdef123456"
        directory = root / "local-generations" / generation
        directory.mkdir(parents=True)
        (root / "CURRENT_LOCAL.json").write_text(json.dumps({"current": generation}))
        dump = directory / "database.dump"
        dump.write_bytes(b"database")
        import hashlib
        (directory / "generation.json").write_text(json.dumps({"files": {"database.dump": {
            "bytes": 8, "sha256": hashlib.sha256(b"database").hexdigest()}}}))
        m.current_generation(root)
        dump.write_bytes(b"modified")
        with self.assertRaisesRegex(m.Refused, "local_generation_hash_mismatch"):
            m.current_generation(root)

    def test_failed_stage_cannot_reuse_previous_checkpoint(self):
        root = self.base / "stage"
        root.mkdir()
        (root / "status.json").write_text(json.dumps({"status": "failed"}))
        generation = root / "current"
        with mock.patch.object(m, "storage_guard"), mock.patch.object(m, "current_generation", return_value=(generation, {})):
            with self.assertRaisesRegex(m.Refused, "matching_fresh_database_stage_required"):
                m.backup(root, {})

    def test_storage_markers_are_selected_without_generated_payloads(self):
        thumbs = self.upload / "thumbs"
        thumbs.mkdir()
        marker = thumbs / ".immich"
        marker.write_bytes(b"")
        generated = thumbs / "preview.webp"
        generated.write_bytes(b"generated")
        selected = m.original_roots()
        self.assertIn(marker, selected)
        self.assertNotIn(thumbs, selected)
        self.assertNotIn(generated, selected)

    def test_policy_requires_two_explicit_valid_storage_identities(self):
        policy = {"format": 1, "enabled": True, "media_uuid": MEDIA_UUID,
                  "nvme_uuid": NVME_UUID, "backup_timeout_seconds": 14400}
        self.assertEqual(m.validate_policy(policy), policy)
        for key in ("media_uuid", "nvme_uuid"):
            missing = dict(policy)
            missing.pop(key)
            with self.subTest(key=key), self.assertRaisesRegex(m.Refused, "expected_storage_identity_missing"):
                m.validate_policy(missing)
            for invalid in ("", "<FILESYSTEM_UUID>", "any", MEDIA_UUID.upper()):
                # A UUID containing only digits is unchanged by upper(), so use
                # an alphabetic noncanonical fixture for that case instead.
                if invalid == MEDIA_UUID:
                    invalid = "ABCDEFAB-1234-4234-8234-ABCDEFABCDEF"
                with self.subTest(key=key, invalid=invalid), self.assertRaisesRegex(m.Refused, "expected_storage_identity_invalid"):
                    m.validate_policy({**policy, key: invalid})

    def test_same_storage_identity_cannot_satisfy_both_roles(self):
        with self.assertRaisesRegex(m.Refused, "separate_storage_identities_required"):
            m.validate_policy({"format": 1, "enabled": True, "media_uuid": MEDIA_UUID,
                               "nvme_uuid": MEDIA_UUID})

    def test_guard_compares_each_mount_to_its_private_policy(self):
        policy = {"format": 1, "enabled": True, "media_uuid": MEDIA_UUID, "nvme_uuid": NVME_UUID}
        with mock.patch.object(m, "mounted_uuid") as guard:
            m.storage_guard(policy)
        self.assertEqual(guard.call_args_list,
                         [mock.call(self.media, MEDIA_UUID), mock.call(self.internal, NVME_UUID)])


if __name__ == "__main__":
    unittest.main()
