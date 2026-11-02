"""Offline failure-path tests. No live Docker, Restic, credentials or services."""

import dataclasses
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("private_backup", Path(__file__).with_name("backup.py"))
backup = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = backup
spec.loader.exec_module(backup)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.paths = backup.Paths(**{field.name: self.root / field.name for field in dataclasses.fields(backup.Paths)})
        self.paths.stage.mkdir()
        self.paths.runtime.mkdir()
        self.image_root = self.paths.runtime / "images"
        self.image_root.mkdir()
        (self.image_root / "stirling-pdf.tar").write_bytes(b"isolated-image-fixture-one")
        (self.image_root / "changedetection.tar").write_bytes(b"isolated-image-fixture-two")
        self.image_ids = {app: "sha256:" + digit * 64
                          for app, digit in zip(backup.APPS, ("b", "c"))}
        self.image_index = [{"file": app + ".tar", "image_id": self.image_ids[app],
                             "sha256": backup.hash_file(self.image_root / (app + ".tar")),
                             "size": (self.image_root / (app + ".tar")).stat().st_size}
                            for app in backup.APPS]
        backup.atomic_json(self.image_root / "archives.json", self.image_index)
        stirling = self.paths.runtime / "stirling-pdf/configs"
        stirling.mkdir(parents=True)
        (stirling / "settings.yml").write_text("system:\n  enableAnalytics: false\n")
        for directory in ("signatures", "templates", "pipeline"):
            (self.paths.runtime / "stirling-pdf" / directory).mkdir()
        changedetection = self.paths.runtime / "changedetection"
        changedetection.mkdir()
        (changedetection / "changedetection.json").write_text(json.dumps({
            "app_guid": "offline-fixture-guid", "build_sha": None, "version_tag": None,
            "note": "Settings file; watches are in separate UUID directories",
            "settings": {"application": {
                "api_access_token_enabled": True, "api_access_token": "offline-fixture-only"}}}))
        self.identifier = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def fake_repository(self):
        return mock.Mock(environment={"RESTIC_REPOSITORY": str(self.root / "repository")})

    def recovery_artifacts(self):
        files = [self.paths.runtime / "deploy/stirling-pdf.env",
                 self.paths.runtime / "deploy/changedetection.env", self.paths.runtime / "proxy.json",
                 self.paths.mount_guard,
                 self.paths.user_units / "edsys-private-web-apps-proxy.service"]
        files += [self.paths.runtime / "deploy" / name for name in (
            backup.MCP_KEY, backup.MCP_REGISTRATION, backup.MCP_INSTALL)]
        files += [self.paths.system_units / name for name in (
            "edsys-private-web-apps.service", "edsys-private-web-apps-backup.service",
            "edsys-private-web-apps-backup.timer")]
        files += [self.paths.libexec / name for name in (
            "edsys-private-web-apps-control", "edsys-private-web-apps-mount-check",
            "edsys-private-web-apps-backup")]
        files += [self.paths.source / relative for relative in (
            "docker/stirling-pdf/compose.yaml", "docker/changedetection/compose.yaml",
            "docker/changedetection/initialize.py", "docker/changedetection/healthcheck.py",
            "services/private-web-apps/backup.py", "services/private-web-apps/control.py",
            "services/stirling-mcp/server.py", "services/stirling-mcp/bridge.py")]
        files += [self.paths.source / "services/changedetection-mcp" / name for name in backup.MCP_REQUIRED_FILES]
        files += [self.paths.source / "docker/changedetection" / name for name in backup.CHANGEDETECTION_SOURCE_FILES]
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("offline-recovery-source-fixture\n")

    def payload(self, path=None):
        path = path or self.root / "payload"
        path.mkdir(mode=0o700, parents=True)
        apps = {}
        for app in backup.APPS:
            backup.make_archive(path / (app + ".tar"), [(self.paths.runtime / app, ".")], seconds=10)
            apps[app] = {"image_id": self.image_ids[app],
                         "members": backup.archive_inventory(path / (app + ".tar"), app=app)}
        recovery = self.root / "recovery-fixture"
        recovery.mkdir(exist_ok=True)
        (recovery / "compose.yaml").write_text("services: {}\n")
        backup.make_archive(path / "recovery.tar", [(recovery, "source")], seconds=10)
        manifest = {"schema": 1, "scope": backup.SCOPE, "apps": apps,
                    "images": backup.image_inventory(self.image_root),
                    "recovery_members": backup.archive_inventory(path / "recovery.tar"),
                    "archives": {name: backup.file_record(path / name, name)
                                 for name in backup.PAYLOAD_FILES - {"manifest.json"}}}
        backup.atomic_json(path / "manifest.json", manifest)
        return path

    def simulated_docker(self, *, running=True, stop_error=False, start_error=False):
        state = {"running": running, "events": []}
        def inspect(_identifier, app, paths):
            state["events"].append("inspect")
            return {"Id": self.identifier, "Image": "sha256:" + "b" * 64,
                    "State": {"Running": state["running"]}}
        def run(command, **kwargs):
            if "stop" in command:
                state["events"].append("stop")
                if stop_error:
                    raise backup.BackupError("container_stop_failed")
                state["running"] = False
            elif "start" in command:
                state["events"].append("start")
                if start_error:
                    raise backup.BackupError("container_restart_failed")
                state["running"] = True
            else:
                state["events"].append("mount-check")
            return b""
        return state, inspect, run


class ExpandedFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.recovery_artifacts()
        self.real_run = backup.run
        self.compose = {"name": "edsys-changedetection", "services": {}}
        self.containers = {}
        self.config_hashes = {}
        for number, (service, name) in enumerate({"changedetection": backup.APPS["changedetection"],
                                                 **backup.SIDECARS}.items()):
            if service != "changedetection":
                self.image_ids[service] = "sha256:" + str(number + 3) * 64
                path = self.image_root / (service + ".tar")
                path.write_bytes(("offline-" + service + "-image").encode())
                self.image_index.append({"file": path.name, "image_id": self.image_ids[service],
                                         "sha256": backup.hash_file(path), "size": path.stat().st_size})
            mounts = []
            if service == "changedetection":
                mounts = [{"type": "bind", "source": str(self.paths.runtime / "changedetection"),
                           "target": "/datastore", "read_only": False}]
                mounts += [{"type": "bind", "source": str(self.paths.source / "docker/changedetection" / filename),
                            "target": "/opt/edsys/" + filename, "read_only": True}
                           for filename in ("initialize.py", "healthcheck.py")]
            self.compose["services"][service] = {"image": self.image_ids[service], "container_name": name,
                                                  "volumes": mounts}
            self.config_hashes[service] = backup.hashlib.sha256(service.encode()).hexdigest()
            self.containers[service] = {
                "Name": "/" + name, "Id": str(number + 6) * 64, "Image": self.image_ids[service],
                "Config": {"Image": self.image_ids[service], "Labels": {
                    "com.docker.compose.project": "edsys-changedetection", "com.docker.compose.service": service,
                    "com.docker.compose.config-hash": self.config_hashes[service]}},
                "State": {"Running": True},
                "Mounts": [{"Type": "bind", "Source": item["source"], "Destination": item["target"],
                            "RW": not item["read_only"]} for item in mounts]}
        backup.atomic_json(self.image_root / "archives.json", self.image_index)
        (self.paths.source / "docker/changedetection/compose.yaml").write_text(backup.yaml.safe_dump(self.compose))
        self.write_image_lock()
        release_name = "20261102T000000Z-0.1.0"
        self.release = self.paths.changedetection_mcp / "releases" / release_name
        self.release.mkdir(parents=True)
        (self.paths.changedetection_mcp / "current").symlink_to(self.release, target_is_directory=True)
        hashes = {}
        for filename in backup.MCP_RUNTIME_FILES:
            source = self.paths.source / "services/changedetection-mcp" / filename
            shutil.copy2(source, self.release / filename)
            hashes[filename] = backup.hash_file(source)
        (self.release / "venv/bin").mkdir(parents=True)
        (self.release / "venv/bin/python").write_text("offline-interpreter-fixture")
        (self.release / "venv/pyvenv.cfg").write_text("home = /usr/bin\n")
        archive = self.image_root / "changedetection-mcp-runtime-0.1.0.tar.zst"
        archive.write_bytes(b"offline-qualified-runtime-archive")
        self.runtime_receipt = {"kind": "isolated-python-runtime", "file": archive.name,
            "sha256": backup.hash_file(archive), "size": archive.stat().st_size,
            "python_version": "offline-fixture", "source_commit": "a" * 40, "release": release_name,
            "restore_parent": str(self.paths.changedetection_mcp.parent), "source_hashes": hashes}
        backup.atomic_json(self.image_root / backup.MCP_RECEIPT, self.runtime_receipt)
        before = self.paths.runtime / "deploy/codex-config-before-changedetection-20261102T000000Z.toml"
        before.write_text('model = "offline-fixture"\n')
        before.chmod(0o600)
        backup.atomic_json(self.paths.runtime / "deploy" / backup.MCP_INSTALL, {
            "release": release_name, "source_commit": "a" * 40, "new_server": backup.MCP_SERVER,
            "preserved_all_existing_settings": True, "before_config_file": before.name,
            "before_sha256": backup.hash_file(before)})
        key = self.paths.runtime / "deploy" / backup.MCP_KEY
        key.write_text("offline-fixture-only\n")
        key.chmod(0o600)
        registration = ('[mcp_servers.edsys-changedetection]\n'
            f'command = {json.dumps(str(self.paths.changedetection_mcp / "current/run.sh"))}\n'
            '[mcp_servers.edsys-changedetection.env]\n'
            f'CHANGEDETECTION_BASE_URL = {json.dumps(backup.MCP_BASE_URL)}\n'
            f'CHANGEDETECTION_API_KEY_FILE = {json.dumps(str(key))}\n'
            f'CHANGEDETECTION_ARTIFACT_DIR = {json.dumps(str(self.root / "mcp-output"))}\n')
        (self.paths.runtime / "deploy" / backup.MCP_REGISTRATION).write_text(registration)

    def write_image_lock(self):
        self.image_lock = {"schema_version": 1, "images": {
            role: {"image_id": self.image_ids[service], "producer": "offline-fixture"}
            for role, service in (("app", "changedetection"), ("browser", "browser"), ("egress", "egress"))},
            "source_sha256": {name: backup.hash_file(self.paths.source / "docker/changedetection" / name)
                              for name in backup.CHANGEDETECTION_BUILD_FILES}}
        backup.atomic_json(self.paths.source / "docker/changedetection/images.lock.json", self.image_lock)

    def docker_read(self, command, **kwargs):
        if command[0] == "tar":
            return self.real_run(command, **kwargs)
        self.assertEqual(command[:3], ["docker", "--host", "unix:///var/run/docker.sock"])
        if command[3] == "compose":
            if "--hash" in command:
                return "".join(f"{name} {digest}\n" for name, digest in self.config_hashes.items()).encode()
            return json.dumps(self.compose).encode()
        if command[3:5] == ["image", "inspect"]:
            for name, definition in self.compose["services"].items():
                if command[-1] == definition["image"]:
                    return self.image_ids[name].encode()
        if command[3] == "inspect":
            return json.dumps(list(self.containers.values())).encode()
        self.fail("Unexpected Docker operation in read-only deployment check")

    def deployment(self):
        with mock.patch.object(backup, "run", side_effect=self.docker_read):
            return backup.deployment_snapshot(self.paths)

    def capture(self, app, target, paths):
        backup.make_archive(target, [(paths.runtime / app, ".")], seconds=10)
        return {"image_id": self.image_ids[app], "members": backup.archive_inventory(target, app=app)}

    def payload(self, path=None):
        path = super().payload(path)
        backup.make_archive(path / "recovery.tar", backup.recovery_sources(self.paths), seconds=10)
        manifest = json.loads((path / "manifest.json").read_text())
        manifest.update(schema=2, deployment=self.deployment(), mcp=backup.mcp_snapshot(self.paths, manifest["images"]),
                        recovery_members=backup.archive_inventory(path / "recovery.tar"))
        manifest["archives"]["recovery.tar"] = backup.file_record(path / "recovery.tar", "recovery.tar")
        backup.atomic_json(path / "manifest.json", manifest)
        return path


