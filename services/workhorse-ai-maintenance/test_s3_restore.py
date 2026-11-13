"""Restore destination, overwrite and actual read-back integrity contracts."""

import hashlib
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from botocore.exceptions import ClientError

from s3_backup import add_bytes
import s3_restore


class RestoreContracts(unittest.TestCase):
    def fixture(self, directory, payload=b"abc"):
        archive = Path(directory) / "objects.tar"
        entry = {
            "bucket": "canary",
            "key": "folder/source page 1.json",
            "member": "objects/one",
            "sha256": hashlib.sha256(b"abc").hexdigest(),
            "bytes": 3,
            "headers": {"ContentType": "application/json"},
            "metadata": {"page": "1"},
            "tags": [],
        }
        with tarfile.open(archive, "w:") as tar:
            add_bytes(tar, entry["member"], payload)
            add_bytes(
                tar,
                "manifest.json",
                json.dumps({"buckets": ["canary"], "objects": [entry]}).encode(),
            )
            add_bytes(
                tar,
                "bucket-configurations.private.json",
                json.dumps(
                    {
                        "canary": {
                            "versioning": {},
                            "acl_private_owner_only": True,
                            "configs": {},
                        }
                    }
                ).encode(),
            )
            add_bytes(
                tar,
                "identity.private.json",
                b'{"identities":[{"credentials":[{"accessKey":"test-only","secretKey":"test-only"}]}]}',
            )
            add_bytes(tar, "source.private.json", b"{}")
        return archive, Path(directory) / "receipt.json"

    def test_public_or_credential_url_is_refused(self):
        for endpoint in [
            "https://8.8.8.8",
            "http://s3.example.com",
            "http://user:secret@10.0.0.1",
            "http://10.0.0.1/path",
        ]:
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                s3_restore.private_destination(endpoint)
        self.assertEqual(
            s3_restore.private_destination("http://10.0.0.1:8333/"),
            "http://10.0.0.1:8333",
        )

    @patch("s3_restore.client")
    def test_existing_bucket_is_never_overwritten(self, factory):
        storage = factory.return_value
        storage.list_buckets.return_value = {"Buckets": [{"Name": "canary"}]}
        with tempfile.TemporaryDirectory() as directory:
            archive, report = self.fixture(directory)
            with self.assertRaises(RuntimeError):
                s3_restore.restore(archive, "http://10.0.0.1:8333", report)
        storage.create_bucket.assert_not_called()
        storage.put_object.assert_not_called()

    @patch("s3_restore.client")
    def test_corrupt_archive_refused_before_network(self, factory):
        with tempfile.TemporaryDirectory() as directory:
            archive, report = self.fixture(directory, b"abX")
            with self.assertRaises(RuntimeError):
                s3_restore.restore(archive, "http://10.0.0.1:8333", report)
        factory.assert_not_called()

    @patch("s3_restore.client")
    def test_actual_readback_corruption_cannot_pass(self, factory):
        storage = factory.return_value
        storage.list_buckets.return_value = {"Buckets": []}
        storage.head_bucket.side_effect = ClientError(
            {
                "Error": {"Code": "NoSuchBucket"},
                "ResponseMetadata": {"HTTPStatusCode": 404},
            },
            "HeadBucket",
        )
        storage.get_bucket_versioning.return_value = {}
        storage.get_object.return_value = {
            "Body": io.BytesIO(b"abX"),
            "ContentType": "application/json",
            "Metadata": {"page": "1"},
        }
        storage.get_object_tagging.return_value = {"TagSet": []}
        with tempfile.TemporaryDirectory() as directory:
            archive, report = self.fixture(directory)
            with self.assertRaises(RuntimeError):
                s3_restore.restore(archive, "http://10.0.0.1:8333", report)
            self.assertFalse(report.exists())


if __name__ == "__main__":
    unittest.main()
