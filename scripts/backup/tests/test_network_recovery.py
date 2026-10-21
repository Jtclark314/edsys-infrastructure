"""Offline network recovery contracts: fixtures only; no SSH/Docker/source access."""
import datetime as dt
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile
import time
from types import SimpleNamespace
import unittest
from urllib.parse import unquote_to_bytes
from unittest import mock

SPEC = importlib.util.spec_from_file_location("network_recovery", Path(__file__).resolve().parents[1] / "edsys-network-recovery.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
ORIGINAL_NATIVE_WORK_ROOT = m.native_work_root


class NetworkRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.files = self.base / "configuration"
        self.files.mkdir(mode=0o700)
        (self.files / "nftables.conf").write_text("fixture configuration")
        self.stage = self.base / "stage"
        self.native_work = self.base / "native-work"
        self.native_work.mkdir(mode=0o700)
        patch = mock.patch.object(m, "native_work_root", return_value=self.native_work)
        patch.start()
        self.addCleanup(patch.stop)
        self.profile = {"kind": "router", "approved": True, "roots": [{"label": "etc", "path": str(self.files)}], "commands": [], "exclude_globs": [str(self.files / "*.tmp")]}
        self.args = SimpleNamespace(stage_root=str(self.stage), ssh_key="fixture", known_hosts="fixture")
        m.END = time.monotonic() + 30

    def fake_capture(self, _args, profile, output):
        m.worker(profile, output)

    def stage_one(self):
        with mock.patch.object(m, "ssh_capture", side_effect=self.fake_capture), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            return m.stage_source(self.args, "router", self.profile)

    def test_complete_archive_hashes_roundtrip(self):
        self.assertEqual(self.stage_one()["status"], "healthy")
        result = m.verify_source(self.stage / "sources/router", "router")
        self.assertEqual(result["status"], "verified")
        self.assertGreaterEqual(result["members"], 2)

    def test_ssh_is_pinned_and_remote_envelope_compiles(self):
        profile = {**self.profile, "ssh_target": "fixture@example.invalid", "expected_hostname": "fixture"}
        with mock.patch.object(m, "run", return_value=b"") as run:
            m.ssh_capture(self.args, profile, io.BytesIO())
        args, kwargs = run.call_args
        self.assertIn("StrictHostKeyChecking=yes", args[0])
        self.assertNotIn("accept-new", " ".join(args[0]))
        self.assertNotIn(str(self.files), " ".join(args[0]))
        compile(kwargs["data"], "remote-fixture", "exec")
        self.assertIn(b"REMOTE_PROFILE = ", kwargs["data"])

    def test_query_history_and_gravity_sidecars_are_never_opened(self):
        for name in ("pihole-FTL.db", "pihole-FTL.db-wal", "pihole-FTL.db-shm", "pihole-tmp.db", "pihole-tmp.db-wal", "pihole-tmp.db-shm", "gravity.db", "gravity.db-wal", "gravity.db-shm", "ordinary.tmp"):
            (self.files / name).write_bytes(b"corrupt or volatile fixture")
        profile = {**self.profile, "kind": "pihole", "sqlite_databases": [{"name": "gravity", "path": str(self.files / "gravity.db")}]}
        result = m.inventory(profile)
        self.assertEqual(list(result["files"]), ["files/etc/nftables.conf"])

    def test_configured_query_history_and_sidecars_are_excluded(self):
        for name in ("custom-history.sqlite", "custom-history.sqlite-wal", "custom-history.sqlite-shm"):
            (self.files / name).write_bytes(b"never open raw query data")
        profile = {**self.profile, "kind": "pihole", "query_history_paths": [str(self.files / "custom-history.sqlite")]}
        self.assertEqual(list(m.inventory(profile)["files"]), ["files/etc/nftables.conf"])

    def test_gravity_snapshot_is_consistent_and_source_unchanged(self):
        source, destination = self.files / "gravity.db", self.base / "gravity-copy.db"
        with sqlite3.connect(source) as db:
            db.execute("pragma journal_mode=wal")
            db.execute("create table gravity(value text)")
            db.execute("insert into gravity values ('fixture')")
        before = source.read_bytes()
        with mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            m.sqlite_snapshot(source, destination)
        self.assertEqual(source.read_bytes(), before)
        with sqlite3.connect(destination) as db:
            self.assertEqual(db.execute("pragma integrity_check").fetchone()[0], "ok")
            self.assertEqual(db.execute("select value from gravity").fetchone()[0], "fixture")

    def test_sqlite_query_history_cannot_be_configured(self):
        profile = {"kind": "pihole", "approved": True, "ssh_target": "fixture@example.invalid", "expected_hostname": "fixture", "roots": [{"label": name, "path": "/fixture/" + name} for name in m.REQUIRED_ROOTS["pihole"]], "commands": [{"name": name, "argv": ["uname", "-a"]} for name in m.REQUIRED_COMMANDS["pihole"]], "sqlite_databases": [{"name": "gravity", "path": "/etc/pihole/pihole-FTL.db"}, {"name": "macvendor", "path": "/etc/pihole/macvendor.db", "required": False}]}
        with self.assertRaisesRegex(m.Refused, "unapproved_sqlite_database"):
            m.validate_profile("pihole-primary", profile)

    def test_systemd_and_resolver_symlinks_recorded_without_following(self):
        outside = self.base / "private-outside"
        outside.write_text("do not read")
        (self.files / "resolver.conf").symlink_to(outside)
        result = m.inventory(self.profile)
        self.assertNotIn("files/etc/resolver.conf", result["files"])
        self.assertEqual(result["links"]["files/etc/resolver.conf"]["target"], str(outside))
        self.assertEqual(outside.read_text(), "do not read")

    def test_symlink_directory_is_not_walked(self):
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "private").write_text("private")
        (self.files / "wants").symlink_to(outside, target_is_directory=True)
        result = m.inventory(self.profile)
        self.assertIn("files/etc/wants", result["links"])
        self.assertFalse(any("private" in x for x in result["files"]))

    def test_source_parent_symlink_refused(self):
        link = self.base / "link"
        link.symlink_to(self.files, target_is_directory=True)
        profile = {**self.profile, "roots": [{"label": "etc", "path": str(link / "nftables.conf")}]}
        with self.assertRaisesRegex(m.Refused, "symlink_source_parent"):
            m.inventory(profile)

    def test_live_mongo_data_excluded(self):
        database = self.files / "db"
        database.mkdir()
        (database / "WiredTiger").write_text("live mutable database")
        profile = {**self.profile, "kind": "unifi", "mongo_data_roots": [str(database)]}
        self.assertEqual(list(m.inventory(profile)["files"]), ["files/etc/nftables.conf"])

    def test_unapproved_native_method_never_runs(self):
        with mock.patch.object(m, "run") as run:
            with self.assertRaisesRegex(m.Refused, "native_export_method_not_approved"):
                m.native_export({"kind": "unifi"}, self.base)
        run.assert_not_called()

    def test_old_native_timestamp_is_not_refreshed_by_collection(self):
        old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=20)).isoformat()
        self.assertFalse(m.fresh(old))
        future = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat()
        self.assertFalse(m.fresh(future))
        self.assertFalse(m.fresh("invalid"))
        self.assertTrue(m.fresh(m.utc()))

    def test_native_stale_backup_rejected(self):
        executable = self.files / "export"
        executable.write_text("fixture")
        executable.chmod(0o700)
        profile = {"kind": "unifi", "native_export": {"approved": True, "executable": str(executable), "sha256": "fixture"}}
        # No native operation; mock only the ownership/pin guard and subprocess.
        with mock.patch.object(m, "strict_path", side_effect=lambda p: Path(p)), mock.patch.object(m, "sha", return_value="fixture"), mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o100700)), mock.patch.object(m, "private_dir", return_value=self.base), mock.patch.object(m, "run", return_value=json.dumps({"native_created_utc": "2026-01-01T00:00:00Z"}).encode()):
            with self.assertRaisesRegex(m.Refused, "native_backup_stale_or_invalid"):
                m.native_export(profile, self.base)

    def test_missing_aruba_credential_or_slot_coverage_refused(self):
        executable = self.files / "export"
        executable.write_text("fixture")
        profile = {"kind": "aruba", "native_export": {"approved": True, "executable": str(executable), "sha256": "fixture"}}
        response = {"native_created_utc": m.utc(), "artifacts": {"running-config": "fixture", "startup-config": "fixture", "boot-metadata": "fixture"}, "method": "aruba-read-only", "read_only": True, "prompt_complete": True, "startup_slots_complete": True, "credentials_complete": False}
        response["startup_slots_complete"] = False
        with mock.patch.object(m, "strict_path", side_effect=lambda p: Path(p)), mock.patch.object(m, "sha", return_value="fixture"), mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=0, st_mode=0o100700)), mock.patch.object(m, "private_dir", return_value=self.base), mock.patch.object(m, "run", return_value=json.dumps(response).encode()):
            with self.assertRaisesRegex(m.Refused, "aruba_recovery_scope_incomplete"):
                m.native_export(profile, self.base)

    def test_failed_refresh_preserves_last_good_and_clears_incoming(self):
        self.stage_one()
        root = self.stage / "sources/router"
        before = (root / "CURRENT.json").read_bytes()
        def failed(*_args, **_kwargs):
            raise m.Refused("fixture_failure")
        with mock.patch.object(m, "ssh_capture", side_effect=failed), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            with self.assertRaises(m.Refused):
                m.stage_source(self.args, "router", self.profile)
        self.assertEqual((root / "CURRENT.json").read_bytes(), before)
        self.assertEqual(json.loads((root / "status.json").read_text())["status"], "failed")
        self.assertEqual(list((self.stage / ".incoming/router").iterdir()), [])
        self.assertEqual(m.verify_source(root, "router")["status"], "verified")

    def test_new_source_failure_cannot_mask_other_sources(self):
        self.stage_one()
        result = m.status(self.args)
        self.assertEqual(result["status"], "degraded")
        self.assertEqual(result["healthy_sources"], 1)
        self.assertEqual(result["required_sources"], 6)

    def test_corrupt_invalid_future_status_is_not_healthy(self):
        self.stage_one()
        path = self.stage / "sources/router/status.json"
        path.write_text(json.dumps({"status": "healthy", "last_success_utc": "2999-01-01T00:00:00Z"}))
        self.assertEqual(m.status(self.args)["sources"]["router"]["status"], "stale")
        path.write_text("not json")
        self.assertEqual(m.status(self.args)["sources"]["router"]["status"], "missing")

    def test_staging_only_removes_owned_old_generations_not_sources(self):
        for _ in range(3):
            self.stage_one()
        root = self.stage / "sources/router/generations"
        self.assertEqual(len(list(root.iterdir())), 2)
        self.assertTrue((self.files / "nftables.conf").exists())

    def test_unsafe_old_generation_blocks_before_current_publication(self):
        self.stage_one()
        root = self.stage / "sources/router"
        pointer = (root / "CURRENT.json").read_bytes()
        (root / "generations/20260101T000000Z-000000000001").symlink_to(self.files, target_is_directory=True)
        with mock.patch.object(m, "ssh_capture", side_effect=self.fake_capture), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            with self.assertRaisesRegex(m.Refused, "symlink_private_path"):
                m.stage_source(self.args, "router", self.profile)
        self.assertEqual((root / "CURRENT.json").read_bytes(), pointer)

    def test_selected_config_mutation_refuses_publication(self):
        original = m.Archive.add
        def changed(archive, *args, **kwargs):
            original(archive, *args, **kwargs)
            (self.files / "new-selected.conf").write_text("new config")
        with mock.patch.object(m.Archive, "add", changed), mock.patch.object(m, "ssh_capture", side_effect=self.fake_capture), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            with self.assertRaisesRegex(m.Refused, "configuration_changed_during_capture"):
                m.stage_source(self.args, "router", self.profile)
        self.assertFalse((self.stage / "sources/router/CURRENT.json").exists())

    def test_excluded_tmp_churn_does_not_change_selected_inventory(self):
        before = m.inventory(self.profile)
        (self.files / "churn.tmp").write_text("volatile")
        after = m.inventory(self.profile)
        self.assertEqual(m.stable_inventory(before), m.stable_inventory(after))

    def test_unsafe_archive_paths_and_symlinks_refused(self):
        for name, kind in (("../outside", tarfile.REGTYPE), ("symlink", tarfile.SYMTYPE)):
            path = self.base / "unsafe.tar"
            with tarfile.open(path, "w") as archive:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = "/outside" if kind == tarfile.SYMTYPE else ""
                archive.addfile(info)
            with self.assertRaisesRegex(m.Refused, "unsafe_archive_member"):
                m.verify_archive(path, "router")

    def test_payload_corruption_refused(self):
        self.stage_one()
        root = self.stage / "sources/router"
        generation = m.read_current(root)["current"]
        path = root / "generations" / generation / "network.tar"
        with tarfile.open(path) as archive:
            offset = archive.getmembers()[0].offset_data
        with path.open("r+b") as handle:
            handle.seek(offset)
            handle.write(b"X")
        with self.assertRaisesRegex(m.Refused, "archive_hash_mismatch"):
            m.verify_archive(path, "router")

    def test_required_native_and_gravity_members_cannot_be_omitted(self):
        for kind in ("pihole", "unifi", "aruba"):
            path = self.base / (kind + ".tar")
            with path.open("wb") as output:
                archive = m.Archive(output, kind)
                archive.json("RECOVERY.json", {"fixture": True})
                archive.finish()
            with self.assertRaisesRegex(m.Refused, "required_recovery_member_missing"):
                m.verify_archive(path, kind)

    def aruba_stage(self, complete):
        profile = {"kind": "aruba", "approved": True, "transport": "local", "roots": [], "commands": []}
        native = {"method": "aruba-read-only", "native_created_utc": m.utc(), "read_only": True,
                  "prompt_complete": True, "startup_slots_complete": True, "credentials_complete": complete}
        artifacts = {label: self.files / "nftables.conf" for label in ("running-config", "startup-config", "boot-metadata")}
        with mock.patch.object(m, "native_export", return_value=(native, artifacts)), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            return m.stage_source(self.args, "aruba", profile)

    def test_limited_aruba_export_is_preserved_without_complete_claim(self):
        result = self.aruba_stage(False)
        root = self.stage / "sources/aruba"
        self.assertEqual(result["status"], "limited")
        self.assertFalse(result["coverage_complete"])
        self.assertFalse((root / "CURRENT.json").exists())
        self.assertTrue((root / "LIMITED.json").exists())
        self.assertEqual(m.verify_source(root, "aruba")["status"], "verified_limited")
        self.assertEqual(m.status(self.args)["sources"]["aruba"]["status"], "limited")
        self.assertEqual(m.status(self.args)["status"], "degraded")

    def test_limited_aruba_never_replaces_complete_current_or_previous(self):
        self.aruba_stage(True)
        self.aruba_stage(True)
        root = self.stage / "sources/aruba"
        original = (root / "CURRENT.json").read_bytes()
        pointer = m.read_current(root)
        for _ in range(3):
            self.aruba_stage(False)
        self.assertEqual((root / "CURRENT.json").read_bytes(), original)
        for name in (pointer["current"], pointer["previous"]):
            self.assertTrue((root / "generations" / name / "network.tar").exists())
        self.assertEqual(len(list((root / "generations").iterdir())), 4)

    def test_extended_attributes_recorded_for_manual_recovery(self):
        file = self.files / "nftables.conf"
        try:
            os.setxattr(file, "user.recovery-fixture", b"metadata")
        except OSError:
            self.skipTest("fixture filesystem does not support user xattrs")
        result = m.meta(file)
        self.assertTrue(result["xattrs_supported"])
        self.assertEqual(result["xattrs_base64"]["user.recovery-fixture"], "bWV0YWRhdGE=")

    def test_global_attempt_failure_remains_explicit(self):
        self.stage_one()
        (self.stage / "status.json").write_text('{"last_attempt_status":"failed"}')
        self.assertEqual(m.status(self.args)["status"], "failed")
        self.assertEqual(m.status(self.args, honor_attempt=False)["status"], "degraded")

    def test_hub_native_merge_preserves_remote_hashes_without_extraction(self):
        config = self.base / "configuration.tar"
        with config.open("wb") as output:
            archive = m.Archive(output, "unifi-config")
            archive.add("files/configuration/settings", self.files / "nftables.conf")
            archive.json("RECOVERY.json", {"kind": "unifi", "native": {}})
            archive.finish()
        native_file = self.base / "native.unf"
        native_file.write_bytes(b"native consistent fixture")
        final = self.base / "final.tar"
        with final.open("wb") as output:
            m.merge_hub_native(config, output, {"method": "native-unifi-backup", "settings_complete": True, "native_created_utc": m.utc()}, {"network-backup": native_file})
        proof = m.verify_archive(final, "unifi")
        self.assertEqual(proof["members"], 3)
        self.assertEqual((self.files / "nftables.conf").read_text(), "fixture configuration")
        with tarfile.open(final) as archive:
            self.assertEqual(archive.extractfile("native/network-backup").read(), native_file.read_bytes())

    def test_unifi_mount_order_is_ignored_but_image_changes_are_not(self):
        rows = [{"Name": "/controller", "Image": "sha256:one", "Config": {}, "Mounts": [{"Destination": "/b", "Source": "/second"}, {"Destination": "/a", "Source": "/first"}]}]
        first = m.stable_reference("controller-inspect", json.dumps(rows))
        rows[0]["Mounts"].reverse()
        rows[0]["State"] = {"health": "volatile"}
        self.assertEqual(first, m.stable_reference("controller-inspect", json.dumps(rows)))
        rows[0]["Image"] = "sha256:changed"
        self.assertNotEqual(first, m.stable_reference("controller-inspect", json.dumps(rows)))

    def test_unifi_wrong_container_identity_refused(self):
        profile = {"expected_container_images": {"controller": "sha256:one", "mongo": "sha256:two"}}
        references = {"controller-inspect": {"output": json.dumps([{"Name": "/controller", "Image": "sha256:one", "State": {"Running": True}}])}, "database-inspect": {"output": json.dumps([{"Name": "/wrong", "Image": "sha256:two", "State": {"Running": True}}])}}
        with self.assertRaisesRegex(m.Refused, "unifi_container_identity_changed"):
            m.unifi_identity(profile, references)

    def test_legitimate_core_modules_and_packages_are_preserved(self):
        (self.files / "core.py").write_text("legitimate module")
        (self.files / "core").mkdir()
        (self.files / "core/__init__.py").write_text("legitimate package")
        files = m.inventory(self.profile)["files"]
        self.assertIn("files/etc/core.py", files)
        self.assertIn("files/etc/core/__init__.py", files)

    def test_legitimate_runtime_named_code_directories_are_not_globally_dropped(self):
        for name in ("log", "logs", "cache", "tmp", "temp", "core"):
            directory = self.files / name
            directory.mkdir()
            (directory / "__init__.py").write_text("legitimate service source")
        selected = m.inventory(self.profile)["files"]
        for name in ("log", "logs", "cache", "tmp", "temp", "core"):
            self.assertIn("files/etc/" + name + "/__init__.py", selected)

    def test_later_complete_aruba_export_is_not_degraded_by_retained_limited(self):
        self.aruba_stage(False)
        self.aruba_stage(True)
        root = self.stage / "sources/aruba"
        self.assertTrue((root / "LIMITED.json").exists())
        self.assertEqual(m.verify_source(root, "aruba")["status"], "verified")
        self.assertEqual(m.status(self.args)["sources"]["aruba"]["status"], "healthy")

    def test_status_cannot_be_healthy_when_payload_disappears(self):
        self.stage_one()
        root = self.stage / "sources/router"
        generation = m.read_current(root)["current"]
        (root / "generations" / generation / "network.tar").unlink()
        self.assertNotEqual(m.status(self.args)["sources"]["router"]["status"], "healthy")

    def test_incomplete_unifi_native_metadata_never_verifies_complete(self):
        artifact = self.files / "nftables.conf"
        for native in ({"settings_complete": False}, {"method": "native-unifi-backup", "settings_complete": False, "native_created_utc": m.utc()}, {"method": "native-unifi-backup", "settings_complete": True, "native_created_utc": "invalid"}):
            path = self.base / "incomplete.tar"
            with path.open("wb") as output:
                archive = m.Archive(output, "unifi")
                archive.add("native/network-backup", artifact)
                archive.json("RECOVERY.json", {"native": native})
                archive.finish()
            with self.assertRaisesRegex(m.Refused, "native_unifi_backup_incomplete"):
                m.verify_archive(path, "unifi")

    def test_old_native_time_keeps_new_collection_stale(self):
        self.aruba_stage(False)
        path = self.stage / "sources/aruba/status.json"
        health = json.loads(path.read_text())
        health["native_created_utc"] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
        path.write_text(json.dumps(health))
        self.assertEqual(m.status(self.args)["sources"]["aruba"]["status"], "limited_stale")

    def test_same_image_container_restart_changes_checkpoint(self):
        rows = [{"Id": "fixture", "Name": "/controller", "Image": "sha256:one", "Config": {}, "Mounts": [], "State": {"Running": True, "StartedAt": "before"}}]
        before = m.stable_reference("controller-inspect", json.dumps(rows))
        rows[0]["State"]["StartedAt"] = "after"
        self.assertNotEqual(before, m.stable_reference("controller-inspect", json.dumps(rows)))
        rows[0]["State"]["StartedAt"] = "before"
        rows[0]["Id"] = "recreated"
        self.assertNotEqual(before, m.stable_reference("controller-inspect", json.dumps(rows)))

    def test_hub_native_operation_is_bracketed_and_checkpoint_change_refused(self):
        profile = {**self.profile, "kind": "unifi", "native_export": {"location": "hub"}}
        events = []
        def capture(_args, _profile, output=None, checkpoint=False):
            if checkpoint:
                events.append("checkpoint")
                return json.dumps({"kind": "unifi-configuration-checkpoint", "sha256": str(len(events))}).encode()
            events.append("configuration")
            output.write(b"fixture configuration")
        def export(_profile, _temporary):
            events.append("native")
            return {}, {}
        with mock.patch.object(m, "ssh_capture", side_effect=capture), mock.patch.object(m, "native_export", side_effect=export), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            with self.assertRaisesRegex(m.Refused, "native_configuration_checkpoint_changed"):
                m.stage_source(self.args, "unifi", profile)
        self.assertEqual(events, ["checkpoint", "native", "configuration", "checkpoint"])
        self.assertFalse((self.stage / "sources/unifi/CURRENT.json").exists())

    def test_legitimate_posix_backslash_and_percent_names_roundtrip(self):
        names = ["network" + chr(92) + "x2dservice.conf", "network%5Cx2dservice.conf"]
        for name in names:
            (self.files / name).write_text("legitimate systemd configuration")
        inventory = m.inventory(self.profile)
        encoded = {name.rsplit("/", 1)[-1]: value["source"] for name, value in inventory["files"].items()}
        self.assertEqual(len(encoded), 3)
        for name in names:
            component = m.archive_relative(Path(name))
            self.assertNotIn(chr(92), component)
            self.assertEqual(unquote_to_bytes(component), os.fsencode(name))
            self.assertEqual(encoded[component], str(self.files / name))
        self.stage_one()
        self.assertEqual(m.verify_source(self.stage / "sources/router", "router")["status"], "verified")

    def test_local_native_temp_uses_separate_trusted_native_work(self):
        seen = []
        native = {"method": "aruba-read-only", "native_created_utc": m.utc(), "read_only": True,
                  "prompt_complete": True, "startup_slots_complete": True, "credentials_complete": False}
        artifacts = {label: self.files / "nftables.conf" for label in ("running-config", "startup-config", "boot-metadata")}
        def export(_profile, temporary):
            seen.append(temporary)
            return native, artifacts
        profile = {"kind": "aruba", "approved": True, "transport": "local", "roots": [], "commands": []}
        with mock.patch.object(m, "native_export", side_effect=export), mock.patch.object(m.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * m.MAX_BYTES)):
            m.stage_source(self.args, "aruba", profile)
        self.assertEqual(len(seen), 1)
        self.assertIn(self.native_work, seen[0].parents)
        self.assertNotIn(self.stage, seen[0].parents)

    def test_native_worker_uses_fixed_private_root_not_default_tmp(self):
        seen = []
        def exported(_profile, temporary):
            seen.append(temporary)
            raise m.Refused("fixture_stop_before_capture")
        with mock.patch.object(m, "native_export", side_effect=exported):
            with self.assertRaisesRegex(m.Refused, "fixture_stop_before_capture"):
                m.worker({"kind": "aruba", "approved": True, "roots": [], "commands": []}, io.BytesIO())
        self.assertEqual(len(seen), 1)
        self.assertIn(self.native_work, seen[0].parents)

    def test_real_native_work_guard_requires_root_before_filesystem_access(self):
        with mock.patch.object(m.os, "geteuid", return_value=1000):
            with self.assertRaisesRegex(m.Refused, "native_work_requires_root"):
                ORIGINAL_NATIVE_WORK_ROOT()


    def test_write_commands_refused(self):
        for argv in (["systemctl", "restart", "networking"], ["ip", "-j", "link", "delete", "eth0"], ["netplan", "apply"], ["nft", "-j", "list", "ruleset", "-f", "/fixture"], ["hostnamectl", "set-hostname", "bad"], ["docker", "exec", "fixture", "true"]):
            self.assertFalse(m.safe_read_command(argv), argv)
        self.assertTrue(m.safe_read_command(["ip", "-j", "route", "show", "table", "all"]))

    def test_command_failure_has_no_private_native_output(self):
        with self.assertRaisesRegex(m.Refused, "^capture_command_failed$"):
            m.run(["/bin/sh", "-c", "printf secret-private >&2; exit 3"])

    def test_output_and_deadline_are_bounded(self):
        with self.assertRaisesRegex(m.Refused, "command_output_limit"):
            m.run(["/bin/printf", "too long"], output_limit=1)
        m.END = time.monotonic() - 1
        with self.assertRaisesRegex(m.Refused, "deadline_exceeded"):
            m.run(["/bin/sleep", "5"])

    def test_current_pointer_cannot_escape_source_root(self):
        root = self.base / "pointer"
        root.mkdir()
        (root / "CURRENT.json").write_text('{"current":"../../outside"}')
        with self.assertRaisesRegex(m.Refused, "invalid_current_pointer"):
            m.read_current(root)


if __name__ == "__main__":
    unittest.main()