class VerificationTests(Fixture):
    def test_changedetection_06010_settings_without_watching_are_accepted(self):
        data = json.loads((self.paths.runtime / "changedetection/changedetection.json").read_text())
        self.assertNotIn("watching", data)
        backup.verify_stage(self.payload(), self.image_root)

    def test_changedetection_missing_or_malformed_application_settings_are_refused(self):
        for number, data in enumerate(([], {}, {"settings": []}, {"settings": {"application": []}})):
            with self.subTest(number=number):
                (self.paths.runtime / "changedetection/changedetection.json").write_text(json.dumps(data))
                with self.assertRaisesRegex(backup.BackupError, "changedetection_store_invalid"):
                    self.payload(self.root / f"bad-settings-{number}")

    def test_changedetection_private_api_must_be_enabled_with_a_nonempty_string(self):
        settings_file = self.paths.runtime / "changedetection/changedetection.json"
        data = json.loads(settings_file.read_text())
        settings = data["settings"]["application"]
        for number, (enabled, token) in enumerate(((False, "fixture"), ("true", "fixture"),
                                                  (True, ""), (True, "  "), (True, None),
                                                  (True, 42), (True, {"invalid": "fixture"}))):
            with self.subTest(number=number):
                settings.update(api_access_token_enabled=enabled, api_access_token=token)
                settings_file.write_text(json.dumps(data))
                with self.assertRaisesRegex(backup.BackupError, "changedetection_private_api_state_missing"):
                    self.payload(self.root / f"bad-api-{number}")

    def test_changedetection_separate_watch_files_are_captured(self):
        watch = self.paths.runtime / "changedetection/00000000-0000-4000-8000-000000000001/watch.json"
        watch.parent.mkdir()
        watch.write_text(json.dumps({"url": "https://example.invalid/offline-fixture"}))
        payload = self.payload()
        members = backup.archive_inventory(payload / "changedetection.tar", app="changedetection")
        self.assertEqual(next(item["sha256"] for item in members if item["path"].endswith("/watch.json")),
                         backup.hash_file(watch))
        backup.verify_stage(payload, self.image_root)

    def test_stirling_settings_must_be_a_yaml_mapping(self):
        (self.paths.runtime / "stirling-pdf/configs/settings.yml").write_text("- unexpected list\n")
        with self.assertRaisesRegex(backup.BackupError, "stirling_settings_invalid"):
            self.payload()

    def test_stirling_persistent_directories_must_be_present(self):
        shutil.rmtree(self.paths.runtime / "stirling-pdf/signatures")
        with self.assertRaisesRegex(backup.BackupError, "stirling_persistent_directory_missing"):
            self.payload()

    def test_empty_optional_custom_settings_are_valid(self):
        (self.paths.runtime / "stirling-pdf/configs/custom_settings.yml").touch()
        backup.verify_stage(self.payload(), self.image_root)

    def test_completed_pdfs_templates_and_signatures_are_protected(self):
        expected = {}
        for name in ("pipeline/finished/result.pdf", "templates/input.pdf", "signatures/initials.png"):
            path = self.paths.runtime / "stirling-pdf" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"isolated-persisted-fixture")
            expected[name] = backup.hash_file(path)
        payload = self.payload()
        members = backup.archive_inventory(payload / "stirling-pdf.tar", app="stirling-pdf")
        for name, digest in expected.items():
            self.assertEqual(next(member["sha256"] for member in members if member["path"] == name), digest)
        backup.verify_stage(payload, self.image_root)

    def test_valid_nonempty_payload_with_empty_watch_list_is_accepted(self):
        result = backup.verify_stage(self.payload(), self.image_root)
        self.assertEqual(result["app_count"], 2)
        self.assertGreater(result["state_and_recovery_bytes"], 0)

    def test_empty_payload_is_refused(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(backup.BackupError):
            backup.verify_stage(empty, self.image_root)

    def test_missing_archive_is_refused(self):
        payload = self.payload()
        (payload / "changedetection.tar").unlink()
        with self.assertRaises(backup.BackupError):
            backup.verify_stage(payload, self.image_root)

    def test_tampered_archive_is_refused(self):
        payload = self.payload()
        with (payload / "stirling-pdf.tar").open("r+b") as stream:
            stream.seek(600)
            stream.write(b"changed")
        with self.assertRaises(backup.BackupError):
            backup.verify_stage(payload, self.image_root)

    def test_changed_image_is_refused(self):
        payload = self.payload()
        (self.image_root / "stirling-pdf.tar").write_bytes(b"replaced-image")
        with self.assertRaises(backup.BackupError):
            backup.verify_stage(payload, self.image_root)

    def test_missing_docker_image_index_is_refused(self):
        (self.image_root / "archives.json").unlink()
        with self.assertRaisesRegex(backup.BackupError, "image_archive_index_missing"):
            backup.verify_stage(self.payload(), self.image_root)

    def test_missing_active_image_index_entry_is_refused(self):
        backup.atomic_json(self.image_root / "archives.json", self.image_index[1:])
        with self.assertRaisesRegex(backup.BackupError, "active_image_export_missing"):
            backup.verify_stage(self.payload(), self.image_root)

    def test_changed_app_image_requires_its_own_export(self):
        self.image_ids["stirling-pdf"] = "sha256:" + "d" * 64
        with self.assertRaisesRegex(backup.BackupError, "active_image_export_missing"):
            backup.verify_stage(self.payload(), self.image_root)

    def test_image_index_filename_hash_and_size_must_match_inventory(self):
        for number, (key, value) in enumerate((("file", "missing.tar"), ("sha256", "0" * 64), ("size", 1))):
            with self.subTest(key=key):
                entries = [dict(entry) for entry in self.image_index]
                entries[0][key] = value
                backup.atomic_json(self.image_root / "archives.json", entries)
                with self.assertRaisesRegex(backup.BackupError, "image_export_inventory_mismatch"):
                    backup.verify_stage(self.payload(self.root / f"bad-index-{number}"), self.image_root)

    def test_opaque_mcp_runtime_archive_is_protected_without_becoming_a_docker_image(self):
        (self.image_root / "stirling-mcp-runtime.tar.zst").write_bytes(b"isolated-runtime-fixture")
        payload = self.payload()
        manifest = json.loads((payload / "manifest.json").read_text())
        self.assertIn("stirling-mcp-runtime.tar.zst", {item["path"] for item in manifest["images"]})
        backup.verify_stage(payload, self.image_root)

    def test_changed_mode_is_refused(self):
        payload = self.payload()
        (payload / "recovery.tar").chmod(0o644)
        with self.assertRaises(backup.BackupError):
            backup.verify_stage(payload, self.image_root)

    def test_missing_application_state_is_not_empty_success(self):
        root = self.paths.runtime / "changedetection"
        (root / "changedetection.json").unlink()
        (root / "unrelated.txt").write_text("not a recoverable app store")
        target = self.root / "bad.tar"
        backup.make_archive(target, [(root, ".")], seconds=10)
        with self.assertRaises(backup.BackupError):
            backup.archive_inventory(target, app="changedetection")

    def test_symlink_archive_is_refused(self):
        (self.paths.runtime / "changedetection/escape").symlink_to("/etc/passwd")
        target = self.root / "bad.tar"
        backup.make_archive(target, [(self.paths.runtime / "changedetection", ".")], seconds=10)
        with self.assertRaises(backup.BackupError):
            backup.archive_inventory(target)


class ContainerContractTests(Fixture):
    def test_changedetection_datastore_and_readonly_source_binds_are_accepted(self):
        mounts = [{"Type": "bind", "Source": str(self.paths.runtime / "changedetection"),
                   "Destination": "/datastore", "RW": True}]
        mounts += [{"Type": "bind", "Source": str(self.paths.source / "docker/changedetection" / name),
                    "Destination": "/opt/edsys/" + name, "RW": False}
                   for name in ("initialize.py", "healthcheck.py")]
        info = {"Name": "/edsys-changedetection", "Mounts": mounts, "State": {"Running": True}}
        with mock.patch.object(backup, "run", return_value=json.dumps([info]).encode()):
            backup.docker_inspect("edsys-changedetection", "changedetection", self.paths)
        mounts[-1]["RW"] = True
        with mock.patch.object(backup, "run", return_value=json.dumps([info]).encode()):
            with self.assertRaises(backup.BackupError):
                backup.docker_inspect("edsys-changedetection", "changedetection", self.paths)

    def test_all_stirling_state_binds_are_required(self):
        mounts = [{"Type": "bind", "Source": str(self.paths.runtime / "stirling-pdf" / name),
                   "Destination": destination, "RW": True}
                  for name, destination in backup.STATE_BINDS["stirling-pdf"].items()]
        info = {"Name": "/edsys-stirling-pdf", "Mounts": mounts, "State": {"Running": True}}
        with mock.patch.object(backup, "run", return_value=json.dumps([info]).encode()):
            backup.docker_inspect("edsys-stirling-pdf", "stirling-pdf", self.paths)
        info["Mounts"] = mounts[:-1]
        with mock.patch.object(backup, "run", return_value=json.dumps([info]).encode()):
            with self.assertRaises(backup.BackupError):
                backup.docker_inspect("edsys-stirling-pdf", "stirling-pdf", self.paths)

    def test_uncaptured_extra_durable_mount_is_refused(self):
        mounts = [{"Type": "bind", "Source": str(self.paths.runtime / "stirling-pdf" / name),
                   "Destination": destination, "RW": True}
                  for name, destination in backup.STATE_BINDS["stirling-pdf"].items()]
        mounts.append({"Type": "volume", "Destination": "/database", "Name": "uncaptured"})
        info = {"Name": "/edsys-stirling-pdf", "Mounts": mounts, "State": {"Running": True}}
        with mock.patch.object(backup, "run", return_value=json.dumps([info]).encode()):
            with self.assertRaises(backup.BackupError):
                backup.docker_inspect("edsys-stirling-pdf", "stirling-pdf", self.paths)


class RecoverySourceTests(Fixture):
    def setUp(self):
        super().setUp()
        self.recovery_artifacts()

    def test_mcp_entrypoint_and_bridge_source_are_captured(self):
        target = self.root / "recovery.tar"
        backup.make_archive(target, backup.recovery_sources(self.paths), seconds=10)
        members = backup.archive_inventory(target)
        names = {item["path"] for item in members}
        self.assertTrue({"source/services/stirling-mcp/server.py",
                         "source/services/stirling-mcp/bridge.py"}.issubset(names))

    def test_missing_mcp_entrypoint_is_refused_before_cold_capture(self):
        (self.paths.source / "services/stirling-mcp/server.py").unlink()
        with mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "recovery_source_missing"):
                backup.prepare_generation(self.paths, "missing-mcp-fixture")
            capture.assert_not_called()


