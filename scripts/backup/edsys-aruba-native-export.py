#!/usr/bin/env python3
"""Root-only, ordinary pinned-SSH read export for the qualified AOS-S switch.

Only --output-directory EXISTING_PRIVATE_DIRECTORY is accepted. No credential,
host, command, trust-policy or switch-setting overrides are accepted. The one
password connection uses the existing root-private credential and an independently
pinned RSA host key. Six native show commands and ordinary SPACE pagination are
the entire CLI protocol; closing the SSH channel ends the read-only session.

These are sequential native reads, not an atomic switch snapshot or a restore
test. Only the qualified five-row, single-populated-slot configuration layout is
accepted. Authentication/key recovery is explicitly incomplete even when the
configuration text contains include-credentials. Raw output stays in private
artifacts. Standard output contains only the successful native-export JSON.
"""

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time

import paramiko


HOST = "192.168.50.3"
CREDENTIALS = Path("/etc/edsys-backup/network-credentials/aruba.json")
KNOWN_HOSTS = Path("/etc/edsys-backup/network-known-hosts")
COMMANDS = (
    "show version", "show flash", "show config files", "show config status",
    "show running-config", "show config",
)
MAX_SECONDS = 180
COMMAND_SECONDS = 40
MAX_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_PAGES = 1024
MAX_PRIVATE_FILE = 65536
QUIET_SECONDS = 0.35
CSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
OSC = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
CHARSET = re.compile(r"\x1b[()][AB0]")
PAGER = re.compile(r"--\s*MORE\s*--", re.IGNORECASE)
VERSION = re.compile(r"\bKB\.\d+\.\d+\.\d+\b")
SAFE_REASONS = frozenset({
    "root_required", "invalid_arguments", "unsafe_private_path",
    "unsafe_private_ancestor", "unsafe_output_directory", "unsafe_private_file",
    "private_file_changed", "invalid_credentials_file", "invalid_pinned_host_key",
    "deadline_exceeded", "prompt_timeout", "output_limit", "pagination_limit",
    "unexpected_prompt", "channel_closed", "native_read_rejected",
    "invalid_terminal_output", "ambiguous_pager_layout", "incomplete_slot_table",
    "unqualified_slot_layout", "configuration_header_missing",
    "configuration_body_missing", "version_metadata_missing",
})
FILE_NAMES = (
    "initial-banner.raw", *(command.replace(" ", "-") + ".raw" for command in COMMANDS),
    "running-config.txt", "startup-config.txt", "boot-metadata.json",
)


class Refused(Exception):
    """Fixed safe code only. Never attach native output or a credential."""


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def remaining(deadline, maximum=8):
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise Refused("deadline_exceeded")
    return min(seconds, maximum)


@contextlib.contextmanager
def wall_clock_limit():
    """Bound even a library call waiting for a peer's SSH channel response."""
    def expired(_number, _frame):
        raise Refused("deadline_exceeded")

    previous = signal.signal(signal.SIGALRM, expired)
    timer = signal.setitimer(signal.ITIMER_REAL, MAX_SECONDS)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
        if timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, *timer)


def absolute_path(value):
    text = os.fspath(value)
    if (not text.startswith("/") or "\x00" in text
            or any(part in {"", ".", ".."} for part in text.split("/")[1:])):
        raise Refused("unsafe_private_path")
    return Path(text)


@contextlib.contextmanager
def private_directory(value, exact_mode=None):
    """FD-anchored traversal: no symlinks or writable/non-root ancestors."""
    path = absolute_path(value)
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for component in [None, *path.parts[1:]]:
            if component is not None:
                child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            observed = os.fstat(descriptor)
            if observed.st_uid != 0 or stat.S_IMODE(observed.st_mode) & 0o022:
                raise Refused("unsafe_private_ancestor")
        if exact_mode is not None and stat.S_IMODE(observed.st_mode) != exact_mode:
            raise Refused("unsafe_output_directory")
        yield descriptor
    finally:
        os.close(descriptor)


