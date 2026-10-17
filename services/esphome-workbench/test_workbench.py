"""Offline boundary checks; never invoke package installers or device tools."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import install
import tool_launcher
import verify_offline


class InstallationBoundaries(unittest.TestCase):
    def test_unowned_directory_is_not_adopted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(RuntimeError, "unowned"):
                install.own_root(root)
            self.assertFalse((root / ".edsys-owner").exists())

    def test_runtime_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            root.symlink_to(Path(directory), target_is_directory=True)
            with self.assertRaisesRegex(RuntimeError, "symlink"):
                install.own_root(root)

    def test_fresh_runtime_is_private_and_can_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "runtime"
            old_umask = os.umask(0o077)
            try:
                install.own_root(root)
                install.own_root(root)
            finally:
                os.umask(old_umask)
            self.assertEqual(root.stat().st_mode & 0o777, 0o700)
            self.assertEqual((root / ".edsys-owner").read_text(), install.MARKER)

    def test_launcher_collision_refused_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            (base / "edsys-pio").write_text("unrelated\n")
            with self.assertRaisesRegex(RuntimeError, "unrelated"):
                install.check_launcher_links(base, base / "runtime")
            self.assertEqual((base / "edsys-pio").read_text(), "unrelated\n")

    def test_only_exact_owned_launcher_link_is_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "runtime"
            (base / "edsys-pio").symlink_to(root / "bin/edsys-pio")
            install.check_launcher_links(base, root)
            (base / "edsys-esptool").symlink_to(base / "other")
            with self.assertRaises(RuntimeError):
                install.check_launcher_links(base, root)


class LockedClosureBoundaries(unittest.TestCase):
    LOCK = "esphome==2026.9.1\nrequests==2.34.2\nruamel-yaml==0.19.1\n"
    PACKAGES = [("esphome", "2026.9.1"), ("requests", "2.34.2"), ("ruamel.yaml", "0.19.1")]

    def test_exact_closure_accepts_canonical_package_names(self):
        install.validate_locked_packages(self.LOCK, self.PACKAGES)
        self.assertEqual(install.canonical_package_map([("Ruamel_YAML", "0.19.1")]), {"ruamel-yaml": "0.19.1"})

    def test_compatible_transitive_drift_is_rejected(self):
        # Core accepts requests==2.*, so this remains compatible but drifts.
        lock = "platformio==6.2.0\nrequests==2.34.2\n"
        installed = [("platformio", "6.2.0"), ("requests", "2.34.1")]
        with self.assertRaisesRegex(RuntimeError, "version_drift=\\['requests'\\]"):
            install.validate_locked_packages(lock, installed)

    def test_extraneous_and_missing_packages_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "extra=\\['unrelated-package'\\]"):
            install.validate_locked_packages(self.LOCK, self.PACKAGES + [("unrelated_package", "1.0")])
        with self.assertRaisesRegex(RuntimeError, "missing=\\['requests'\\]"):
            install.validate_locked_packages(self.LOCK, [pair for pair in self.PACKAGES if pair[0] != "requests"])

    def test_duplicate_distributions_and_partial_lock_parsing_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "Duplicate"):
            install.validate_locked_packages(self.LOCK, self.PACKAGES + [("Ruamel_YAML", "0.19.1")])
        with self.assertRaisesRegex(RuntimeError, "Unsupported"):
            install.validate_locked_packages("requests>=2\n", [("requests", "2.34.2")])


class LauncherBoundaries(unittest.TestCase):
    def fixture(self, root):
        environments = {}
        for name in ("esphome", "platformio"):
            relative = Path("venvs") / name
            python = root / relative / "bin/python"
            python.parent.mkdir(parents=True)
            python.write_text("offline test placeholder")
            environments[name] = {"path": str(relative)}
        (root / "install.json").write_text(json.dumps({"environments": environments}))

    def test_separate_engines_private_state_and_two_job_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            inherited = {"PATH": "/usr/bin", "PYTHONPATH": "/unrelated", "PYTHONHOME": "/other", "VIRTUAL_ENV": "/global", "PLATFORMIO_PACKAGES_DIR": "/global/packages", "PLATFORMIO_CORE_DIR": "/global", "PLATFORMIO_RUN_JOBS": "64"}
            for launcher, (key, module) in tool_launcher.TOOLS.items():
                argv, env = tool_launcher.launch_spec(root, launcher, ["--version"], inherited)
                expected_args = ["--host", "127.0.0.1", "--version"] if launcher == "edsys-espbuilder" else ["--version"]
                self.assertEqual(argv, [str(root / "venvs" / key / "bin/python"), "-I", "-m", module, *expected_args])
                self.assertNotIn("PYTHONPATH", env)
                self.assertNotIn("PYTHONHOME", env)
                self.assertNotIn("VIRTUAL_ENV", env)
                self.assertNotIn("PLATFORMIO_PACKAGES_DIR", env)
                self.assertEqual(env["PLATFORMIO_RUN_JOBS"], "2")
                self.assertEqual(env["ESPHOME_DEFAULT_COMPILE_PROCESS_LIMIT"], "2")
                self.assertTrue(env["PLATFORMIO_CORE_DIR"].startswith(str(root / "state")))
                self.assertEqual(env["PLATFORMIO_SETTING_ENABLE_TELEMETRY"], "false")
            self.assertEqual(inherited["PLATFORMIO_RUN_JOBS"], "64")

    def test_manifest_escape_and_missing_environment_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            for path in ("/tmp/unowned", "venvs/../../unowned", "other/env"):
                (root / "install.json").write_text(json.dumps({"environments": {"esphome": {"path": path}}}))
                with self.assertRaisesRegex(ValueError, "Invalid environment"):
                    tool_launcher.launch_spec(root, "edsys-esphome", [], {})
            (root / "install.json").write_text(json.dumps({"environments": {"esphome": {"path": "venvs/absent"}}}))
            with self.assertRaisesRegex(ValueError, "missing"):
                tool_launcher.launch_spec(root, "edsys-esphome", [], {})

    def test_argv_is_forwarded_without_shell_interpretation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.fixture(root)
            args = ["config", "a config;$(nothing).yaml"]
            argv, _ = tool_launcher.launch_spec(root, "edsys-esphome", args, {})
            self.assertEqual(argv[-2:], args)


class OfflineGuardBoundaries(unittest.TestCase):
    def guard(self):
        namespace = {}
        exec(verify_offline.GUARD.split("sys.addaudithook(guard)")[0], namespace)
        return namespace["guard"]

    def test_network_and_serial_attempts_are_refused(self):
        guard = self.guard()
        with contextlib.redirect_stderr(io.StringIO()):
            for event, args in (("socket.connect", (None, ("192.0.2.1", 80))),
                                ("socket.bind", (None, ("127.0.0.1", 6052))),
                                ("subprocess.Popen", ("esptool", ["esptool", "chip-id"])),
                                ("open", ("/dev/ttyUSB0", "r")),
                                ("os.listdir", ("/dev",))):
                with self.assertRaises(RuntimeError):
                    guard(event, args)

    def test_ipv6_import_probe_is_prevented_with_normal_fallback(self):
        with self.assertRaises(OSError):
            self.guard()("socket.bind", (None, ("::1", 0)))

    def test_only_named_local_metadata_subprocesses_are_allowed(self):
        guard = self.guard()
        guard("subprocess.Popen", ("uname", ["uname", "-p"]))
        guard("subprocess.Popen", ("xdg-user-dir", ["xdg-user-dir", "DOCUMENTS"]))
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(RuntimeError):
                guard("subprocess.Popen", ("uname", ["uname", "--anything-else"]))


if __name__ == "__main__":
    unittest.main()