class ExpandedRecoveryTests(ExpandedFixture):
    def test_schema_2_capture_never_stops_or_starts_stateless_helpers(self):
        for service in backup.SIDECARS:
            self.containers[service]["State"]["Running"] = False
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app", side_effect=self.capture) as capture:
            payload = backup.prepare_generation(self.paths, "expanded-fixture")
        self.assertEqual([call.args[0] for call in capture.call_args_list], list(backup.APPS))
        result = backup.verify_stage(payload, self.image_root)
        self.assertEqual((result["manifest_schema"], result["sidecar_count"]), (2, 2))
        manifest = json.loads((payload / "manifest.json").read_text())
        self.assertTrue(all(not manifest["deployment"]["containers"][name]["was_running"]
                            for name in backup.SIDECARS))

    def test_missing_sidecar_is_refused_before_cold_capture(self):
        del self.containers["browser"]
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "deployment_container_set_mismatch"):
                backup.prepare_generation(self.paths, "missing-sidecar")
            capture.assert_not_called()

    def test_deployed_compose_configuration_must_match_source(self):
        self.containers["egress"]["Config"]["Labels"]["com.docker.compose.config-hash"] = "0" * 64
        with self.assertRaisesRegex(backup.BackupError, "deployment_source_runtime_mismatch"):
            self.deployment()

    def test_stateless_helper_cannot_hide_an_uncaptured_volume(self):
        self.containers["browser"]["Mounts"].append({"Type": "volume", "Destination": "/unexpected"})
        with self.assertRaisesRegex(backup.BackupError, "deployment_mount_contract_mismatch"):
            self.deployment()

    def test_sidecar_source_images_must_be_pinned(self):
        self.compose["services"]["browser"]["image"] = "example.invalid/browser:latest"
        with self.assertRaisesRegex(backup.BackupError, "deployment_image_not_pinned"):
            self.deployment()

    def test_sidecar_export_is_required_even_with_other_image_archives_present(self):
        index = [entry for entry in self.image_index if entry["file"] != "browser.tar"]
        backup.atomic_json(self.image_root / "archives.json", index)
        with self.assertRaisesRegex(backup.BackupError, "active_image_export_missing"):
            backup.verify_stage(self.payload(), self.image_root)

    def test_app_and_egress_can_share_one_exact_retained_image(self):
        image_id = self.image_ids["changedetection"]
        self.image_ids["egress"] = image_id
        self.compose["services"]["egress"]["image"] = image_id
        self.containers["egress"]["Image"] = image_id
        self.containers["egress"]["Config"]["Image"] = image_id
        self.write_image_lock()
        index = [entry for entry in self.image_index if entry["file"] != "egress.tar"]
        backup.atomic_json(self.image_root / "archives.json", index)
        (self.image_root / "egress.tar").unlink()
        result = backup.verify_stage(self.payload(), self.image_root)
        self.assertEqual(result["sidecar_count"], 2)

    def test_changed_sidecar_image_without_new_export_fails_before_cold_capture(self):
        image_id = "sha256:" + "9" * 64
        self.image_ids["browser"] = image_id
        self.compose["services"]["browser"]["image"] = image_id
        self.containers["browser"]["Image"] = image_id
        self.containers["browser"]["Config"]["Image"] = image_id
        self.write_image_lock()
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "active_image_export_missing"):
                backup.prepare_generation(self.paths, "stale-sidecar-image")
            capture.assert_not_called()

    def test_stale_qualified_source_pin_fails_before_cold_capture(self):
        self.image_lock["images"]["browser"]["image_id"] = "sha256:" + "9" * 64
        backup.atomic_json(self.paths.source / "docker/changedetection/images.lock.json", self.image_lock)
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "deployment_image_lock_mismatch"):
                backup.prepare_generation(self.paths, "stale-source-pin")
            capture.assert_not_called()

    def test_invalid_qualified_source_lock_is_refused(self):
        invalid = [dict(self.image_lock, schema_version=True),
                   {"schema_version": 1, "images": {"app": self.image_lock["images"]["app"]}},
                   {"schema_version": 1, "images": dict(self.image_lock["images"], browser={"image_id": "latest"})}]
        for value in invalid:
            with self.subTest(value=value):
                backup.atomic_json(self.paths.source / "docker/changedetection/images.lock.json", value)
                with self.assertRaisesRegex(backup.BackupError, "deployment_image_lock_invalid"):
                    self.deployment()

    def test_edited_build_recipe_requires_new_qualification(self):
        (self.paths.source / "docker/changedetection/patch_app.py").write_text("unqualified-source-edit\n")
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "deployment_build_source_mismatch"):
                backup.prepare_generation(self.paths, "stale-build-recipe")
            capture.assert_not_called()

    def test_restored_source_image_lock_must_match_manifest_deployment(self):
        path = self.payload()
        self.image_lock["images"]["browser"]["image_id"] = "sha256:" + "9" * 64
        backup.atomic_json(self.paths.source / "docker/changedetection/images.lock.json", self.image_lock)
        backup.make_archive(path / "recovery.tar", backup.recovery_sources(self.paths), seconds=10)
        manifest = json.loads((path / "manifest.json").read_text())
        manifest["recovery_members"] = backup.archive_inventory(path / "recovery.tar")
        manifest["archives"]["recovery.tar"] = backup.file_record(path / "recovery.tar", "recovery.tar")
        manifest["deployment"]["source_hashes"]["source/docker/changedetection/images.lock.json"] = backup.hash_file(
            self.paths.source / "docker/changedetection/images.lock.json")
        backup.atomic_json(path / "manifest.json", manifest)
        with self.assertRaisesRegex(backup.BackupError, "deployment_image_lock_mismatch"):
            backup.verify_stage(path, self.image_root)

    def test_missing_mcp_source_and_private_prerequisites_fail_before_cold_capture(self):
        paths = [self.paths.source / "services/changedetection-mcp/catalog-source.json",
                 self.paths.source / "docker/changedetection/egress_proxy.py"]
        paths += [self.paths.runtime / "deploy" / name for name in (
            backup.MCP_KEY, backup.MCP_REGISTRATION, backup.MCP_INSTALL)]
        for number, path in enumerate(paths):
            with self.subTest(number=number):
                content, mode = path.read_bytes(), path.stat().st_mode & 0o777
                path.unlink()
                try:
                    with mock.patch.object(backup, "capture_app") as capture:
                        with self.assertRaises(backup.BackupError):
                            backup.prepare_generation(self.paths, f"missing-mcp-{number}")
                        capture.assert_not_called()
                finally:
                    path.write_bytes(content)
                    path.chmod(mode)

    def test_missing_mcp_runtime_archive_fails_before_cold_capture(self):
        (self.image_root / self.runtime_receipt["file"]).unlink()
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "mcp_runtime_archive_mismatch"):
                backup.prepare_generation(self.paths, "missing-runtime-archive")
            capture.assert_not_called()

    def test_missing_virtualenv_is_not_a_qualified_active_runtime(self):
        (self.release / "venv/pyvenv.cfg").unlink()
        with self.assertRaisesRegex(backup.BackupError, "mcp_virtualenv_missing"):
            backup.mcp_snapshot(self.paths, backup.image_inventory(self.image_root))

    def test_active_mcp_release_must_match_retained_archive(self):
        other = self.release.with_name("20261102T010000Z-0.1.1")
        other.mkdir()
        (self.paths.changedetection_mcp / "current").unlink()
        (self.paths.changedetection_mcp / "current").symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(backup.BackupError, "mcp_active_release_mismatch"):
            backup.mcp_snapshot(self.paths, backup.image_inventory(self.image_root))

    def test_installed_mcp_code_must_match_captured_source_and_runtime_receipt(self):
        (self.release / "bridge.py").write_text("different offline runtime")
        with self.assertRaisesRegex(backup.BackupError, "mcp_runtime_source_mismatch"):
            backup.mcp_snapshot(self.paths, backup.image_inventory(self.image_root))

    def test_captured_api_key_must_match_captured_native_state_without_exposing_values(self):
        (self.paths.runtime / "deploy" / backup.MCP_KEY).write_text("different-offline-key\n")
        with self.assertRaisesRegex(backup.BackupError, "^mcp_api_key_mismatch$"):
            backup.verify_stage(self.payload(), self.image_root)

    def test_api_key_requires_private_mode(self):
        (self.paths.runtime / "deploy" / backup.MCP_KEY).chmod(0o644)
        with self.assertRaisesRegex(backup.BackupError, "mcp_key_permissions_invalid"):
            backup.mcp_snapshot(self.paths, backup.image_inventory(self.image_root))

    def test_registration_must_use_expected_launcher_endpoint_and_key(self):
        path = self.paths.runtime / "deploy" / backup.MCP_REGISTRATION
        original = path.read_text()
        cases = ((backup.MCP_BASE_URL, "http://127.0.0.1:8141"),
                 (backup.MCP_KEY, "unrelated-key"), ("current/run.sh", "other/run.sh"),
                 (str(self.root / "mcp-output"), str(self.paths.runtime / "artifacts")))
        for before, after in cases:
            with self.subTest(after=after):
                path.write_text(original.replace(before, after))
                with self.assertRaisesRegex(backup.BackupError, "mcp_registration_mismatch"):
                    backup.mcp_snapshot(self.paths, backup.image_inventory(self.image_root))

    def test_exact_prechange_configuration_is_required(self):
        installation = json.loads((self.paths.runtime / "deploy" / backup.MCP_INSTALL).read_text())
        (self.paths.runtime / "deploy" / installation["before_config_file"]).unlink()
        with self.assertRaisesRegex(backup.BackupError, "mcp_prechange_config_missing"):
            backup.mcp_snapshot(self.paths, backup.image_inventory(self.image_root))

    def test_installed_lifecycle_helper_must_match_captured_source_before_capture(self):
        (self.paths.libexec / "edsys-private-web-apps-control").write_text("outdated offline helper")
        with mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "installed_helper_source_mismatch"):
                backup.prepare_generation(self.paths, "outdated-helper")
            capture.assert_not_called()

    def test_source_change_during_capture_preserves_last_good(self):
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        def changed_source(app, target, paths):
            result = self.capture(app, target, paths)
            if app == "changedetection":
                with (paths.runtime / "deploy/changedetection.env").open("a") as stream:
                    stream.write("changed-during-capture\n")
            return result
        with mock.patch.object(backup, "run", side_effect=self.docker_read), \
                mock.patch.object(backup, "capture_app", side_effect=changed_source):
            with self.assertRaisesRegex(backup.BackupError, "deployment_changed_during_capture"):
                backup.prepare_generation(self.paths, "source-drift")
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)


