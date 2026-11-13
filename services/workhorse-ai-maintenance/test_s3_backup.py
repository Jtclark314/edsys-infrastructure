"""Recovery archive corruption and path-preservation contracts."""

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

import s3_backup


class BackupContracts(unittest.TestCase):
    def archive(self, directory, payload=b"abc", member="objects/one", omit=None):
        path = Path(directory) / "objects.tar"
        manifest = {
            "buckets": ["sample"],
            "objects": [
                {
                    "member": member,
                    "sha256": hashlib.sha256(b"abc").hexdigest(),
                    "bytes": 3,
                }
            ],
        }
        with tarfile.open(path, "w:") as archive:
            s3_backup.add_bytes(archive, member, payload)
            s3_backup.add_bytes(archive, "manifest.json", json.dumps(manifest).encode())
            for name in (
                "identity.private.json",
                "bucket-configurations.private.json",
                "source.private.json",
            ):
                if name != omit:
                    s3_backup.add_bytes(archive, name, b"{}")
        return path

    def test_verified_archive_counts_actual_data(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                s3_backup.archive_verify(self.archive(directory)),
                {"objects": 1, "bytes": 3, "bucket_count": 1},
            )

    def test_corrupt_object_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                s3_backup.archive_verify(self.archive(directory, b"abX"))

    def test_missing_identity_refuses_otherwise_valid_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                s3_backup.archive_verify(
                    self.archive(directory, omit="identity.private.json")
                )

    def test_path_escape_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RuntimeError):
                s3_backup.archive_verify(self.archive(directory, member="../outside"))

    def test_stream_hash_tracks_partial_reads(self):
        reader = s3_backup.HashingReader(io.BytesIO(b"abc"))
        self.assertEqual(reader.read(1), b"a")
        self.assertEqual(reader.read(), b"bc")
        self.assertEqual(reader.count, 3)
        self.assertEqual(reader.digest.hexdigest(), hashlib.sha256(b"abc").hexdigest())


if __name__ == "__main__":
    unittest.main()
