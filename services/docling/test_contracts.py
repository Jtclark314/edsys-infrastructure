"""Regression checks for data preservation and privacy boundaries."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name(name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Contracts(unittest.TestCase):
    def test_model_or_output_restore_corruption_is_rejected(self):
        backup = load("backup")
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "sample").write_text("changed")
            (directory / "manifest.json").write_text(
                json.dumps({"files": {"sample": {"sha256": "0" * 64, "bytes": 7}}})
            )
            with self.assertRaises(RuntimeError):
                backup.verify(directory)

    def test_restore_path_escape_is_rejected(self):
        backup = load("backup")
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            (directory / "manifest.json").write_text(
                json.dumps({"files": {"../outside": {"sha256": "0" * 64, "bytes": 0}}})
            )
            with self.assertRaises(RuntimeError):
                backup.verify(directory)

    def test_network_guard_rejects_connect(self):
        converter = load("convert")
        with self.assertRaises(RuntimeError):
            converter.refuse_network("socket.connect", ())
        converter.refuse_network("open", ())

    def test_installer_refuses_unowned_existing_runtime(self):
        installer = load("install")
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(RuntimeError):
                installer.own_directory(Path(raw))


if __name__ == "__main__":
    unittest.main()