class LegacyRestoreTests(Fixture):
    def test_schema_1_full_snapshot_id_remains_supported(self):
        identifier = "c" * 64
        record = {"id": identifier, "tags": [backup.SCOPE, backup.FORMAT_TAGS[1]],
                  "paths": [str(self.paths.stage / "snapshot-payload"), str(self.image_root)]}
        repository = backup.Repository({})
        with mock.patch.object(repository, "call", return_value=json.dumps([record]).encode()):
            self.assertEqual(repository.metadata(identifier, self.paths), record)

    def test_conflicting_format_tags_are_refused(self):
        identifier = "c" * 64
        record = {"id": identifier, "tags": [backup.SCOPE, *backup.FORMAT_TAGS.values()],
                  "paths": [str(self.paths.stage / "snapshot-payload"), str(self.image_root)]}
        repository = backup.Repository({})
        with mock.patch.object(repository, "call", return_value=json.dumps([record]).encode()):
            with self.assertRaisesRegex(backup.BackupError, "snapshot_scope_mismatch"):
                repository.metadata(identifier, self.paths)

    def test_schema_1_restore_needs_no_new_live_mcp_or_sidecars(self):
        payload = self.payload()
        target = self.root / "isolated-legacy-restore"
        repository = mock.Mock()
        repository.metadata.return_value = {"tags": [backup.SCOPE, backup.FORMAT_TAGS[1]]}
        def restored_files(*args, **kwargs):
            destination = target / str(self.paths.stage / "snapshot-payload").lstrip("/")
            destination.parent.mkdir(parents=True)
            shutil.copytree(payload, destination)
            images = target / str(self.image_root).lstrip("/")
            images.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.image_root, images)
            return b""
        repository.call.side_effect = restored_files
        with mock.patch.object(backup, "deployment_snapshot", side_effect=AssertionError("live deployment read")), \
                mock.patch.object(backup, "mcp_snapshot", side_effect=AssertionError("live MCP read")):
            result = backup.restore(self.paths, repository, "c" * 64, target)
        self.assertEqual((result["manifest_schema"], result["sidecar_count"]), (1, 0))

    def test_restore_format_tag_must_match_manifest(self):
        payload = self.payload()
        target = self.root / "wrong-format-restore"
        repository = mock.Mock()
        repository.metadata.return_value = {"tags": [backup.SCOPE, backup.FORMAT_TAGS[2]]}
        def restored_files(*args, **kwargs):
            destination = target / str(self.paths.stage / "snapshot-payload").lstrip("/")
            destination.parent.mkdir(parents=True)
            shutil.copytree(payload, destination)
            images = target / str(self.image_root).lstrip("/")
            images.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(self.image_root, images)
            return b""
        repository.call.side_effect = restored_files
        with self.assertRaisesRegex(backup.BackupError, "snapshot_format_mismatch"):
            backup.restore(self.paths, repository, "c" * 64, target)


