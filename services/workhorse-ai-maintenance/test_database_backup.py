import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "database_backup", Path(__file__).with_name("database_backup.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PublicationTests(unittest.TestCase):
    def generation(self, root, name, content):
        path = root / name
        path.mkdir()
        (path / ".owner").write_text(module.OWNER)
        (path / "payload").write_text(content)
        return path

    def test_publish_keeps_current_and_previous_complete_generations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.generation(root, "previous", "oldest")
            self.generation(root, "current", "old")
            incoming = self.generation(root, ".incoming-test", "new")
            module.publish(incoming, root)
            self.assertEqual((root / "current/payload").read_text(), "new")
            self.assertEqual((root / "previous/payload").read_text(), "old")
            self.assertEqual({p.name for p in root.iterdir()}, {"current", "previous"})

    def test_unowned_existing_generation_is_preserved_on_refusal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            current = self.generation(root, "current", "original")
            (current / ".owner").write_text("someone-else")
            incoming = self.generation(root, ".incoming-test", "new")
            with self.assertRaises(RuntimeError):
                module.publish(incoming, root)
            self.assertEqual((current / "payload").read_text(), "original")
            self.assertTrue(incoming.exists())

    def test_symlink_generation_is_refused_without_touching_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = self.generation(root, "user-data", "original")
            (root / "current").symlink_to(target, target_is_directory=True)
            incoming = self.generation(root, ".incoming-test", "new")
            with self.assertRaises(RuntimeError):
                module.publish(incoming, root)
            self.assertEqual((target / "payload").read_text(), "original")


if __name__ == "__main__":
    unittest.main()
