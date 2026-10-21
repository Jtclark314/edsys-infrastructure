#!/usr/bin/env python3
"""Settings-only native export for the qualified standalone UniFi 10.0.162 API.

Root-only; accepts only --output-directory EXISTING_PRIVATE_DIRECTORY. Credentials
are exactly {username, password, persist_for_backups:true} in the root-owned 0600
file below. The separate 0600 PEM contains exactly the controller leaf certificate
obtained through the operator's pinned router SSH connection. It is the sole TLS
trust anchor AND the explicit SHA256 peer pin, checked before every HTTP request.
Hostname verification is disabled only for this exact certificate-pinned endpoint.

No redirects, browser sessions, credential arguments, raw response logging, ZIP
assumptions, restore attempts, provisioning or service actions. The receipt dates
the successful new synchronous creation action using hub UTC clocks; it does not
claim to decode an embedded native archive timestamp or prove a restore. Settings
coverage follows the installed API's days=0 contract; statistics are excluded.
Installed public UI also qualifies session-scoped POST /api/logout and the
csrf_token cookie / X-Csrf-Token header. Cookie state exists only in this process.
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import hmac
import http.client
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import ssl
import stat
import sys
import time


HOST = "192.168.50.1"
PORT = 8443
QUALIFIED_VERSION = "10.0.162"
CREDENTIALS = Path("/etc/edsys-backup/network-credentials/unifi.json")
CERTIFICATE = CREDENTIALS.with_name("unifi-tls.pem")
BACKUP_NAME = "unifi-settings.unf"
RECEIPT_NAME = "unifi-native-proof.json"
MAX_JSON = 65536
MAX_BACKUP = 128 * 1024 * 1024
MAX_SECONDS = 240
DOWNLOAD_PATH = re.compile(r"/dl/backup/[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.unf\Z")


class Refused(Exception):
    """Fixed safe reason only; never attach a response, cookie or credential."""


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def remaining(deadline):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise Refused("deadline_exceeded")
    return min(seconds, 30)


def absolute_path(path):
    value = os.fspath(path)
    if not value.startswith("/") or "\x00" in value or any(p in {".", "..", ""} for p in value.split("/")[1:]):
        raise Refused("unsafe_private_path")
    return Path(value)


@contextlib.contextmanager
def private_directory(path, exact_mode=None):
    """FD-anchored traversal rejects symlinks and writable/non-root ancestors."""
    path = absolute_path(path)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in [None, *path.parts[1:]]:
            if component is not None:
                next_fd = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = next_fd
            value = os.fstat(descriptor)
            if value.st_uid != 0 or stat.S_IMODE(value.st_mode) & 0o022:
                raise Refused("unsafe_private_ancestor")
        if exact_mode is not None and stat.S_IMODE(value.st_mode) != exact_mode:
            raise Refused("unsafe_output_directory")
        yield descriptor
    finally:
        os.close(descriptor)


def private_file(path):
    path = absolute_path(path)
    with private_directory(path.parent) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            value = os.fstat(handle.fileno())
            if (not stat.S_ISREG(value.st_mode) or value.st_uid != 0
                    or stat.S_IMODE(value.st_mode) != 0o600 or value.st_nlink != 1
                    or not 0 < value.st_size <= MAX_JSON):
                raise Refused("unsafe_private_file")
            result = handle.read(MAX_JSON + 1)
            if len(result) != value.st_size:
                raise Refused("private_file_changed_or_oversized")
            after = os.fstat(handle.fileno())
            if (value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns) != (
                    after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise Refused("private_file_changed_or_oversized")
            return result


def credentials(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise Refused("invalid_credentials_file") from None
    if (not isinstance(value, dict) or set(value) != {"username", "password", "persist_for_backups"}
            or value["persist_for_backups"] is not True
            or any(not isinstance(value[k], str) or not value[k] or len(value[k]) > 4096
                   or any(ord(c) < 32 for c in value[k]) for k in ("username", "password"))):
        raise Refused("invalid_credentials_file")
    return value


def tls_context(data):
    try:
        pem = data.decode("ascii").strip()
        if (pem.count("-----BEGIN CERTIFICATE-----") != 1
                or pem.count("-----END CERTIFICATE-----") != 1
                or not pem.startswith("-----BEGIN CERTIFICATE-----")
                or not pem.endswith("-----END CERTIFICATE-----")):
            raise ValueError
        der = ssl.PEM_cert_to_DER_cert(pem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_REQUIRED
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(cadata=pem)
    except (ValueError, UnicodeError, ssl.SSLError):
        raise Refused("invalid_pinned_certificate") from None
    return context, hashlib.sha256(der).digest()


def download_path(value):
    if not isinstance(value, str) or not DOWNLOAD_PATH.fullmatch(value) or ".." in value:
        raise Refused("unsafe_download_path")
    return value


def successful_json(data):
    try:
        value = json.loads(data)
    except (ValueError, UnicodeError):
        raise Refused("invalid_api_response") from None
    if not isinstance(value, dict) or not isinstance(value.get("meta"), dict) or value["meta"].get("rc") != "ok":
        raise Refused("api_operation_refused")
    return value


class Client:
    def __init__(self, context, fingerprint, deadline):
        self.context = context
        self.fingerprint = fingerprint
        self.deadline = deadline
        self.cookies = {}

    def connection(self):
        connection = http.client.HTTPSConnection(HOST, PORT, context=self.context, timeout=remaining(self.deadline))
        try:
            connection.connect()
            peer = connection.sock.getpeercert(binary_form=True)
            if not peer or not hmac.compare_digest(hashlib.sha256(peer).digest(), self.fingerprint):
                raise Refused("tls_peer_pin_mismatch")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextlib.contextmanager
    def request(self, method, path, body=None):
        allowed = {("GET", "/status"), ("POST", "/api/login"), ("POST", "/api/logout"),
                   ("POST", "/api/s/default/cmd/backup")}
        if (method, path) not in allowed and not (method == "GET" and DOWNLOAD_PATH.fullmatch(path)):
            raise Refused("unapproved_request")
        connection = self.connection()  # peer pin checked before credentials are serialized or sent
        response = None
        try:
            headers = {"Accept": "application/json" if body is not None or path == "/status" else "*/*",
                       "Accept-Encoding": "identity"}
            if self.cookies:
                headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in self.cookies.items())
                if "csrf_token" in self.cookies:
                    headers["X-Csrf-Token"] = self.cookies["csrf_token"]
            payload = None
            if body is not None:
                payload = json.dumps(body, separators=(",", ":")).encode()
                headers["Content-Type"] = "application/json"
            connection.sock.settimeout(remaining(self.deadline))
            connection.request(method, path, body=payload, headers=headers)
            transport = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                raise Refused("http_operation_refused")  # redirects and raw error bodies are never followed/read
            if sum(len(k) + len(v) for k, v in response.getheaders()) > 16384:
                raise Refused("response_headers_oversized")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise Refused("unexpected_content_encoding")
            transfer = response.getheader("Transfer-Encoding")
            if transfer is not None and (transfer.lower() != "chunked" or response.getheader("Content-Length") is not None):
                raise Refused("ambiguous_transfer_encoding")
            for key, value in response.getheaders():
                if key.lower() == "set-cookie":
                    cookie = SimpleCookie()
                    cookie.load(value)
                    if not cookie:
                        raise Refused("invalid_session_cookie")
                    for name, item in cookie.items():
                        if not item.value:
                            self.cookies.pop(name, None)
                            continue
                        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)
                                or not re.fullmatch(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]{1,8192}", item.value)):
                            raise Refused("invalid_session_cookie")
                        self.cookies[name] = item.value
            if len(self.cookies) > 16 or sum(len(v) for v in self.cookies.values()) > 8192:
                raise Refused("session_cookies_oversized")
            yield response, transport
        finally:
            if response is not None:
                response.close()
            connection.close()

    def json(self, method, path, body=None):
        with self.request(method, path, body) as (response, transport):
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > MAX_JSON):
                raise Refused("api_response_oversized")
            transport.settimeout(remaining(self.deadline))
            data = response.read(MAX_JSON + 1)
            remaining(self.deadline)
            if len(data) > MAX_JSON:
                raise Refused("api_response_oversized")
            if length is not None and len(data) != int(length):
                raise Refused("api_response_truncated")
            return successful_json(data)

    def download(self, path, handle):
        path = download_path(path)
        with self.request("GET", path) as (response, transport):
            if response.getheader("Content-Type", "").strip().lower() != "application/octet-stream":
                raise Refused("invalid_backup_content_type")
            # This qualified controller omits disposition on native /dl/backup responses.
            disposition = response.getheader("Content-Disposition")
            if disposition is not None and not re.fullmatch(r'attachment;\s*filename="[A-Za-z0-9][A-Za-z0-9_.-]{0,180}\.unf"', disposition, re.IGNORECASE):
                raise Refused("invalid_backup_attachment")
            length = response.getheader("Content-Length")
            if length is not None and (not length.isdigit() or not 128 <= int(length) <= MAX_BACKUP):
                raise Refused("invalid_backup_size")
            digest, count = hashlib.sha256(), 0
            while True:
                transport.settimeout(remaining(self.deadline))
                block = response.read(min(65536, MAX_BACKUP + 1 - count))
                if not block:
                    break
                count += len(block)
                if count > MAX_BACKUP:
                    raise Refused("invalid_backup_size")
                digest.update(block)
                handle.write(block)
            remaining(self.deadline)
            if count < 128 or (length is not None and count != int(length)):
                raise Refused("backup_truncated_or_empty")
            return {"bytes": count, "sha256": digest.hexdigest()}


def collect(output_directory):
    if os.geteuid() != 0:
        raise Refused("root_required")
    output_directory = absolute_path(output_directory)
    deadline = time.monotonic() + MAX_SECONDS
    context, fingerprint = tls_context(private_file(CERTIFICATE))
    account = credentials(private_file(CREDENTIALS))
    client = Client(context, fingerprint, deadline)
    with private_directory(output_directory, exact_mode=0o700) as output:
        owned, completed, logged_in = [], False, False
        try:
            # Reserve both destinations before login; never overwrite prior recovery material.
            handles = []
            with contextlib.ExitStack() as stack:
                for name in (BACKUP_NAME, RECEIPT_NAME):
                    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=output)
                    owned.append(name)
                    os.fchmod(descriptor, 0o600)
                    handles.append(stack.enter_context(os.fdopen(descriptor, "wb")))
                backup, receipt_file = handles
                version = client.json("GET", "/status")["meta"].get("server_version")
                if version != QUALIFIED_VERSION:
                    raise Refused("unqualified_controller_version")
                client.json("POST", "/api/login", {"username": account["username"], "password": account["password"],
                                                    "remember": False, "strict": True})
                logged_in = True
                started = utc()
                response = client.json("POST", "/api/s/default/cmd/backup", {"cmd": "backup", "days": 0})
                created = utc()
                data = response.get("data")
                if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
                    raise Refused("invalid_backup_response")
                path = download_path(data[0].get("url"))
                metadata = client.download(path, backup)
                logged_in = False
                client.json("POST", "/api/logout")
                receipt = {"format": 1, "method": "native-unifi-backup", "controller_version": version,
                           "qualified_version": QUALIFIED_VERSION, "endpoint": f"https://{HOST}:{PORT}",
                           "tls_leaf_sha256": fingerprint.hex(), "command": {"cmd": "backup", "days": 0},
                           "action_started_utc": started, "action_completed_utc": created,
                           "native_created_utc": created,
                           "native_created_utc_basis": "hub UTC at successful new synchronous native creation response; bounded by action_started_utc/action_completed_utc",
                           "download_completed_utc": utc(), "native_file": metadata, "settings_complete": True,
                           "statistics_included": False, "session_logout": "succeeded",
                           "archive_format": "opaque vendor .unf; not decoded or asserted to be ZIP",
                           "restore_verified": False, "embedded_native_timestamp_verified": False}
                receipt_file.write((json.dumps(receipt, sort_keys=True) + "\n").encode())
                for handle in handles:
                    handle.flush()
                    os.fsync(handle.fileno())
            os.fsync(output)
            completed = True
            return {"method": "native-unifi-backup", "native_created_utc": created, "settings_complete": True,
                    "artifacts": {"network-backup": str(output_directory / BACKUP_NAME),
                                  "native-proof": str(output_directory / RECEIPT_NAME)}}
        finally:
            if logged_in:
                with contextlib.suppress(Exception):
                    client.json("POST", "/api/logout")
            client.cookies.clear()
            account.clear()
            if not completed:
                for name in owned:
                    os.unlink(name, dir_fd=output)


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise Refused("invalid_arguments")


def main(argv=None):
    try:
        parser = Parser(description=__doc__, allow_abbrev=False)
        parser.add_argument("--output-directory", required=True)
        args = parser.parse_args(argv)
        print(json.dumps(collect(args.output_directory), sort_keys=True))
        return 0
    except Refused as error:
        reason = str(error)
    except ssl.SSLError:
        reason = "tls_verification_failed"
    except TimeoutError:
        reason = "operation_timed_out"
    except Exception:
        reason = "operation_failed"
    print("unifi_native_export_failed:" + reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