def private_file(value, parent_mode=None):
    path = absolute_path(value)
    with private_directory(path.parent, exact_mode=parent_mode) as parent:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(descriptor, "rb") as handle:
            before = os.fstat(handle.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                    or not 0 < before.st_size <= MAX_PRIVATE_FILE):
                raise Refused("unsafe_private_file")
            data = handle.read(MAX_PRIVATE_FILE + 1)
            after = os.fstat(handle.fileno())
            if (len(data) != before.st_size or
                    (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) !=
                    (after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
                raise Refused("private_file_changed")
            return data


def credentials(data):
    try:
        account = json.loads(data)
    except (ValueError, UnicodeError):
        raise Refused("invalid_credentials_file") from None
    if (not isinstance(account, dict)
            or set(account) != {"username", "password", "persist_for_backups"}
            or account["persist_for_backups"] is not True
            or any(not isinstance(account[key], str) or not account[key]
                   or len(account[key]) > 4096 or any(ord(ch) < 32 for ch in account[key])
                   for key in ("username", "password"))):
        raise Refused("invalid_credentials_file")
    return account


def pinned_key(data):
    """Read one literal host/RSA entry; never consult ambient/system host keys."""
    try:
        lines = [line.strip() for line in data.decode("ascii").splitlines()
                 if line.strip() and not line.lstrip().startswith("#")]
        if len(lines) != 1 or len(lines[0]) > MAX_PRIVATE_FILE:
            raise ValueError
        entry = paramiko.hostkeys.HostKeyEntry.from_line(lines[0])
        if entry is None or entry.hostnames != [HOST] or entry.key.get_name() != "ssh-rsa":
            raise ValueError
        return entry.key
    except Exception:
        raise Refused("invalid_pinned_host_key") from None


def ssh_client(key):
    # Paramiko failures can otherwise log server-supplied text before main catches
    # them. This standalone exporter never emits transport logs or tracebacks.
    logger = logging.getLogger("paramiko")
    logger.handlers = [logging.NullHandler()]
    logger.propagate = False
    client = paramiko.SSHClient()
    client.get_host_keys().add(HOST, "ssh-rsa", key)
    client.set_missing_host_key_policy(paramiko.RejectPolicy())
    return client


def without_escapes(raw):
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise Refused("invalid_terminal_output") from None
    return CHARSET.sub("", CSI.sub("", OSC.sub("", text)))


def plain(raw):
    text = without_escapes(raw).replace("\r\n", "\n").replace("\r", "\n")
    output = []
    for character in text:
        if character == "\b":
            if output and output[-1] != "\n":
                output.pop()
        elif character not in "\x00\x07":
            output.append(character)
    return "".join(output)


def ending_prompt(text, expected=None):
    match = re.search(r"(?m)^([A-Za-z0-9][A-Za-z0-9_.:-]{0,79}#)[ \t]*(?:\n[ \t]*)*\Z", text)
    if match is None:
        return None
    prompt = match.group(1)
    if expected is not None and prompt != expected:
        raise Refused("unexpected_prompt")
    return prompt


def read_prompt(channel, deadline, expected=None, timeout=COMMAND_SECONDS):
    """Observe an exact manager prompt after ordinary paginated native output."""
    end = min(deadline, time.monotonic() + timeout)
    last_read = time.monotonic()
    raw = bytearray()
    pages = 0
    answered_banner = False
    while True:
        remaining(deadline)
        if time.monotonic() >= end:
            raise Refused("prompt_timeout")
        if channel.recv_ready():
            block = channel.recv(65536)
            if not block:
                raise Refused("channel_closed")
            raw.extend(block)
            last_read = time.monotonic()
            if len(raw) > MAX_BYTES:
                raise Refused("output_limit")
            # Match on the received transcript before applying backspaces, so an
            # erased pager cannot make its next occurrence look like the first.
            # Ignore only a possibly split UTF-8 tail while receiving; final
            # output is decoded strictly before it can qualify as complete.
            visible = CHARSET.sub("", CSI.sub("", OSC.sub("", raw.decode("utf-8", errors="replace"))))
            if expected is None and not answered_banner and re.search(r"press any key to continue", visible, re.I):
                channel.sendall("\r")
                answered_banner = True
            count = len(PAGER.findall(visible))
            while pages < count:
                if pages >= MAX_PAGES:
                    raise Refused("pagination_limit")
                channel.sendall(" ")
                pages += 1
        else:
            if channel.closed:
                raise Refused("channel_closed")
            if time.monotonic() - last_read >= QUIET_SECONDS:
                text = plain(bytes(raw))
                prompt = ending_prompt(text, expected)
                if prompt is not None:
                    return bytes(raw), text, prompt, pages
            time.sleep(min(0.05, remaining(end, 0.05)))


def command_text(text, prompt):
    if ending_prompt(text, prompt) != prompt:
        raise Refused("unexpected_prompt")
    lines = text.rstrip(" \t\n").splitlines()
    if not lines or lines[-1].strip() != prompt:
        raise Refused("unexpected_prompt")
    lines.pop()
    if re.search(r"(?im)^\s*(?:invalid input|incomplete input|ambiguous input|permission denied|not authorized|access denied|error:)", "\n".join(lines)):
        raise Refused("native_read_rejected")
    cleaned = []
    for line in lines:
        if PAGER.search(line):
            if not re.fullmatch(r"\s*--\s*MORE\s*--(?:,.*)?\s*", line, re.I):
                raise Refused("ambiguous_pager_layout")
            continue
        if any(ord(character) < 32 and character != "\t" for character in line):
            raise Refused("invalid_terminal_output")
        cleaned.append(line)
    return "\n".join(cleaned)


def configuration_slots(text):
    if not re.search(r"(?im)^\s*id\s*\|\s*act\s+pri\s+sec\s*\|\s*name\s*$", text):
        raise Refused("incomplete_slot_table")
    rows = []
    for line in text.splitlines():
        match = re.fullmatch(r"\s*(\d+)\s*\|([^|]*)\|([^|]*)", line)
        if match:
            rows.append((int(match.group(1)), match.group(2).split(), match.group(3).strip()))
    if len(rows) != 5 or sorted(row[0] for row in rows) != [1, 2, 3, 4, 5]:
        raise Refused("incomplete_slot_table")
    populated = [row for row in rows if row[2]]
    if (len(populated) != 1 or populated[0][1] != ["*", "*", "*"]
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", populated[0][2])
            or any(flags for _slot, flags, name in rows if not name)):
        raise Refused("unqualified_slot_layout")
    slot, _flags, name = populated[0]
    return {"slot_count": 5, "populated_slots": [{"slot": slot, "name": name}],
            "active_slot": slot, "primary_image_slot": slot, "secondary_image_slot": slot}


def native_configuration(text):
    lines = text.splitlines()
    headers = [index for index, line in enumerate(lines)
               if re.fullmatch(r"\s*;.*Configuration Editor;.*Created on release #KB\.\d+\.\d+\.\d+\s*", line)]
    if len(headers) != 1:
        raise Refused("configuration_header_missing")
    lines = lines[headers[0]:]
    while lines and not lines[-1].strip():
        lines.pop()
    data = "\n".join(lines) + "\n"
    if not re.search(r"(?m)^hostname\s+\S", data):
        raise Refused("configuration_body_missing")
    return data.encode("utf-8")


def collect(output_directory):
    if os.geteuid() != 0:
        raise Refused("root_required")
    output_directory = absolute_path(output_directory)
    deadline = time.monotonic() + MAX_SECONDS
    key = pinned_key(private_file(KNOWN_HOSTS))
    account = {}
    client, channel = None, None
    try:
        account = credentials(private_file(CREDENTIALS, parent_mode=0o700))
        with private_directory(output_directory, exact_mode=0o700) as output:
            owned, completed = [], False
            try:
                with contextlib.ExitStack() as stack:
                    files = {}
                    # Refuse all collisions before opening a network connection.
                    for name in FILE_NAMES:
                        descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                             0o600, dir_fd=output)
                        owned.append(name)
                        os.fchmod(descriptor, 0o600)
                        files[name] = stack.enter_context(os.fdopen(descriptor, "wb"))
                    client = ssh_client(key)
                    client.connect(HOST, username=account["username"], password=account["password"],
                                   allow_agent=False, look_for_keys=False, timeout=remaining(deadline),
                                   banner_timeout=remaining(deadline), auth_timeout=remaining(deadline))
                    account.clear()
                    channel = client.invoke_shell(width=180, height=24)
                    channel.settimeout(remaining(deadline))
                    raw, _text, prompt, pages = read_prompt(channel, deadline, timeout=15)
                    files["initial-banner.raw"].write(raw)
                    total = len(raw)
                    receipts, bodies, slots = [], {}, None
                    for command in COMMANDS:
                        remaining(deadline)
                        channel.settimeout(remaining(deadline))
                        started = utc()
                        channel.sendall(command + "\r")
                        raw, text, _prompt, pages = read_prompt(channel, deadline, expected=prompt)
                        total += len(raw)
                        if total > MAX_TOTAL_BYTES:
                            raise Refused("output_limit")
                        body = command_text(text, prompt)
                        # Native show config is complete only for this qualified
                        # single saved slot. New slots need a separately reviewed
                        # read method, not an optimistic completeness flag.
                        if command == "show config files":
                            slots = configuration_slots(body)
                        bodies[command] = body
                        name = command.replace(" ", "-") + ".raw"
                        files[name].write(raw)
                        receipts.append({"command": command, "started_utc": started,
                                         "completed_utc": utc(), "prompt_complete": True,
                                         "pager_advances": pages, "bytes": len(raw),
                                         "sha256": hashlib.sha256(raw).hexdigest(), "artifact": name})
                    versions = {command: sorted(set(VERSION.findall(bodies[command])))
                                for command in ("show version", "show flash")}
                    if not all(versions.values()):
                        raise Refused("version_metadata_missing")
                    running = native_configuration(bodies["show running-config"])
                    startup = native_configuration(bodies["show config"])
                    created = utc()
                    metadata = {
                        "format": 1, "method": "aruba-read-only", "native_created_utc": created,
                        "timestamp_basis": "hub UTC after six complete sequential native reads",
                        "read_only": True, "prompt_complete": True, "startup_slots_complete": True,
                        "pinned_host_key_type": "ssh-rsa",
                        "pinned_host_key_sha256": hashlib.sha256(key.asbytes()).hexdigest(),
                        "configuration_slots": slots, "observed_versions": versions,
                        "boot_references": [command.replace(" ", "-") + ".raw" for command in COMMANDS[:4]],
                        "credentials_complete": False,
                        "credential_recovery_limit": "displayed configuration does not prove hidden credentials or generated identity/key recovery",
                        "include_credentials_line_observed": {
                            label: bool(re.search(rb"(?m)^\s*include-credentials(?:\s.*)?$", data))
                            for label, data in (("running", running), ("startup", startup))
                        },
                        "configuration_exports_match": running == startup,
                        "commands": receipts, "atomic_snapshot": False, "restore_verified": False,
                    }
                    files["running-config.txt"].write(running)
                    files["startup-config.txt"].write(startup)
                    files["boot-metadata.json"].write((json.dumps(metadata, sort_keys=True) + "\n").encode())
                    for handle in files.values():
                        handle.flush()
                        os.fsync(handle.fileno())
                os.fsync(output)
                completed = True
                labels = {"running-config": "running-config.txt", "startup-config": "startup-config.txt",
                          "boot-metadata": "boot-metadata.json", "initial-banner": "initial-banner.raw"}
                labels.update({command.replace(" ", "-"): command.replace(" ", "-") + ".raw"
                               for command in COMMANDS})
                return {"method": "aruba-read-only", "native_created_utc": created,
                        "read_only": True, "prompt_complete": True,
                        "startup_slots_complete": True, "credentials_complete": False,
                        "artifacts": {label: str(output_directory / name) for label, name in labels.items()}}
            finally:
                if not completed:
                    for name in owned:
                        os.unlink(name, dir_fd=output)
    finally:
        account.clear()
        if channel is not None:
            with contextlib.suppress(Exception):
                channel.close()
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()


class Parser(argparse.ArgumentParser):
    def error(self, _message):
        raise Refused("invalid_arguments")


def main(argv=None):
    try:
        parser = Parser(add_help=False, allow_abbrev=False)
        parser.add_argument("--output-directory", required=True)
        args = parser.parse_args(argv)
        with wall_clock_limit():
            result = collect(args.output_directory)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refused as error:
        reason = str(error) if str(error) in SAFE_REASONS else "operation_failed"
    except paramiko.BadHostKeyException:
        reason = "host_key_rejected"
    except paramiko.AuthenticationException:
        reason = "authentication_failed"
    except (TimeoutError, paramiko.ssh_exception.NoValidConnectionsError):
        reason = "connection_failed"
    except Exception:
        reason = "operation_failed"
    print("aruba_native_export_failed:" + reason, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