class RunningStateTests(Fixture):
    def test_growth_during_stop_is_refused_and_running_state_restored(self):
        state, inspect, run = self.simulated_docker()
        def growing_stop(command, **kwargs):
            result = run(command, **kwargs)
            if "stop" in command:
                with (self.paths.runtime / "stirling-pdf/finished.pdf").open("wb") as stream:
                    stream.truncate(backup.MAX_STATE_BYTES + 1)
            return result
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", growing_stop), \
                mock.patch.object(backup, "make_archive") as archive:
            with self.assertRaisesRegex(backup.BackupError, "capture_size_limit"):
                backup.capture_app("stirling-pdf", self.root / "state.tar", self.paths)
            archive.assert_not_called()
        self.assertTrue(state["running"])

    def test_running_app_is_restarted_before_inventory(self):
        state, inspect, run = self.simulated_docker()
        real_inventory = backup.archive_inventory
        def checked_inventory(*args, **kwargs):
            self.assertTrue(state["running"])
            return real_inventory(*args, **kwargs)
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", run), \
                mock.patch.object(backup, "archive_inventory", checked_inventory):
            backup.capture_app("changedetection", self.root / "state.tar", self.paths)
        self.assertEqual(state["events"].count("stop"), 1)
        self.assertEqual(state["events"].count("start"), 1)
        self.assertFalse((self.paths.stage / "restart-intent.json").exists())

    def test_stopped_app_is_never_started(self):
        state, inspect, run = self.simulated_docker(running=False)
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", run):
            backup.capture_app("changedetection", self.root / "state.tar", self.paths)
        self.assertFalse(state["running"])
        self.assertNotIn("stop", state["events"])
        self.assertNotIn("start", state["events"])

    def test_archive_failure_restores_previous_running_state(self):
        state, inspect, run = self.simulated_docker()
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", run), \
                mock.patch.object(backup, "make_archive", side_effect=backup.BackupError("capture_timeout")):
            with self.assertRaises(backup.BackupError):
                backup.capture_app("changedetection", self.root / "state.tar", self.paths)
        self.assertTrue(state["running"])
        self.assertIn("start", state["events"])

    def test_failed_stop_does_not_toggle_a_still_running_app(self):
        state, inspect, run = self.simulated_docker(stop_error=True)
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", run):
            with self.assertRaises(backup.BackupError):
                backup.capture_app("changedetection", self.root / "state.tar", self.paths)
        self.assertTrue(state["running"])
        self.assertNotIn("start", state["events"])

    def test_restart_failure_retains_recovery_intent(self):
        state, inspect, run = self.simulated_docker(start_error=True)
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", run):
            with self.assertRaises(backup.BackupError):
                backup.capture_app("changedetection", self.root / "state.tar", self.paths)
        self.assertFalse(state["running"])
        self.assertTrue((self.paths.stage / "restart-intent.json").exists())

    def test_forced_kill_does_not_qualify_as_consistent_capture(self):
        state, inspect, run = self.simulated_docker()
        def killed_inspect(*args):
            info = inspect(*args)
            info["State"]["ExitCode"] = 0 if state["running"] else 137
            return info
        with mock.patch.object(backup, "docker_inspect", killed_inspect), mock.patch.object(backup, "run", run), \
                mock.patch.object(backup, "make_archive") as archive:
            with self.assertRaises(backup.BackupError):
                backup.capture_app("changedetection", self.root / "state.tar", self.paths)
            archive.assert_not_called()
        self.assertTrue(state["running"])

    def test_termination_waits_for_stop_then_restores_running_state(self):
        state, inspect, run = self.simulated_docker()
        drained = []
        def interrupted_stop(command, **kwargs):
            if "stop" in command:
                backup.signal.getsignal(backup.signal.SIGTERM)(backup.signal.SIGTERM, None)
                drained.append(True)
            return run(command, **kwargs)
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", interrupted_stop), \
                mock.patch.object(backup, "make_archive") as archive:
            with self.assertRaises(backup.BackupError):
                backup.capture_app("changedetection", self.root / "state.tar", self.paths)
            archive.assert_not_called()
        self.assertEqual(drained, [True])
        self.assertTrue(state["running"])

    def test_stale_restart_intent_never_resurrects_a_later_stopped_service(self):
        state, inspect, run = self.simulated_docker(running=False)
        backup.atomic_json(self.paths.stage / "restart-intent.json", {
            "app": "changedetection", "container_id": self.identifier, "created_at": "2020-01-01T00:00:00+00:00"})
        with mock.patch.object(backup, "docker_inspect", inspect), mock.patch.object(backup, "run", run):
            with self.assertRaises(backup.BackupError):
                backup.recover_running(self.paths)
        self.assertNotIn("start", state["events"])

    def test_empty_state_is_rejected_before_any_container_operation(self):
        shutil.rmtree(self.paths.runtime / "changedetection")
        (self.paths.runtime / "changedetection").mkdir()
        with mock.patch.object(backup, "docker_inspect") as inspect:
            with self.assertRaises(backup.BackupError):
                backup.capture_app("changedetection", self.root / "state.tar", self.paths)
            inspect.assert_not_called()


