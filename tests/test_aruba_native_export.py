"""Synthetic offline Aruba CLI replay. Never opens production files or sockets."""

import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import paramiko


SPEC = importlib.util.spec_from_file_location(
    "aruba_native_export", Path(__file__).resolve().parents[1] / "scripts/backup/edsys-aruba-native-export.py")
m = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(m)

PROMPT = "fixture-switch#"
APPROVED_COMMANDS = (
    "show version", "show flash", "show config files", "show config status",
    "show running-config", "show config",
)
ACCOUNT = {"username": "synthetic-admin", "password": "SYNTHETIC-DO-NOT-PRINT",
           "persist_for_backups": True}
CONFIG = ("; SYNTHETIC Configuration Editor; Created on release #KB.16.99.0001\n"
          "; Ver #synthetic\n"
          'hostname "fixture-switch"\n'
          "vlan 1\n   name \"fixture\"\n   exit\n")
SLOTS = ("Configuration files:\n"
         " id | act pri sec | name\n"
         " ---+-------------+----------------\n"
         " 1  |  *   *   *  | fixture-config\n"
         " 2  |             |\n"
         " 3  |             |\n"
         " 4  |             |\n"
         " 5  |             |\n")


def synthetic_key():
    # Public-only mathematical fixture; no generated/private identity or file.
    message = paramiko.Message()
    message.add_string("ssh-rsa")
    message.add_mpint(65537)
    message.add_mpint((1 << 1023) + 643)
    return paramiko.RSAKey(data=message.asbytes())


def known_hosts():
    key = synthetic_key()
    return f"{m.HOST} ssh-rsa {base64.b64encode(key.asbytes()).decode()}\n".encode()


def responses(slots=SLOTS, running=CONFIG, startup=CONFIG):
    bodies = {
        "show version": "SYNTHETIC switch\nImage stamp: KB.16.99.0001\nBoot Image: Primary\n",
        "show flash": "Primary Image : 12345 01/01/26 KB.16.99.0001\nSecondary Image : 12345 01/01/25 KB.16.98.0001\nDefault Boot Image : Primary\n",
        "show config files": slots,
        "show config status": "Running configuration is the same as the startup configuration.\n",
        "show running-config": "Running configuration:\n" + running,
        "show config": "Startup configuration:\n" + startup,
    }
    return {command: [(command + "\r\n" + body.replace("\n", "\r\n") + PROMPT).encode()]
            for command, body in bodies.items()}


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Channel:
    def __init__(self, transcript=None, banner=False):
        self.responses = transcript or responses()
        self.closed = False
        self.sent = []
        self.queue = [b"Press any key to continue"] if banner else [("\r\n" + PROMPT).encode()]
        self.awaiting_banner = banner
        self.pages = []
        self.timeouts = []

    def sendall(self, data):
        self.sent.append(data)
        if data == "\r" and self.awaiting_banner:
            self.awaiting_banner = False
            self.queue.append(("\r\n" + PROMPT).encode())
        elif data == " ":
            if not self.pages:
                raise AssertionError("unsolicited pagination")
            self.queue.extend(self.pages.pop(0))
        else:
            if not data.endswith("\r") or data[:-1] not in APPROVED_COMMANDS:
                raise AssertionError("non-allowlisted CLI input")
            segments = self.responses[data[:-1]]
            self.queue.extend(segments[0] if isinstance(segments[0], list) else segments)
            self.pages = segments[1:] if isinstance(segments[0], list) else []

    def recv_ready(self):
        return bool(self.queue)

    def recv(self, _size):
        return self.queue.pop(0)

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


class Client:
    def __init__(self, channel):
        self.channel = channel
        self.host_keys = paramiko.HostKeys()
        self.policy = None
        self.closed = False
        self.connections = []

    def get_host_keys(self):
        return self.host_keys

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def connect(self, *args, **kwargs):
        self.connections.append((args, kwargs))

    def invoke_shell(self, **kwargs):
        if kwargs != {"width": 180, "height": 24}:
            raise AssertionError("unexpected PTY request")
        return self.channel

    def close(self):
        self.closed = True


@contextlib.contextmanager
def fixture_directory(path, exact_mode=None):
    # Real private artifact writes below a temporary fixture. Only the root/
    # ancestor check is substituted, never the O_EXCL/NOFOLLOW write operation.
    if exact_mode is not None:
        assert stat.S_IMODE(Path(path).stat().st_mode) == exact_mode
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


class ArubaExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / "private"
        self.output.mkdir(mode=0o700)
        self.clock = Clock()

    @contextlib.contextmanager
    def replay(self, channel=None):
        channel = channel or Channel()
        client = Client(channel)
        def private_file(path, parent_mode=None):
            if path == m.CREDENTIALS:
                self.assertEqual(parent_mode, 0o700)
                return json.dumps(ACCOUNT).encode()
            self.assertEqual(path, m.KNOWN_HOSTS)
            return known_hosts()
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(m.os, "geteuid", return_value=0))
            stack.enter_context(mock.patch.object(m, "private_file", side_effect=private_file))
            stack.enter_context(mock.patch.object(m, "private_directory", side_effect=fixture_directory))
            stack.enter_context(mock.patch.object(m.paramiko, "SSHClient", return_value=client))
            stack.enter_context(mock.patch.object(m.time, "monotonic", side_effect=self.clock.monotonic))
            stack.enter_context(mock.patch.object(m.time, "sleep", side_effect=self.clock.sleep))
            yield client, channel

    def read(self, channel, **kwargs):
        with mock.patch.object(m.time, "monotonic", side_effect=self.clock.monotonic), mock.patch.object(m.time, "sleep", side_effect=self.clock.sleep):
            return m.read_prompt(channel, deadline=180, **kwargs)

    def test_complete_paginated_replay_and_private_artifact_protocol(self):
        transcript = responses()
        first, rest = CONFIG.split("vlan", 1)
        transcript["show running-config"] = [
            [("show running-config\r\nRunning configuration:\r\n" + first).encode(),
             b"\x1b[7m-- MO", b"RE --\x1b[0m"],
            [("\b" * 10 + "vlan" + rest + PROMPT).encode()],
        ]
        with self.replay(Channel(transcript, banner=True)) as (client, channel):
            result = m.collect(str(self.output))
        self.assertEqual(result["method"], "aruba-read-only")
        self.assertTrue(result["read_only"] and result["prompt_complete"] and result["startup_slots_complete"])
        self.assertFalse(result["credentials_complete"])
        self.assertRegex(result["native_created_utc"], r"^\d{4}-.*Z$")
        self.assertEqual((self.output / "running-config.txt").read_text(), CONFIG)
        self.assertEqual((self.output / "startup-config.txt").read_text(), CONFIG)
        self.assertEqual(set(path.name for path in self.output.iterdir()), set(m.FILE_NAMES))
        for path in result["artifacts"].values():
            self.assertTrue(Path(path).is_absolute())
            self.assertEqual(Path(path).parent, self.output)
            self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
        metadata = json.loads((self.output / "boot-metadata.json").read_bytes())
        self.assertEqual(metadata["configuration_slots"]["populated_slots"], [{"slot": 1, "name": "fixture-config"}])
        self.assertTrue(metadata["configuration_exports_match"])
        self.assertFalse(metadata["atomic_snapshot"] or metadata["restore_verified"])
        self.assertEqual(metadata["commands"][4]["pager_advances"], 1)
        self.assertEqual(len(metadata["commands"]), 6)
        self.assertEqual(m.COMMANDS, APPROVED_COMMANDS)
        self.assertEqual(channel.sent, ["\r", *(command + "\r" for command in APPROVED_COMMANDS[:5]), " ", "show config\r"])
        self.assertTrue(client.closed and channel.closed)
        self.assertIsInstance(client.policy, paramiko.RejectPolicy)
        self.assertEqual(list(client.host_keys), [m.HOST])
        self.assertEqual(len(client.connections), 1)
        args, flags = client.connections[0]
        self.assertEqual(args, (m.HOST,))
        self.assertFalse(flags["look_for_keys"] or flags["allow_agent"])
        self.assertEqual(flags["username"], ACCOUNT["username"])
        self.assertEqual(flags["password"], ACCOUNT["password"])
        self.assertTrue(all(0 < flags[name] <= 8 for name in ("timeout", "banner_timeout", "auth_timeout")))
        serialized = json.dumps(result) + (self.output / "boot-metadata.json").read_text()
        self.assertNotIn(ACCOUNT["password"], serialized)

    def test_new_populated_slot_fails_before_config_reads_and_removes_own_files(self):
        changed = SLOTS.replace(" 2  |             |", " 2  |             | additional-fixture")
        with self.replay(Channel(responses(slots=changed))) as (client, channel):
            with self.assertRaisesRegex(m.Refused, "unqualified_slot_layout"):
                m.collect(str(self.output))
        self.assertEqual(channel.sent, [command + "\r" for command in APPROVED_COMMANDS[:3]])
        self.assertEqual(list(self.output.iterdir()), [])
        self.assertTrue(client.closed and channel.closed)

    def test_slot_table_requires_all_five_rows_and_three_matching_image_flags(self):
        invalid = (
            SLOTS.replace(" 5  |             |\n", ""),
            SLOTS.replace("act pri sec", "unknown headings"),
            SLOTS.replace("*   *   *", "*       *"),
            SLOTS + " 6 | | unexpected\n",
            SLOTS.replace(" 3  |", " 2  |"),
        )
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(m.Refused):
                m.configuration_slots(text)

    def test_exact_prompt_and_ansi_fragmentation_do_not_drop_body(self):
        channel = Channel()
        channel.queue = [b"show version\r\nfixture-switch# appears inside a line\r\n",
                         b"KB.16.99.0001\r\n\x1b[", b"0mfixture-switch", b"#"]
        _raw, text, prompt, pages = self.read(channel, expected=PROMPT)
        self.assertIn("appears inside a line", text)
        self.assertEqual(prompt, PROMPT)
        self.assertEqual(pages, 0)
        self.assertGreaterEqual(self.clock.value, m.QUIET_SECONDS)
        self.assertEqual(m.ending_prompt(PROMPT + " \n  \n", PROMPT), PROMPT)
        self.assertEqual(m.command_text("show version\nKB.16.99.0001\n" + PROMPT + "\n", PROMPT),
                         "show version\nKB.16.99.0001")

    def test_repeated_pagers_are_advanced_once_each(self):
        channel = Channel({"show version": [
            [b"show version\r\nfirst\r\n-- MORE --"],
            [b"\b\b\b\b\b\b\b\b\b\bsecond\r\n-- MORE --"],
            [("\b" * 10 + "third\r\n" + PROMPT).encode()],
        ]})
        channel.queue.clear()
        channel.sendall("show version\r")
        _raw, text, _prompt, pages = self.read(channel, expected=PROMPT)
        self.assertEqual(channel.sent, ["show version\r", " ", " "])
        self.assertEqual(pages, 2)
        self.assertIn("first\nsecond\nthird", text)

    def test_timeout_closed_output_size_and_page_limits_fail_closed(self):
        channel = Channel()
        channel.queue = [b"truncated without a prompt"]
        with self.assertRaisesRegex(m.Refused, "prompt_timeout"):
            self.read(channel, expected=PROMPT, timeout=0.2)
        self.clock.value = 0
        channel = Channel()
        channel.closed = True
        channel.queue = []
        with self.assertRaisesRegex(m.Refused, "channel_closed"):
            self.read(channel, expected=PROMPT)
        channel = Channel()
        channel.queue = [b"x" * 40]
        with mock.patch.object(m, "MAX_BYTES", 32), self.assertRaisesRegex(m.Refused, "output_limit"):
            self.read(channel, expected=PROMPT)
        channel = Channel()
        channel.queue = [b"-- MORE --"]
        with mock.patch.object(m, "MAX_PAGES", 0), self.assertRaisesRegex(m.Refused, "pagination_limit"):
            self.read(channel, expected=PROMPT)

    def test_wrong_prompt_config_mode_and_command_error_cannot_qualify(self):
        channel = Channel()
        channel.queue = [b"different-switch#"]
        with self.assertRaisesRegex(m.Refused, "unexpected_prompt"):
            self.read(channel, expected=PROMPT)
        self.clock.value = 0
        channel.queue = [b"fixture-switch(config)#"]
        with self.assertRaisesRegex(m.Refused, "prompt_timeout"):
            self.read(channel, expected=PROMPT, timeout=0.5)
        with self.assertRaisesRegex(m.Refused, "native_read_rejected"):
            m.command_text("show flash\nInvalid input: flash\n" + PROMPT, PROMPT)

    def test_missing_config_header_or_body_and_ambiguous_pager_fail(self):
        for text in ("hostname fixture\n", CONFIG.replace("hostname", "absent-hostname")):
            with self.subTest(text=text), self.assertRaises(m.Refused):
                m.native_configuration(text)
        with self.assertRaisesRegex(m.Refused, "ambiguous_pager_layout"):
            m.command_text("config value -- MORE --\n" + PROMPT, PROMPT)

    def test_include_credentials_never_becomes_full_credential_coverage(self):
        config = CONFIG + "include-credentials\n"
        with self.replay(Channel(responses(running=config, startup=config))):
            result = m.collect(str(self.output))
        metadata = json.loads((self.output / "boot-metadata.json").read_bytes())
        self.assertTrue(all(metadata["include_credentials_line_observed"].values()))
        self.assertFalse(result["credentials_complete"] or metadata["credentials_complete"])

    def test_output_collision_preserves_prior_artifacts_and_never_connects(self):
        previous = self.output / "show-flash.raw"
        previous.write_bytes(b"previous fixture")
        with self.replay() as (client, _channel):
            with self.assertRaises(FileExistsError):
                m.collect(str(self.output))
        self.assertEqual(previous.read_bytes(), b"previous fixture")
        self.assertEqual(list(self.output.iterdir()), [previous])
        self.assertEqual(client.connections, [])

    def test_session_failure_closes_client_and_cleans_partial_outputs(self):
        with self.replay() as (client, channel):
            with mock.patch.object(client, "connect", side_effect=RuntimeError(ACCOUNT["password"])):
                with self.assertRaises(RuntimeError):
                    m.collect(str(self.output))
        self.assertTrue(client.closed)
        self.assertFalse(channel.closed)  # Never acquired a channel.
        self.assertEqual(list(self.output.iterdir()), [])

    def test_total_output_and_global_deadline_cannot_publish_partial_export(self):
        with self.replay() as (client, channel), mock.patch.object(m, "MAX_TOTAL_BYTES", 1):
            with self.assertRaisesRegex(m.Refused, "output_limit"):
                m.collect(str(self.output))
        self.assertTrue(client.closed and channel.closed)
        self.assertEqual(list(self.output.iterdir()), [])
        self.clock.value = 181
        with self.assertRaisesRegex(m.Refused, "deadline_exceeded"):
            self.read(Channel(), expected=PROMPT)

    def test_process_alarm_bounds_library_calls_and_is_cancelled_on_exit(self):
        with mock.patch.object(m.signal, "signal", return_value="previous-handler") as handler, mock.patch.object(m.signal, "setitimer", return_value=(0, 0)) as timer:
            with m.wall_clock_limit():
                installed = handler.call_args_list[0].args[1]
                with self.assertRaisesRegex(m.Refused, "deadline_exceeded"):
                    installed(None, None)
        self.assertEqual(timer.call_args_list[0].args, (m.signal.ITIMER_REAL, m.MAX_SECONDS))
        self.assertEqual(timer.call_args_list[-1].args, (m.signal.ITIMER_REAL, 0))
        self.assertEqual(handler.call_args_list[-1].args, (m.signal.SIGALRM, "previous-handler"))

    def test_pin_rejects_other_hosts_multiple_keys_and_non_rsa_keys(self):
        data = known_hosts()
        for invalid in (data.replace(m.HOST.encode(), b"fixture.invalid"), data + data,
                        b"", b"malformed", data.replace(b"ssh-rsa ", b"ssh-ed25519 ")):
            with self.subTest(data=invalid), self.assertRaisesRegex(m.Refused, "invalid_pinned_host_key"):
                m.pinned_key(invalid)
        self.assertEqual(m.pinned_key(data).asbytes(), synthetic_key().asbytes())

    def test_real_reject_policy_rejects_unknown_key_without_save_or_auth(self):
        client = paramiko.SSHClient()
        client._log = mock.Mock()
        with mock.patch.object(client, "save_host_keys") as save:
            with self.assertRaises(paramiko.SSHException):
                paramiko.RejectPolicy().missing_host_key(client, m.HOST, synthetic_key())
        save.assert_not_called()
        self.assertEqual(list(client.get_host_keys()), [])
        client.close()

    def test_credential_schema_requires_explicit_persistence_and_strings(self):
        for account in ({**ACCOUNT, "persist_for_backups": False},
                        {**ACCOUNT, "persist_for_backups": 1},
                        {**ACCOUNT, "password": ""}, {**ACCOUNT, "username": "line\nbreak"},
                        {**ACCOUNT, "extra": "not accepted"}, [], None):
            with self.subTest(account=account), self.assertRaisesRegex(m.Refused, "invalid_credentials_file"):
                m.credentials(json.dumps(account).encode())
        self.assertEqual(m.credentials(json.dumps(ACCOUNT).encode()), ACCOUNT)

    def test_nonroot_fails_before_any_private_file_or_network_access(self):
        with mock.patch.object(m.os, "geteuid", return_value=1000), mock.patch.object(m, "private_file") as private, mock.patch.object(m, "ssh_client") as client:
            with self.assertRaisesRegex(m.Refused, "root_required"):
                m.collect(str(self.output))
        private.assert_not_called()
        client.assert_not_called()

    def test_unsafe_paths_and_writable_ancestors_rejected(self):
        for value in ("relative", "/has/../traversal", "/has//empty", "/trailing/", "/null\x00"):
            with self.subTest(path=value), self.assertRaisesRegex(m.Refused, "unsafe_private_path"):
                m.absolute_path(value)
        with mock.patch.object(m.os, "fstat", return_value=SimpleNamespace(st_uid=0, st_mode=0o40777)):
            with self.assertRaisesRegex(m.Refused, "unsafe_private_ancestor"):
                with m.private_directory("/etc"):
                    self.fail("unqualified private directory accepted")

    def test_private_file_symlink_and_world_readable_mode_rejected(self):
        target = self.output / "fixture.json"
        target.write_bytes(json.dumps(ACCOUNT).encode())
        target.chmod(0o644)
        link = self.output / "link"
        link.symlink_to(target)
        with mock.patch.object(m, "private_directory", side_effect=fixture_directory):
            with self.assertRaises(OSError):
                m.private_file(link)
            with self.assertRaisesRegex(m.Refused, "unsafe_private_file"):
                m.private_file(target)

    def test_private_file_exact_mode_and_stability_are_independently_checked(self):
        target = self.output / "fixture-private.json"
        target.write_bytes(json.dumps(ACCOUNT).encode())
        observed = target.stat()
        def state(mode=0o100600, inode=observed.st_ino):
            return SimpleNamespace(st_uid=0, st_mode=mode, st_nlink=1, st_size=observed.st_size,
                                   st_ino=inode, st_mtime_ns=observed.st_mtime_ns,
                                   st_ctime_ns=observed.st_ctime_ns)
        with mock.patch.object(m, "private_directory", side_effect=fixture_directory):
            with mock.patch.object(m.os, "fstat", return_value=state()):
                self.assertEqual(m.private_file(target), json.dumps(ACCOUNT).encode())
            with mock.patch.object(m.os, "fstat", return_value=state(mode=0o100640)):
                with self.assertRaisesRegex(m.Refused, "unsafe_private_file"):
                    m.private_file(target)
            with mock.patch.object(m.os, "fstat", side_effect=[state(), state(inode=observed.st_ino + 1)]):
                with self.assertRaisesRegex(m.Refused, "private_file_changed"):
                    m.private_file(target)

    def test_safe_error_codes_never_print_secrets_raw_output_or_tracebacks(self):
        for error in (RuntimeError("RAW-FIXTURE " + ACCOUNT["password"]),
                      m.Refused(ACCOUNT["password"]),
                      paramiko.AuthenticationException(ACCOUNT["password"])):
            stdout, stderr = io.StringIO(), io.StringIO()
            with mock.patch.object(m, "collect", side_effect=error), mock.patch.object(m, "wall_clock_limit", return_value=contextlib.nullcontext()), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                status = m.main(["--output-directory", str(self.output)])
            self.assertEqual(status, 1)
            self.assertEqual(stdout.getvalue(), "")
            self.assertRegex(stderr.getvalue(), r"^aruba_native_export_failed:(operation_failed|authentication_failed)\n$")
            self.assertNotIn(ACCOUNT["password"], stderr.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(m.main(["--password", ACCOUNT["password"]]), 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "aruba_native_export_failed:invalid_arguments\n")

    def test_success_stdout_is_only_one_protocol_json(self):
        with self.replay(), mock.patch.object(m, "wall_clock_limit", return_value=contextlib.nullcontext()):
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(m.main(["--output-directory", str(self.output)]), 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(len(stdout.getvalue().splitlines()), 1)
        self.assertEqual(json.loads(stdout.getvalue())["method"], "aruba-read-only")
        self.assertNotIn(ACCOUNT["password"], stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
