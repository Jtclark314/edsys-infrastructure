import importlib.util
from pathlib import Path
import tempfile
import unittest


spec = importlib.util.spec_from_file_location("immich_guard", Path(__file__).with_name("guard.py"))
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class GuardTests(unittest.TestCase):
    def test_rejects_missing_wrong_or_read_only_mount(self):
        expected = {"target": "/mnt/photos", "uuid": "photo-uuid", "fstype": "ext4", "options": "rw,relatime"}
        guard.validate_mount(expected, "/mnt/photos", "photo-uuid")
        for field, value in (("target", "/"), ("uuid", "other"), ("fstype", "nfs4"), ("options", "ro,relatime")):
            with self.subTest(field=field), self.assertRaises(guard.Refused):
                guard.validate_mount({**expected, field: value}, "/mnt/photos", "photo-uuid")

    def test_rejects_symlinked_data_and_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "upload").mkdir()
            guard.validate_directory(root / "upload", root)
            (root / "alias").symlink_to(root / "upload", target_is_directory=True)
            with self.assertRaises(guard.Refused):
                guard.validate_directory(root / "alias", root)
            with self.assertRaises(guard.Refused):
                guard.validate_directory(root, root / "upload")

    def test_requires_existing_expected_postgres_cluster(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(guard.Refused):
                guard.validate_postgres_cluster(root)
            (root / "global").mkdir()
            (root / "global/pg_control").write_bytes(bytes(8192))
            (root / "PG_VERSION").write_text("14\n")
            guard.validate_postgres_cluster(root)
            (root / "PG_VERSION").write_text("18\n")
            with self.assertRaises(guard.Refused):
                guard.validate_postgres_cluster(root)
            (root / "PG_VERSION").unlink()
            (root / "PG_VERSION").symlink_to(root / "global/pg_control")
            with self.assertRaises(guard.Refused):
                guard.validate_postgres_cluster(root)


if __name__ == "__main__":
    unittest.main()
