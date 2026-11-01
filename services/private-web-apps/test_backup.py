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
        stirling = self.paths.runtime / "stirling-pdf/configs"
        stirling.mkdir(parents=True)
        (stirling / "settings.yml").write_text("system:\n  enableAnalytics: false\n")
        for directory in ("signatures", "templates", "pipeline"):
            (self.paths.runtime / "stirling-pdf" / directory).mkdir()
        changedetection = self.paths.runtime / "changedetection"
        changedetection.mkdir()
        (changedetection / "changedetection.json").write_text(json.dumps({
            "watching": {}, "settings": {"application": {
                "api_access_token_enabled": True, "api_access_token": "offline-fixture-only"}}}))
        self.identifier = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def fake_repository(self):
        return mock.Mock(environment={"RESTIC_REPOSITORY": str(self.root / "repository")})

    def payload(self, path=None):
        path = path or self.root / "payload"
        path.mkdir(mode=0o700, parents=True)
        apps = {}
        for app in backup.APPS:
            backup.make_archive(path / (app + ".tar"), [(self.paths.runtime / app, ".")], seconds=10)
            apps[app] = {"members": backup.archive_inventory(path / (app + ".tar"), app=app)}
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


class VerificationTests(Fixture):
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


class TransactionTests(Fixture):
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
        for target in (self.paths.runtime, self.paths.runtime / "changedetection", self.paths.stage):
            with self.subTest(target=target), self.assertRaises(backup.BackupError):
                backup.restore(self.paths, repo, "c" * 64, target)
        repo.call.assert_not_called()

    def test_missing_recovery_artifacts_are_refused_before_cold_capture(self):
        with mock.patch.object(backup, "capture_app") as capture:
            with self.assertRaises(backup.BackupError):
                backup.prepare_generation(self.paths, "test-generation")
            capture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
