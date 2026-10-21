"""Offline fixtures only: no production credentials, login, export or network."""

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import ssl
import stat
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock


SPEC = importlib.util.spec_from_file_location(
    "unifi_native_export", Path(__file__).resolve().parents[1] / "edsys-unifi-native-export.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)
PEER = b"offline certificate DER fixture"
PIN = hashlib.sha256(PEER).digest()
SECRET = "fixture-password-never-publish"
REAL_FSTAT = os.fstat


def api(value=None, **meta):
    return json.dumps({"meta": {"rc": "ok", **meta}, "data": [] if value is None else value}).encode()


class Response(io.BytesIO):
    def __init__(self, body, headers=None, status=200):
        super().__init__(body)
        self.status = status
        self.headers = headers or []

    def getheaders(self):
        return self.headers

    def getheader(self, name, default=None):
        values = [value for key, value in self.headers if key.lower() == name.lower()]
        return ", ".join(values) if values else default


class Socket:
    def getpeercert(self, binary_form=False):
        assert binary_form
        return PEER

    def settimeout(self, value):
        assert 0 < value <= 30


class Connection:
    def __init__(self, response, calls):
        self.response = response
        self.calls = calls
        self.sock = Socket()
        self.closed = False

    def connect(self):
        pass

    def request(self, method, path, body=None, headers=None):
        self.calls.append((method, path, body, headers))

    def getresponse(self):
        self.sock = None  # HTTP/1.0/Connection:close may detach the connection socket.
        return self.response

    def close(self):
        self.closed = True


class UniFiNativeExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.calls, self.connections = [], []

    def root_metadata(self, descriptor):
        """Only fixture UID and shared /tmp ancestry differ from a root deployment."""
        value = REAL_FSTAT(descriptor)
        mode = value.st_mode
        if os.readlink(f"/proc/self/fd/{descriptor}") == tempfile.gettempdir():
            mode &= ~0o022
        return SimpleNamespace(st_uid=0, st_mode=mode, st_ino=value.st_ino,
                               st_nlink=value.st_nlink, st_size=value.st_size,
                               st_mtime_ns=value.st_mtime_ns, st_ctime_ns=value.st_ctime_ns)

    @contextlib.contextmanager
    def rooted_fixture(self):
        with mock.patch.object(m.os, "fstat", side_effect=self.root_metadata):
            yield

    @contextlib.contextmanager
    def responses(self, *responses):
        pending = list(responses)

        def factory(host, port, **kwargs):
            self.assertEqual((host, port), ("192.168.50.1", 8443))
            self.assertIn("context", kwargs)
            self.assertTrue(pending, "unexpected extra request/retry")
            connection = Connection(pending.pop(0), self.calls)
            self.connections.append(connection)
            return connection

        with mock.patch.object(m.http.client, "HTTPSConnection", side_effect=factory):
            yield

    def client(self, pin=PIN):
        return m.Client(object(), pin, time.monotonic() + 20)

    def backup_response(self, body=b"\x03native opaque fixture\x00" * 20, headers=None):
        return Response(body, headers if headers is not None else [
            ("Content-Type", "application/octet-stream"),
            ("Content-Disposition", 'attachment; filename="network_backup_fixture_v10.0.162.unf"'),
            ("Content-Length", str(len(body)))])

    @contextlib.contextmanager
    def collect_fixture(self, responses):
        account = {"username": "fixture-admin", "password": SECRET, "persist_for_backups": True}
        with (self.rooted_fixture(), self.responses(*responses),
              mock.patch.object(m.os, "geteuid", return_value=0),
              mock.patch.object(m, "private_file", return_value=json.dumps(account).encode()),
              mock.patch.object(m, "tls_context", return_value=(object(), PIN))):
            yield

    def complete_responses(self):
        return [Response(api(server_version=m.QUALIFIED_VERSION)),
                Response(api(), [("Set-Cookie", "unifises=abc.def-_123; Secure; HttpOnly"),
                                 ("Set-Cookie", "csrf_token=csrf-fixture; Secure")]),
                Response(api([{"url": "/dl/backup/network_backup_fixture_v10.0.162.unf"}])),
                self.backup_response(),
                Response(api(), [("Set-Cookie", "unifises=; Max-Age=0")])]

    def test_success_protocol_native_action_receipt_and_private_files(self):
        with self.collect_fixture(self.complete_responses()):
            result = m.collect(self.base)
        self.assertEqual(set(result), {"method", "native_created_utc", "settings_complete", "artifacts"})
        self.assertEqual(result["method"], "native-unifi-backup")
        self.assertTrue(result["settings_complete"])
        self.assertEqual(set(result["artifacts"]), {"network-backup", "native-proof"})
        receipt = json.loads(Path(result["artifacts"]["native-proof"]).read_bytes())
        self.assertLessEqual(receipt["action_started_utc"], result["native_created_utc"])
        self.assertEqual(receipt["action_completed_utc"], result["native_created_utc"])
        self.assertFalse(receipt["embedded_native_timestamp_verified"])
        self.assertFalse(receipt["restore_verified"])
        self.assertFalse(receipt["statistics_included"])
        self.assertEqual(receipt["session_logout"], "succeeded")
        binary = Path(result["artifacts"]["network-backup"]).read_bytes()
        self.assertEqual(receipt["native_file"]["sha256"], hashlib.sha256(binary).hexdigest())
        for path in result["artifacts"].values():
            self.assertEqual(Path(path).parent, self.base)
            self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
            self.assertNotIn(SECRET.encode(), Path(path).read_bytes())
        self.assertEqual([(x[0], x[1]) for x in self.calls], [
            ("GET", "/status"), ("POST", "/api/login"), ("POST", "/api/s/default/cmd/backup"),
            ("GET", "/dl/backup/network_backup_fixture_v10.0.162.unf"), ("POST", "/api/logout")])
        self.assertEqual(json.loads(self.calls[1][2]), {"username": "fixture-admin", "password": SECRET,
                                                     "remember": False, "strict": True})
        self.assertEqual(json.loads(self.calls[2][2]), {"cmd": "backup", "days": 0})
        self.assertEqual(self.calls[2][3]["X-Csrf-Token"], "csrf-fixture")
        self.assertEqual(self.calls[-1][2], None)
        self.assertTrue(all(connection.closed for connection in self.connections))

    def test_pin_mismatch_refuses_before_credentials_sent(self):
        with self.responses(Response(api())):
            with self.assertRaisesRegex(m.Refused, "tls_peer_pin_mismatch"):
                self.client(b"wrong explicit pin").json("POST", "/api/login", {"password": SECRET})
        self.assertEqual(self.calls, [])
        self.assertTrue(self.connections[0].closed)

    def test_tls_context_sole_anchor_and_explicit_leaf_pin(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl needed only to generate an ephemeral offline certificate")
        certificate, key = self.base / "leaf.pem", self.base / "ephemeral.key"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                        "-subj", "/CN=offline-fixture.invalid", "-keyout", str(key), "-out", str(certificate)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        context, fingerprint = m.tls_context(certificate.read_bytes())
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertFalse(context.check_hostname)
        self.assertEqual(context.minimum_version, ssl.TLSVersion.TLSv1_2)
        self.assertEqual(len(context.get_ca_certs(binary_form=True)), 1)
        self.assertEqual(fingerprint, hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate.read_text())).digest())
        with self.assertRaisesRegex(m.Refused, "invalid_pinned_certificate"):
            m.tls_context(certificate.read_bytes() * 2)

    def test_redirect_is_never_followed_or_error_body_read(self):
        response = Response(SECRET.encode(), [("Location", "https://outside.invalid/")], status=302)
        with self.responses(response), mock.patch.object(response, "read", wraps=response.read) as read:
            with self.assertRaisesRegex(m.Refused, "http_operation_refused"):
                self.client().json("POST", "/api/login", {"password": SECRET})
            read.assert_not_called()
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(response.closed)

    def test_unapproved_host_path_methods_never_connect(self):
        with mock.patch.object(m.http.client, "HTTPSConnection") as connection:
            for method, path in [("GET", "https://outside.invalid/"), ("POST", "/api/revoke"),
                                 ("GET", "/api/login"), ("POST", "/api/s/other/cmd/backup")]:
                with self.subTest(path=path), self.assertRaisesRegex(m.Refused, "unapproved_request"):
                    self.client().json(method, path)
        connection.assert_not_called()

    def test_download_url_containment(self):
        for path in ["https://192.168.50.1:8443/dl/backup/x.unf", "//outside/dl/backup/x.unf",
                     "/dl/backup/../x.unf", "/dl/backup/%2e%2e.unf", "/dl/backup/x.unf?token=value",
                     "/dl/backup/x.unf#fragment", "/dl/autobackup/old.unf", "/dl/backup/x.zip", None]:
            with self.subTest(path=path), self.assertRaisesRegex(m.Refused, "unsafe_download_path"):
                m.download_path(path)

    def test_json_size_and_truncation_bounds(self):
        for body, header, reason in [(b"x" * (m.MAX_JSON + 1), [], "api_response_oversized"),
                                     (b"", [("Content-Length", str(m.MAX_JSON + 1))], "api_response_oversized"),
                                     (api(), [("Content-Length", "1000")], "api_response_truncated")]:
            with self.subTest(reason=reason), self.responses(Response(body, header)):
                with self.assertRaisesRegex(m.Refused, reason):
                    self.client().json("GET", "/status")

    def test_api_failure_and_malformed_body_do_not_leak(self):
        for body in [b"not json " + SECRET.encode(), json.dumps({"meta": {"rc": "error", "msg": SECRET}}).encode()]:
            with self.responses(Response(body)):
                with self.assertRaises(m.Refused) as error:
                    self.client().json("GET", "/status")
            self.assertNotIn(SECRET, str(error.exception))

    def test_download_bounds_type_attachment_and_truncation(self):
        cases = [([("Content-Type", "text/html")], "invalid_backup_content_type"),
                 ([("Content-Type", "application/octet-stream"), ("Content-Disposition", "inline")], "invalid_backup_attachment"),
                 ([("Content-Type", "application/octet-stream"), ("Content-Disposition", 'attachment; filename="x.unf"'),
                   ("Content-Length", str(m.MAX_BACKUP + 1))], "invalid_backup_size"),
                 ([("Content-Type", "application/octet-stream"), ("Content-Disposition", 'attachment; filename="x.unf"'),
                   ("Content-Length", "1000")], "backup_truncated_or_empty")]
        for headers, reason in cases:
            with self.subTest(reason=reason), self.responses(self.backup_response(headers=headers)):
                with self.assertRaisesRegex(m.Refused, reason):
                    self.client().download("/dl/backup/x.unf", io.BytesIO())

    def test_streamed_backup_size_bound_without_content_length(self):
        headers = [("Content-Type", "application/octet-stream"),
                   ("Content-Disposition", 'attachment; filename="x.unf"')]
        with mock.patch.object(m, "MAX_BACKUP", 256), self.responses(self.backup_response(b"x" * 257, headers)):
            with self.assertRaisesRegex(m.Refused, "invalid_backup_size"):
                self.client().download("/dl/backup/x.unf", io.BytesIO())

    def test_qualified_download_without_disposition_keeps_other_guards(self):
        body = b"opaque native fixture" * 20
        headers = [("Content-Type", "application/octet-stream"), ("Content-Length", str(len(body)))]
        destination = io.BytesIO()
        with self.responses(self.backup_response(body, headers)):
            result = self.client().download("/dl/backup/fresh-native.unf", destination)
        self.assertEqual(destination.getvalue(), body)
        self.assertEqual(result, {"bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()})
        for invalid in ["", "inline", 'attachment; filename="wrong.zip"']:
            with self.responses(self.backup_response(body, [*headers, ("Content-Disposition", invalid)])):
                with self.assertRaisesRegex(m.Refused, "invalid_backup_attachment"):
                    self.client().download("/dl/backup/fresh-native.unf", io.BytesIO())

    def test_response_header_and_encoding_bounds(self):
        for headers, reason in [([("X-Large", "x" * 16385)], "response_headers_oversized"),
                                ([("Content-Encoding", "gzip")], "unexpected_content_encoding"),
                                ([("Transfer-Encoding", "chunked"), ("Content-Length", "10")], "ambiguous_transfer_encoding")]:
            with self.subTest(reason=reason), self.responses(Response(api(), headers)):
                with self.assertRaisesRegex(m.Refused, reason):
                    self.client().json("GET", "/status")

    def test_cookies_are_memory_only_and_bounded(self):
        with self.responses(Response(api(), [("Set-Cookie", "unifises=" + "a" * 8193)])):
            with self.assertRaisesRegex(m.Refused, "invalid_session_cookie"):
                self.client().json("POST", "/api/login", {})

    def test_deadline_exceeded_before_any_connection(self):
        with mock.patch.object(m.http.client, "HTTPSConnection") as connection:
            with self.assertRaisesRegex(m.Refused, "deadline_exceeded"):
                m.Client(object(), PIN, time.monotonic() - 1).json("GET", "/status")
        connection.assert_not_called()

    def test_root_required_before_files_or_connection(self):
        with mock.patch.object(m.os, "geteuid", return_value=1000), mock.patch.object(m, "private_file") as read:
            with self.assertRaisesRegex(m.Refused, "root_required"):
                m.collect(self.base)
        read.assert_not_called()

    def test_credentials_persistence_schema_is_explicit(self):
        valid = {"username": "fixture", "password": SECRET, "persist_for_backups": True}
        self.assertEqual(m.credentials(json.dumps(valid))["username"], "fixture")
        for invalid in [[], {}, {**valid, "persist_for_backups": False}, {**valid, "extra": "field"},
                        {**valid, "password": ""}, {**valid, "password": "newline\n"}, {**valid, "username": 1}]:
            with self.subTest(value=type(invalid).__name__), self.assertRaisesRegex(m.Refused, "invalid_credentials_file"):
                m.credentials(json.dumps(invalid))

    def test_files_symlinks_modes_hardlinks_and_writable_ancestor(self):
        private = self.base / "private"
        private.write_bytes(b"fixture")
        private.chmod(0o600)
        with self.rooted_fixture():
            self.assertEqual(m.private_file(private), b"fixture")
            private.chmod(0o644)
            with self.assertRaisesRegex(m.Refused, "unsafe_private_file"):
                m.private_file(private)
            private.chmod(0o600)
            link = self.base / "link"
            link.symlink_to(private)
            with self.assertRaises(OSError):
                m.private_file(link)
            os.link(private, self.base / "hardlink")
            with self.assertRaisesRegex(m.Refused, "unsafe_private_file"):
                m.private_file(private)
            directory = self.base / "writable"
            directory.mkdir(mode=0o777)
            directory.chmod(0o777)
            with self.assertRaisesRegex(m.Refused, "unsafe_private_ancestor"):
                with m.private_directory(directory):
                    self.fail("unsafe directory opened")
            parentlink = self.base / "directory-link"
            parentlink.symlink_to(self.base, target_is_directory=True)
            with self.assertRaises(OSError):
                with m.private_directory(parentlink):
                    self.fail("symlink directory followed")

    def test_output_directory_exact_mode_and_path(self):
        self.base.chmod(0o755)
        with self.rooted_fixture():
            with self.assertRaisesRegex(m.Refused, "unsafe_output_directory"):
                with m.private_directory(self.base, exact_mode=0o700):
                    self.fail("public output directory accepted")
        for path in ["relative", "/safe/../escape", "/safe//double", "/safe/./dot", "/trailing/", "/nul\x00path"]:
            with self.subTest(path=path), self.assertRaisesRegex(m.Refused, "unsafe_private_path"):
                m.absolute_path(path)

    def test_preexisting_artifact_never_overwritten_or_logged_in(self):
        existing = self.base / m.BACKUP_NAME
        existing.write_bytes(b"last-good")
        with self.collect_fixture([]):
            with self.assertRaises(FileExistsError):
                m.collect(self.base)
        self.assertEqual(existing.read_bytes(), b"last-good")
        self.assertEqual(self.calls, [])

    def test_second_reserved_file_failure_preserves_existing_receipt(self):
        existing = self.base / m.RECEIPT_NAME
        existing.write_bytes(b"last-good receipt")
        with self.collect_fixture([]):
            with self.assertRaises(FileExistsError):
                m.collect(self.base)
        self.assertEqual(existing.read_bytes(), b"last-good receipt")
        self.assertFalse((self.base / m.BACKUP_NAME).exists())
        self.assertEqual(self.calls, [])

    def test_version_drift_refuses_before_login_and_removes_new_files(self):
        with self.collect_fixture([Response(api(server_version="future-version"))]):
            with self.assertRaisesRegex(m.Refused, "unqualified_controller_version"):
                m.collect(self.base)
        self.assertEqual([(x[0], x[1]) for x in self.calls], [("GET", "/status")])
        self.assertEqual(list(self.base.iterdir()), [])

    def test_failed_backup_attempt_logs_out_once_and_cleans_owned_files(self):
        responses = self.complete_responses()[:2] + [Response(b"private response " + SECRET.encode(), status=500), Response(api())]
        with self.collect_fixture(responses):
            with self.assertRaisesRegex(m.Refused, "http_operation_refused"):
                m.collect(self.base)
        self.assertEqual(sum(path == "/api/logout" for _, path, _, _ in self.calls), 1)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_logout_failure_is_not_retried_or_published(self):
        responses = self.complete_responses()[:-1] + [Response(b"private response " + SECRET.encode(), status=500)]
        with self.collect_fixture(responses):
            with self.assertRaisesRegex(m.Refused, "http_operation_refused"):
                m.collect(self.base)
        self.assertEqual(sum(path == "/api/logout" for _, path, _, _ in self.calls), 1)
        self.assertEqual(list(self.base.iterdir()), [])

    def test_main_stdout_only_success_and_errors_are_fixed(self):
        for failure in [m.Refused("http_operation_refused"), OSError(SECRET), ssl.SSLError(SECRET)]:
            out, err = io.StringIO(), io.StringIO()
            with mock.patch.object(m, "collect", side_effect=failure), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertEqual(m.main(["--output-directory", str(self.base)]), 1)
            self.assertEqual(out.getvalue(), "")
            self.assertNotIn(SECRET, err.getvalue())
            self.assertNotIn("Traceback", err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(m.main(["--password", SECRET]), 1)
        self.assertEqual(out.getvalue(), "")
        self.assertNotIn(SECRET, err.getvalue())


if __name__ == "__main__":
    unittest.main()