class SpaceTests(Fixture):
    def recovery_sources(self):
        return [(self.paths.runtime / "stirling-pdf/configs/settings.yml", "source/settings.yml")]

    def test_insufficient_space_fails_before_any_container_stop(self):
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        with mock.patch.object(backup, "recovery_sources", return_value=self.recovery_sources()), \
                mock.patch.object(backup.shutil, "disk_usage", return_value=mock.Mock(free=1)), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "staging_space_insufficient"):
                backup.prepare_generation(self.paths, "space-refusal")
            capture.assert_not_called()
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)

    def test_shared_filesystem_requires_room_for_stage_and_encrypted_copy(self):
        # Sparse metadata fixture: no multi-GiB file content is written or read.
        document = self.paths.runtime / "stirling-pdf/engineering.pdf"
        with document.open("wb") as stream:
            stream.truncate(3 * 1024**3)
        repository = self.root / "repository"
        repository.mkdir()
        images = backup.image_inventory(self.image_root)
        with mock.patch.object(backup.shutil, "disk_usage", return_value=mock.Mock(free=6 * 1024**3)):
            backup.preflight_space(self.paths, self.recovery_sources(), images, None)
            with self.assertRaisesRegex(backup.BackupError, "staging_space_insufficient"):
                backup.preflight_space(self.paths, self.recovery_sources(), images, repository)

    def test_oversized_state_is_refused_without_copying(self):
        document = self.paths.runtime / "stirling-pdf/too-large.pdf"
        with document.open("wb") as stream:
            stream.truncate(backup.MAX_STATE_BYTES + 1)
        with mock.patch.object(backup, "recovery_sources", return_value=self.recovery_sources()), \
                mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaisesRegex(backup.BackupError, "capture_size_limit"):
                backup.prepare_generation(self.paths, "oversize-refusal")
            capture.assert_not_called()


class ProcessTests(Fixture):
    def test_cancelled_client_receives_graceful_termination(self):
        marker = self.root / "client-cleanup"
        script = """import os, signal, sys
from pathlib import Path
def stop(signum, frame):
    Path(sys.argv[1]).write_text('graceful')
    raise SystemExit(0)
signal.signal(signal.SIGTERM, stop)
os.kill(os.getppid(), signal.SIGTERM)
signal.pause()
"""
        def interrupted(signum, frame):
            raise backup.BackupError("test_termination")
        previous = backup.signal.signal(backup.signal.SIGTERM, interrupted)
        try:
            with self.assertRaises(backup.BackupError):
                backup.run([backup.sys.executable, "-c", script, str(marker)], label="fixture", timeout=5)
        finally:
            backup.signal.signal(backup.signal.SIGTERM, previous)
        self.assertEqual(marker.read_text(), "graceful")


class RetentionTests(Fixture):
    def make_accepted(self, day):
        generation = f"202610{day:02d}T033000Z-{day:08x}"
        directory = self.payload(self.paths.stage / "generations" / generation)
        manifest = json.loads((directory / "manifest.json").read_text())
        manifest["generation"] = generation
        backup.atomic_json(directory / "manifest.json", manifest)
        receipt = {"schema": 1, "scope": backup.SCOPE, "status": "success", "generation": generation,
                   "snapshot_id": f"{day:064x}", "manifest_sha256": backup.hash_file(directory / "manifest.json"),
                   "completed_at": f"2026-10-{day:02d}T03:31:00+00:00"}
        backup.atomic_json(self.paths.stage / "accepted" / (generation + ".json"), receipt)
        return directory, receipt

    def set_current(self, directory, receipt, *, payload_directory=None):
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        payload = self.paths.stage / "snapshot-payload"
        payload.mkdir()
        for name in backup.PAYLOAD_FILES:
            backup.os.link((payload_directory or directory) / name, payload / name)

    def test_keeps_current_and_two_prior_accepted_generations(self):
        items = [self.make_accepted(day) for day in range(25, 30)]
        self.set_current(*items[-1])
        current_bytes = (self.paths.stage / "CURRENT.json").read_bytes()
        image_hashes = {p.name: backup.hash_file(p) for p in self.image_root.iterdir()}
        result = backup.cleanup_accepted_generations(self.paths)
        self.assertEqual(result["removed_generations"], 2)
        self.assertEqual({p.name for p in (self.paths.stage / "generations").iterdir()},
                         {p.name for p, _ in items[-3:]})
        self.assertEqual((self.paths.stage / "CURRENT.json").read_bytes(), current_bytes)
        self.assertEqual({p.name: backup.hash_file(p) for p in self.image_root.iterdir()}, image_hashes)
        backup.verify_stage(self.paths.stage / "snapshot-payload", self.image_root)

    def test_failed_unknown_and_symlink_generations_are_preserved(self):
        items = [self.make_accepted(day) for day in range(25, 30)]
        self.set_current(*items[-1])
        failed = self.payload(self.paths.stage / "generations/20261024T033000Z-00000024")
        unknown = self.paths.stage / "generations/operator-notes"
        unknown.mkdir()
        (unknown / "keep.txt").write_text("operator evidence")
        link = self.paths.stage / "generations/20261023T033000Z-00000023"
        link.symlink_to(self.paths.runtime / "stirling-pdf", target_is_directory=True)
        backup.cleanup_accepted_generations(self.paths)
        self.assertTrue(failed.exists())
        self.assertEqual((unknown / "keep.txt").read_text(), "operator evidence")
        self.assertTrue(link.is_symlink())
        self.assertTrue((self.paths.runtime / "stirling-pdf/configs/settings.yml").exists())

    def test_tampered_old_archive_is_not_deleted(self):
        items = [self.make_accepted(day) for day in range(25, 30)]
        self.set_current(*items[-1])
        with (items[0][0] / "stirling-pdf.tar").open("r+b") as stream:
            stream.write(b"X")
        result = backup.cleanup_accepted_generations(self.paths)
        self.assertEqual(result["removed_generations"], 1)
        self.assertTrue(items[0][0].exists())

    def test_invalid_receipt_is_ignored_and_preserved(self):
        items = [self.make_accepted(day) for day in range(25, 30)]
        self.set_current(*items[-1])
        receipt = dict(items[0][1], snapshot_id="latest")
        backup.atomic_json(self.paths.stage / "accepted" / (items[0][0].name + ".json"), receipt)
        backup.cleanup_accepted_generations(self.paths)
        self.assertTrue(items[0][0].exists())

    def test_mismatched_current_payload_refuses_all_cleanup(self):
        items = [self.make_accepted(day) for day in range(25, 30)]
        self.set_current(*items[-1], payload_directory=items[-2][0])
        with self.assertRaises(backup.BackupError):
            backup.cleanup_accepted_generations(self.paths)
        self.assertTrue(all(path.exists() for path, _ in items))

    def test_newer_receipt_is_not_treated_as_prior_history(self):
        items = [self.make_accepted(day) for day in range(25, 31)]
        self.set_current(*items[-2])
        backup.cleanup_accepted_generations(self.paths)
        self.assertTrue(items[-1][0].exists())


