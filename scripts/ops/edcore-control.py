#!/usr/bin/env python3
"""Small, auditable 9950x control client for the EdCore workhorse."""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shlex
import subprocess
import sys
import time
from typing import Sequence


DEFAULT_HOST = os.environ.get("EDCORE_SSH_HOST", "edcore-admin")
KALI_LAB_ADDRESS = os.environ.get("EDCORE_KALI_LAB_ADDRESS", "192.168.77.10")
KALI_LAB_USER = os.environ.get("EDCORE_KALI_LAB_USER", "kali")
KALI_LAB_KEY = pathlib.Path(
    os.environ.get("EDCORE_KALI_LAB_KEY", "~/.ssh/edsys_kali_lab_key")
).expanduser()
TARGET_DOMAIN = "metasploitable2-lab"


def ssh_base(host: str) -> list[str]:
    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "ClearAllForwardings=yes",
        host,
    ]


def remote_command(prefix: Sequence[str], command: Sequence[str]) -> str:
    return shlex.join([*prefix, *command])


def run_remote(
    host: str,
    prefix: Sequence[str],
    command: Sequence[str],
    *,
    capture: bool = False,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    argv = [*ssh_base(host), remote_command(prefix, command)]
    return subprocess.run(
        argv,
        check=True,
        input=input_bytes,
        stdout=subprocess.PIPE if capture else None,
    )


def session(host: str, command: Sequence[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
    return run_remote(
        host,
        ["sudo", "-n", "/usr/local/bin/edcore-session"],
        command,
        **kwargs,
    )


def pretty_json(raw: bytes, fields: Sequence[str] | None = None) -> None:
    value = json.loads(raw)
    if fields and isinstance(value, list):
        value = [{field: item.get(field) for field in fields} for item in value]
    print(json.dumps(value, indent=2, sort_keys=True))


def command_status(args: argparse.Namespace) -> None:
    script = r"""
set -e
printf '{'
printf '"hostname":'; hostname -s | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'
printf ',"kernel":'; uname -r | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read().strip()))'
printf ',"uptime_seconds":'; cut -d. -f1 /proc/uptime
printf ',"failed_system_units":'; systemctl --failed --no-legend | wc -l
printf ',"tailscale_state":'; sudo -n tailscale status --json | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin).get("BackendState", "Unknown")))'
printf ',"graphical_session":'; sudo -n /usr/local/bin/edcore-session hyprctl -j activeworkspace >/dev/null 2>&1 && printf true || printf false
printf '}\n'
"""
    result = run_remote(args.host, [], ["bash", "-lc", script], capture=True)
    pretty_json(result.stdout)


def command_shell(args: argparse.Namespace) -> None:
    os.execvp("ssh", ["ssh", args.host])


def command_cockpit(args: argparse.Namespace) -> None:
    print(f"Cockpit tunnel is available at https://127.0.0.1:{args.port}", file=sys.stderr)
    os.execvp(
        "ssh",
        [
            "ssh",
            "-N",
            "-o",
            "BatchMode=yes",
            "-o",
            "ExitOnForwardFailure=yes",
            "-L",
            f"127.0.0.1:{args.port}:127.0.0.1:9090",
            args.host,
        ],
    )


def command_lab_status(args: argparse.Namespace) -> None:
    script = r"""
set -e
state=$(sudo -n virsh domstate kali-lab 2>/dev/null || printf absent)
network=$(sudo -n virsh net-info security-lab 2>/dev/null | awk -F: '/^Active/ {gsub(/^[[:space:]]+/, "", $2); print $2}' || true)
autostart=$(sudo -n virsh dominfo kali-lab 2>/dev/null | awk -F: '/^Autostart:/ {gsub(/^[[:space:]]+/, "", $2); print $2}' || true)
snapshots=$(sudo -n virsh snapshot-list kali-lab --name 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l)
python3 - "$state" "$network" "$autostart" "$snapshots" <<'PY'
import json
import sys
print(json.dumps({
    "domain": "kali-lab",
    "state": sys.argv[1],
    "isolated_network_active": sys.argv[2] == "yes",
    "autostart": sys.argv[3],
    "snapshot_count": int(sys.argv[4]),
}, sort_keys=True))
PY
"""
    result = run_remote(args.host, [], ["bash", "-lc", script], capture=True)
    pretty_json(result.stdout)


def command_lab_power(args: argparse.Namespace) -> None:
    verb = {"start": "start", "shutdown": "shutdown", "reboot": "reboot"}[args.lab_command]
    run_remote(args.host, ["sudo", "-n"], ["virsh", verb, "kali-lab"])


def kali_ssh_argv(host: str) -> list[str]:
    if not KALI_LAB_KEY.is_file():
        raise SystemExit(f"Missing dedicated Kali lab key: {KALI_LAB_KEY}")
    return [
        "ssh",
        "-J",
        host,
        "-i",
        str(KALI_LAB_KEY),
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "KbdInteractiveAuthentication=no",
        f"{KALI_LAB_USER}@{KALI_LAB_ADDRESS}",
    ]


def command_lab_shell(args: argparse.Namespace) -> None:
    os.execvp("ssh", kali_ssh_argv(args.host))


def command_lab_run(args: argparse.Namespace) -> None:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("A Kali command is required after --")
    subprocess.run([*kali_ssh_argv(args.host), shlex.join(command)], check=True)


def command_lab_console(args: argparse.Namespace) -> None:
    os.execvp(
        "ssh",
        ["ssh", "-t", args.host, "sudo", "-n", "virsh", "console", "kali-lab"],
    )


def command_lab_snapshots(args: argparse.Namespace) -> None:
    run_remote(
        args.host,
        ["sudo", "-n"],
        ["virsh", "snapshot-list", "kali-lab", "--tree"],
    )


def command_lab_wait(args: argparse.Namespace) -> None:
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        result = subprocess.run(
            [*kali_ssh_argv(args.host), "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            print("kali_ready=yes")
            return
        time.sleep(2)
    raise SystemExit(f"Kali did not become ready within {args.timeout} seconds")


def command_target_status(args: argparse.Namespace) -> None:
    script = rf"""
set -e
state=$(sudo -n virsh domstate {TARGET_DOMAIN} 2>/dev/null || printf absent)
autostart=$(sudo -n virsh dominfo {TARGET_DOMAIN} 2>/dev/null | awk -F: '/^Autostart:/ {{gsub(/^[[:space:]]+/, "", $2); print $2}}' || true)
snapshots=$(sudo -n virsh snapshot-list {TARGET_DOMAIN} --name 2>/dev/null | sed '/^[[:space:]]*$/d' | wc -l)
lease=$(sudo -n virsh net-dhcp-leases security-lab 2>/dev/null | awk '$3 == "52:54:00:ed:77:20" {{print $5; exit}}' | cut -d/ -f1)
python3 - "$state" "$autostart" "$snapshots" "$lease" <<'PY'
import json
import sys
print(json.dumps({{
    "domain": "{TARGET_DOMAIN}",
    "state": sys.argv[1],
    "autostart": sys.argv[2],
    "snapshot_count": int(sys.argv[3]),
    "lease": sys.argv[4] or None,
}}, sort_keys=True))
PY
"""
    result = run_remote(args.host, [], ["bash", "-lc", script], capture=True)
    pretty_json(result.stdout)


def command_target_power(args: argparse.Namespace) -> None:
    verb = {
        "start": "start",
        "shutdown": "shutdown",
        "stop": "destroy",
        "reboot": "reboot",
    }[args.target_command]
    run_remote(args.host, ["sudo", "-n"], ["virsh", verb, TARGET_DOMAIN])


def command_target_console(args: argparse.Namespace) -> None:
    os.execvp(
        "ssh",
        ["ssh", "-t", args.host, "sudo", "-n", "virsh", "console", TARGET_DOMAIN],
    )


def command_target_snapshots(args: argparse.Namespace) -> None:
    run_remote(
        args.host,
        ["sudo", "-n"],
        ["virsh", "snapshot-list", TARGET_DOMAIN, "--tree"],
    )


def command_target_restore(args: argparse.Namespace) -> None:
    script = rf"""
set -euo pipefail
state=$(sudo -n virsh domstate {TARGET_DOMAIN} | xargs)
[[ "$state" == "shut off" ]] || {{ echo "Stop {TARGET_DOMAIN} before restoring its baseline." >&2; exit 1; }}
snapshot=$(sudo -n virsh snapshot-list {TARGET_DOMAIN} --name | grep '^clean-vulnerable-baseline-' | sort | tail -1)
[[ -n "$snapshot" ]] || {{ echo "No clean vulnerable baseline snapshot exists." >&2; exit 1; }}
sudo -n virsh snapshot-revert {TARGET_DOMAIN} "$snapshot"
printf 'restored_snapshot=%s\n' "$snapshot"
"""
    run_remote(args.host, [], ["bash", "-lc", script])


def command_target_wait(args: argparse.Namespace) -> None:
    script = rf"""
set -euo pipefail
for ((attempt=1; attempt<={args.timeout}; attempt++)); do
  if timeout 1 bash -c '</dev/tcp/192.168.77.20/80' 2>/dev/null; then
    echo target_ready=yes
    exit 0
  fi
  sleep 1
done
echo "Metasploitable target did not become ready within {args.timeout} seconds." >&2
exit 1
"""
    run_remote(args.host, [], ["bash", "-lc", script])


def command_passthrough(args: argparse.Namespace, prefix: Sequence[str]) -> None:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("A command is required after --")
    run_remote(args.host, prefix, command)


def command_gui_json(args: argparse.Namespace) -> None:
    mapping = {
        "windows": ("clients", ("address", "class", "title", "workspace", "mapped")),
        "active": ("activewindow", None),
        "monitors": ("monitors", None),
        "workspaces": ("workspaces", None),
    }
    hypr_command, fields = mapping[args.gui_command]
    result = session(args.host, ["hyprctl", "-j", hypr_command], capture=True)
    pretty_json(result.stdout, fields)


def command_gui_dispatch(args: argparse.Namespace) -> None:
    if args.gui_command == "close":
        dispatcher = ["hyprctl", "dispatch", "killactive"]
    elif args.gui_command == "workspace":
        dispatcher = ["hyprctl", "dispatch", "workspace", args.workspace]
    elif args.gui_command == "exec":
        if not args.command:
            raise SystemExit("A desktop command is required")
        dispatcher = ["hyprctl", "dispatch", "exec", shlex.join(args.command)]
    else:
        raise AssertionError(args.gui_command)
    session(args.host, dispatcher)


def command_gui_type(args: argparse.Namespace) -> None:
    session(args.host, ["wtype", "--", args.text])


def command_gui_key(args: argparse.Namespace) -> None:
    session(args.host, ["wtype", "-k", args.key])


def command_gui_pointer(args: argparse.Namespace) -> None:
    if args.gui_command == "move":
        command = ["ydotool", "mousemove", "--absolute", "--", str(args.x), str(args.y)]
    else:
        button = {"left": "0xC0", "right": "0xC1", "middle": "0xC2"}[args.button]
        command = ["ydotool", "click", button]
    session(args.host, command)


def command_gui_screenshot(args: argparse.Namespace) -> None:
    destination = pathlib.Path(args.output).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = session(args.host, ["grim", "-"], capture=True)
    destination.write_bytes(result.stdout)
    print(destination)


def command_gui_clipboard(args: argparse.Namespace) -> None:
    if args.gui_command == "clipboard-get":
        result = session(args.host, ["wl-paste", "--no-newline"], capture=True)
        sys.stdout.buffer.write(result.stdout)
        if sys.stdout.isatty():
            print()
    else:
        session(args.host, ["wl-copy"], input_bytes=args.text.encode())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH alias (default: %(default)s)")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status", help="Show a compact health summary").set_defaults(func=command_status)
    subparsers.add_parser("shell", help="Open an interactive admin shell").set_defaults(func=command_shell)

    cockpit = subparsers.add_parser("cockpit", help="Open a loopback Cockpit SSH tunnel")
    cockpit.add_argument("--port", type=int, default=9090)
    cockpit.set_defaults(func=command_cockpit)

    lab = subparsers.add_parser("lab", help="Control the isolated Kali VM")
    lab_subparsers = lab.add_subparsers(dest="lab_command", required=True)
    lab_subparsers.add_parser("status", help="Show Kali lab state").set_defaults(func=command_lab_status)
    lab_subparsers.add_parser("start", help="Start Kali").set_defaults(func=command_lab_power)
    lab_subparsers.add_parser("shutdown", help="Request a clean shutdown").set_defaults(func=command_lab_power)
    lab_subparsers.add_parser("reboot", help="Request a clean reboot").set_defaults(func=command_lab_power)
    lab_subparsers.add_parser("shell", help="Open key-only SSH through EdCore").set_defaults(func=command_lab_shell)
    lab_run = lab_subparsers.add_parser("run", help="Run a key-only command inside Kali")
    lab_run.add_argument("command", nargs=argparse.REMAINDER)
    lab_run.set_defaults(func=command_lab_run)
    lab_subparsers.add_parser("console", help="Open the libvirt serial console").set_defaults(func=command_lab_console)
    lab_subparsers.add_parser("snapshots", help="List Kali snapshots").set_defaults(func=command_lab_snapshots)
    lab_wait = lab_subparsers.add_parser("wait", help="Wait for key-only Kali SSH readiness")
    lab_wait.add_argument("--timeout", type=int, default=180)
    lab_wait.set_defaults(func=command_lab_wait)

    target = subparsers.add_parser("target", help="Control the isolated Metasploitable 2 target")
    target_subparsers = target.add_subparsers(dest="target_command", required=True)
    target_subparsers.add_parser("status", help="Show target state").set_defaults(func=command_target_status)
    target_subparsers.add_parser("start", help="Start the target").set_defaults(func=command_target_power)
    target_subparsers.add_parser("shutdown", help="Request a clean shutdown").set_defaults(func=command_target_power)
    target_subparsers.add_parser("stop", help="Force off the disposable legacy target").set_defaults(func=command_target_power)
    target_subparsers.add_parser("reboot", help="Request a clean reboot").set_defaults(func=command_target_power)
    target_subparsers.add_parser("console", help="Open the target console").set_defaults(func=command_target_console)
    target_subparsers.add_parser("snapshots", help="List target snapshots").set_defaults(func=command_target_snapshots)
    target_subparsers.add_parser("restore-clean", help="Restore the newest clean vulnerable baseline").set_defaults(func=command_target_restore)
    target_wait = target_subparsers.add_parser("wait", help="Wait for the target web service readiness marker")
    target_wait.add_argument("--timeout", type=int, default=180)
    target_wait.set_defaults(func=command_target_wait)

    for name, prefix, help_text in (
        ("user", [], "Run a command as edsys-admin"),
        ("root", ["sudo", "-n"], "Run a command as root"),
        ("session", ["sudo", "-n", "/usr/local/bin/edcore-session"], "Run in Jeremy's live desktop session"),
    ):
        command_parser = subparsers.add_parser(name, help=help_text)
        command_parser.add_argument("command", nargs=argparse.REMAINDER)
        command_parser.set_defaults(func=lambda args, p=prefix: command_passthrough(args, p))

    gui = subparsers.add_parser("gui", help="Inspect or control the live Omarchy desktop")
    gui_subparsers = gui.add_subparsers(dest="gui_command", required=True)
    for name in ("windows", "active", "monitors", "workspaces"):
        gui_subparsers.add_parser(name).set_defaults(func=command_gui_json)
    gui_subparsers.add_parser("close", help="Close the active window").set_defaults(func=command_gui_dispatch)

    workspace = gui_subparsers.add_parser("workspace", help="Switch workspace")
    workspace.add_argument("workspace")
    workspace.set_defaults(func=command_gui_dispatch)

    gui_exec = gui_subparsers.add_parser("exec", help="Launch a desktop program")
    gui_exec.add_argument("command", nargs=argparse.REMAINDER)
    gui_exec.set_defaults(func=command_gui_dispatch)

    gui_type = gui_subparsers.add_parser("type", help="Type literal text")
    gui_type.add_argument("text")
    gui_type.set_defaults(func=command_gui_type)

    gui_key = gui_subparsers.add_parser("key", help="Send one named key through wtype")
    gui_key.add_argument("key")
    gui_key.set_defaults(func=command_gui_key)

    gui_move = gui_subparsers.add_parser("move", help="Move the pointer to absolute coordinates")
    gui_move.add_argument("x", type=int)
    gui_move.add_argument("y", type=int)
    gui_move.set_defaults(func=command_gui_pointer)

    gui_click = gui_subparsers.add_parser("click", help="Click a pointer button")
    gui_click.add_argument("button", choices=("left", "right", "middle"), default="left", nargs="?")
    gui_click.set_defaults(func=command_gui_pointer)

    screenshot = gui_subparsers.add_parser("screenshot", help="Save a remote desktop screenshot locally")
    screenshot.add_argument("output")
    screenshot.set_defaults(func=command_gui_screenshot)

    gui_subparsers.add_parser("clipboard-get", help="Read the desktop clipboard").set_defaults(func=command_gui_clipboard)
    clipboard_set = gui_subparsers.add_parser("clipboard-set", help="Set the desktop clipboard")
    clipboard_set.add_argument("text")
    clipboard_set.set_defaults(func=command_gui_clipboard)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
