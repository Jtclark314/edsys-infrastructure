"""Targeted checks for content fidelity and refusal of unsupported migrations."""

import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import Mock


SPEC = importlib.util.spec_from_file_location(
    "s3_migrate", Path(__file__).with_name("s3_migrate.py")
)
MIGRATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MIGRATION)


class Source:
    def __init__(self, status=None, grants=None):
        self.status = status
        self.grants = grants

    def get_bucket_versioning(self, **_kwargs):
        return {"Status": self.status} if self.status else {}

    def get_bucket_acl(self, **_kwargs):
        return {"Grants": self.grants or []}


class MigrationContracts(unittest.TestCase):
    def test_stream_copy_computes_actual_bytes_sha256(self):
        target = io.BytesIO()
        digest, size = MIGRATION.hash_stream(io.BytesIO(b"abc"), target)
        self.assertEqual(target.getvalue(), b"abc")
        self.assertEqual(size, 3)
        self.assertEqual(
            digest, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_versioned_and_suspended_sources_are_refused(self):
        for status in ("Enabled", "Suspended"):
            with self.assertRaises(RuntimeError):
                MIGRATION.source_configuration(Source(status=status), "sample")

    def test_non_private_bucket_acl_is_refused(self):
        grants = [{"Permission": "READ", "Grantee": {"Type": "Group", "URI": "public"}}]
        with self.assertRaises(RuntimeError):
            MIGRATION.source_configuration(Source(grants=grants), "sample")

    def test_metadata_or_header_change_is_distinct(self):
        self.assertNotEqual(
            MIGRATION.normalized(
                {"ContentType": "application/json", "Metadata": {"x": "one"}}
            ),
            MIGRATION.normalized(
                {"ContentType": "application/json", "Metadata": {"x": "two"}}
            ),
        )
        self.assertNotEqual(
            MIGRATION.normalized({"ContentType": "application/json"}),
            MIGRATION.normalized({"ContentType": "text/plain"}),
        )

    def test_unmigrated_notification_configuration_is_refused(self):
        source = Mock()
        source.get_bucket_versioning.return_value = {}
        source.get_bucket_acl.return_value = {
            "Grants": [
                {"Permission": "FULL_CONTROL", "Grantee": {"Type": "CanonicalUser"}}
            ]
        }
        source.get_bucket_notification_configuration.return_value = {
            "QueueConfigurations": [{"Id": "synthetic-only"}]
        }
        with self.assertRaisesRegex(RuntimeError, "Non-default bucket"):
            MIGRATION.source_configuration(source, "sample")


if __name__ == "__main__":
    unittest.main()