class TransactionTests(ExpandedFixture):
    def test_mcp_key_mismatch_preserves_last_good_without_restic_write(self):
        previous = self.payload(self.paths.stage / "snapshot-payload")
        previous_hash = backup.hash_file(previous / "manifest.json")
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        (self.paths.runtime / "deploy" / backup.MCP_KEY).write_text("mismatched-offline-key")
        candidate = self.payload(self.paths.stage / "generations/mismatched-key-fixture")
        repository = self.fake_repository()
        with mock.patch.object(backup, "prepare_generation", return_value=candidate):
            with self.assertRaisesRegex(backup.BackupError, "mcp_api_key_mismatch"):
                backup.backup(self.paths, repository)
        repository.backup.assert_not_called()
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)
        self.assertEqual(backup.hash_file(previous / "manifest.json"), previous_hash)

    def test_new_capture_cannot_fall_back_to_schema_1(self):
        previous = self.payload(self.paths.stage / "snapshot-payload")
        old_hash = backup.hash_file(previous / "manifest.json")
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        legacy = Fixture.payload(self, self.paths.stage / "generations/legacy-fixture")
        repository = self.fake_repository()
        with mock.patch.object(backup, "prepare_generation", return_value=legacy):
            with self.assertRaisesRegex(backup.BackupError, "new_capture_requires_schema_2"):
                backup.backup(self.paths, repository)
        repository.backup.assert_not_called()
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)
        self.assertEqual(backup.hash_file(previous / "manifest.json"), old_hash)

    def test_stale_image_export_preserves_last_good_without_restic_write(self):
        previous = self.payload(self.paths.stage / "snapshot-payload")
        previous_hash = backup.hash_file(previous / "manifest.json")
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        self.image_ids["stirling-pdf"] = "sha256:" + "d" * 64
        candidate = self.payload(self.paths.stage / "generations/stale-export-fixture")
        repository = self.fake_repository()
        with mock.patch.object(backup, "prepare_generation", return_value=candidate):
            with self.assertRaisesRegex(backup.BackupError, "active_image_export_missing"):
                backup.backup(self.paths, repository)
        repository.backup.assert_not_called()
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)
        self.assertEqual(backup.hash_file(previous / "manifest.json"), previous_hash)

    def test_cleanup_failure_keeps_successfully_accepted_backup(self):
        def prepare(paths, generation, repository_path):
            directory = self.payload(paths.stage / "generations" / generation)
            manifest = json.loads((directory / "manifest.json").read_text())
            manifest["generation"] = generation
            backup.atomic_json(directory / "manifest.json", manifest)
            return directory
        repository = self.fake_repository()
        repository.backup.return_value = "c" * 64
        with mock.patch.object(backup, "prepare_generation", side_effect=prepare), \
                mock.patch.object(backup, "cleanup_accepted_generations", side_effect=OSError("fixture refusal")):
            result = backup.backup(self.paths, repository)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["staging_cleanup"]["status"], "deferred")
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text())["snapshot_id"], "c" * 64)
        self.assertTrue((self.paths.stage / "accepted" / (result["generation"] + ".json")).is_file())

    def test_failed_payload_publish_restores_previous_payload(self):
        previous = self.payload(self.paths.stage / "snapshot-payload")
        old_hash = backup.hash_file(previous / "manifest.json")
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        candidate = self.payload(self.paths.stage / "generations/new-fixture")
        rename = backup.os.rename
        def fail_publish(source, destination):
            if source.name.startswith(".new-payload-"):
                raise OSError("injected publish failure")
            return rename(source, destination)
        with mock.patch.object(backup, "prepare_generation", return_value=candidate), \
                mock.patch.object(backup.os, "rename", side_effect=fail_publish):
            with self.assertRaises(OSError):
                backup.backup(self.paths, self.fake_repository())
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)
        self.assertEqual(backup.hash_file(previous / "manifest.json"), old_hash)

        self.assertFalse((self.paths.stage / "accepted").exists())

    def test_restic_failure_preserves_current_and_previous_payload(self):
        previous = self.payload(self.paths.stage / "snapshot-payload")
        old_hash = backup.hash_file(previous / "manifest.json")
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        candidate = self.payload(self.paths.stage / "generations/new-fixture")
        repository = self.fake_repository()
        repository.backup.side_effect = backup.BackupError("restic_failed")
        with mock.patch.object(backup, "prepare_generation", return_value=candidate):
            with self.assertRaises(backup.BackupError):
                backup.backup(self.paths, repository)
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)
        self.assertEqual(backup.hash_file(previous / "manifest.json"), old_hash)

    def test_capture_failure_keeps_last_good(self):
        receipt = {"snapshot_id": "c" * 64, "status": "success"}
        backup.atomic_json(self.paths.stage / "CURRENT.json", receipt)
        with mock.patch.object(backup, "prepare_generation", side_effect=backup.BackupError("capture_failed")):
            with self.assertRaises(backup.BackupError):
                backup.backup(self.paths, self.fake_repository())
        self.assertEqual(json.loads((self.paths.stage / "CURRENT.json").read_text()), receipt)

    def test_unqualified_latest_and_short_ids_are_rejected(self):
        repo = backup.Repository({})
        for value in ("latest", "abcd1234", ""):
            with self.subTest(value=value), self.assertRaises(backup.BackupError):
                repo.metadata(value, self.paths)

    def test_wrong_scope_or_missing_image_path_is_rejected(self):
        snapshot = "c" * 64
        repo = backup.Repository({})
        for record in (
            {"id": snapshot, "tags": ["unrelated"], "paths": []},
            {"id": snapshot, "tags": [backup.SCOPE, backup.FORMAT_TAG],
             "paths": [str(self.paths.stage / "snapshot-payload")]},
        ):
            with mock.patch.object(repo, "call", return_value=json.dumps([record]).encode()):
                with self.assertRaises(backup.BackupError):
                    repo.metadata(snapshot, self.paths)

    def test_restore_cannot_write_into_live_state(self):
        repo = mock.Mock()
        for target in (self.paths.runtime, self.paths.runtime / "changedetection", self.paths.stage,
                       self.paths.changedetection_mcp, self.paths.changedetection_mcp / "new-restore"):
            with self.subTest(target=target), self.assertRaises(backup.BackupError):
                backup.restore(self.paths, repo, "c" * 64, target)
        repo.call.assert_not_called()

    def test_missing_recovery_artifacts_are_refused_before_cold_capture(self):
        self.paths.mount_guard.unlink()
        with mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaises(backup.BackupError):
                backup.prepare_generation(self.paths, "test-generation")
            capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
