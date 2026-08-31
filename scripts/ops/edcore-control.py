#!/usr/bin/env python3
"""Auditable 9950x control client for the Proxmox-based EdCore node."""

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


DEFAULT_HOST = os.environ.get("EDCORE_SSH_HOST", "pve-node3")
HA_VMID = 300
KALI_VMID = 330
TARGET_VMID = 331
KALI_LAB_ADDRESS = os.environ.get("EDCORE_KALI_LAB_ADDRESS", "192.168.77.10")
KALI_LAB_USER = os.environ.get("EDCORE_KALI_LAB_USER", "kali")
KALI_LAB_KEY = pathlib.Path(
    os.environ.get("EDCORE_KALI_LAB_KEY", "~/.ssh/edsys_kali_lab_key")
).expanduser()
KALI_BASELINE = "starter-tools-baseline-20260830"
TARGET_BASELINE = "clean-vulnerable-baseline-20260830"


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
) -> subprocess.CompletedProcess[bytes]:
    argv = [*ssh_base(host), remote_command(prefix, command)]
    return subprocess.run(
        argv,
        check=True,
        stdout=subprocess.PIPE if capture else None,
    )


def run_script(
    host: str, script: str, *, capture: bool = False
) -> subprocess.CompletedProcess[bytes]:
    return run_remote(host, [], ["bash", "-lc", script], capture=capture)


def pretty_json(raw: bytes) -> None:
    print(json.dumps(json.loads(raw), indent=2, sort_keys=True))


def command_status(args: argparse.Namespace) -> None:
    script = rf"""
set -eu
pve_version=$(pveversion | head -n1)
quorate=$(pvecm status | awk -F: '/^Quorate:/ {{gsub(/[[:space:]]/, "", $2); print $2}}')
failed_units=$(systemctl --failed --no-legend | wc -l)
if command -v tailscale >/dev/null 2>&1; then
  tailscale_state=$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin).get("BackendState", "Unknown"))' || printf Unknown)
else
  tailscale_state=not-installed
fi
guard=failed
/usr/local/sbin/edsys-security-lab-guard verify >/dev/null 2>&1 && guard=pass
ha_state=$(qm status {HA_VMID} | awk '{{print $2}}')
kali_state=$(qm status {KALI_VMID} | awk '{{print $2}}')
target_state=$(qm status {TARGET_VMID} | awk '{{print $2}}')
python3 - "$pve_version" "$quorate" "$failed_units" "$tailscale_state" "$guard" "$ha_state" "$kali_state" "$target_state" <<'PYJSON'
import json
import sys

print(json.dumps({{
    "hostname": "pve-node3",
    "pve_version": sys.argv[1],
    "cluster_quorate": sys.argv[2] == "Yes",
    "failed_system_units": int(sys.argv[3]),
    "tailscale_state": sys.argv[4],
    "security_lab_guard": sys.argv[5],
    "home_assistant": sys.argv[6],
    "kali_lab": sys.argv[7],
    "metasploitable2_lab": sys.argv[8],
}}, sort_keys=True))
PYJSON
"""
    result = run_script(args.host, script, capture=True)
    pretty_json(result.stdout)


def command_shell(args: argparse.Namespace) -> None:
    os.execvp("ssh", ["ssh", args.host])


def command_web(args: argparse.Namespace) -> None:
    print(
        f"Proxmox is tunneled at https://127.0.0.1:{args.port}",
        file=sys.stderr,
    )
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
            f"127.0.0.1:{args.port}:127.0.0.1:8006",
            args.host,
        ],
    )


def vm_state(host: str, vmid: int) -> str:
    result = run_remote(host, [], ["qm", "status", str(vmid)], capture=True)
    return result.stdout.decode().strip().partition(":")[2].strip()


def command_vm_status(args: argparse.Namespace, vmid: int) -> None:
    script = rf"""
set -eu
state=$(qm status {vmid} | awk '{{print $2}}')
name=$(qm config {vmid} | awk -F': ' '/^name:/ {{print $2}}')
onboot=$(qm config {vmid} | awk -F': ' '/^onboot:/ {{print $2}}')
protection=$(qm config {vmid} | awk -F': ' '/^protection:/ {{print $2}}')
snapshots=$(qm listsnapshot {vmid} | grep -Ec '[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' || true)
python3 - "$state" "$name" "$onboot" "$protection" "$snapshots" <<'PYJSON'
import json
import sys

print(json.dumps({{
    "vmid": {vmid},
    "name": sys.argv[2],
    "state": sys.argv[1],
    "onboot": sys.argv[3] == "1",
    "protection": sys.argv[4] == "1",
    "snapshot_count": int(sys.argv[5]),
}}, sort_keys=True))
PYJSON
"""
    result = run_script(args.host, script, capture=True)
    pretty_json(result.stdout)


def command_vm_power(
    args: argparse.Namespace, vmid: int, command_attribute: str
) -> None:
    action = getattr(args, command_attribute)
    if action == "start":
        command = ["qm", "start", str(vmid)]
    elif action == "shutdown":
        command = ["qm", "shutdown", str(vmid), "--timeout", "180"]
    elif action == "reboot":
        command = ["qm", "reboot", str(vmid), "--timeout", "180"]
    elif action == "stop":
        command = ["qm", "stop", str(vmid)]
    else:
        raise AssertionError(action)
    run_remote(args.host, [], command)


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


def command_kali_shell(args: argparse.Namespace) -> None:
    os.execvp("ssh", kali_ssh_argv(args.host))


def command_kali_run(args: argparse.Namespace) -> None:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("A Kali command is required after --")
    subprocess.run([*kali_ssh_argv(args.host), shlex.join(command)], check=True)


def command_kali_wait(args: argparse.Namespace) -> None:
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


def command_console(args: argparse.Namespace, vmid: int) -> None:
    os.execvp("ssh", ["ssh", "-t", args.host, "qm", "terminal", str(vmid)])


def command_snapshots(args: argparse.Namespace, vmid: int) -> None:
    run_remote(args.host, [], ["qm", "listsnapshot", str(vmid)])


def restore_snapshot(
    args: argparse.Namespace, vmid: int, snapshot: str
) -> None:
    script = rf"""
set -euo pipefail
state=$(qm status {vmid} | awk '{{print $2}}')
[[ "$state" == stopped ]] || {{
  echo "VM {vmid} must be stopped before restoring {snapshot}." >&2
  exit 1
}}
qm listsnapshot {vmid} | grep -Fq -- '{snapshot}'
qm rollback {vmid} {shlex.quote(snapshot)}
qm set {vmid} --protection 1
printf 'restored_snapshot=%s\n' {shlex.quote(snapshot)}
"""
    run_script(args.host, script)


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
    run_script(args.host, script)


def command_isolation(args: argparse.Namespace) -> None:
    script = rf"""
set -euo pipefail
/usr/local/sbin/edsys-security-lab-guard verify
ip -4 address show dev vmbr77 | grep -Fq '192.168.77.1/24'
test "$(cat /sys/class/net/vmbr77/bridge/stp_state)" = 0
test -z "$(bridge link show master vmbr77 | awk '$2 !~ /^tap(330|331)i0:/ {{print}}')"
grep -Fxq 'port=0' /etc/edsys/security-lab-dnsmasq.conf
grep -Fxq 'dhcp-option=3' /etc/edsys/security-lab-dnsmasq.conf
grep -Fxq 'dhcp-option=6' /etc/edsys/security-lab-dnsmasq.conf
for vmid in {KALI_VMID} {TARGET_VMID}; do
  qm config "$vmid" | grep -Eq '^net0: .*,bridge=vmbr77(,|$)'
  qm config "$vmid" | grep -Fxq 'onboot: 0'
  qm config "$vmid" | grep -Fxq 'protection: 1'
done
systemctl is-active --quiet edsys-security-lab-guard.service
systemctl is-active --quiet edsys-security-lab-dhcp.service
nft list table inet edsys_security_lab | grep -Fq 'iifname "vmbr77"'
nft list table inet edsys_security_lab | grep -Fq 'oifname "vmbr77"'
printf '%s\n'   'security_lab_bridge=pass'   'physical_uplink=none'   'dns_service=disabled'   'default_gateway_offer=none'   'forward_guard=pass'   'vm_autostart=disabled'   'vm_deletion_protection=enabled'
"""
    run_script(args.host, script)


def command_passthrough(args: argparse.Namespace) -> None:
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        raise SystemExit("A command is required after --")
    run_remote(args.host, [], command)


def add_common_vm_commands(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    command_attribute: str,
    vmid: int,
) -> None:
    subparsers.add_parser("status").set_defaults(
        func=lambda args: command_vm_status(args, vmid)
    )
    for action in ("start", "shutdown", "reboot", "stop"):
        subparsers.add_parser(action).set_defaults(
            func=lambda args, target=vmid, attr=command_attribute: command_vm_power(
                args, target, attr
            )
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST, help="SSH alias (default: %(default)s)")
    subparsers = parser.add_subparsers(dest="action", required=True)

    subparsers.add_parser("status", help="Show compact pve-node3 health").set_defaults(
        func=command_status
    )
    subparsers.add_parser("shell", help="Open an interactive root shell").set_defaults(
        func=command_shell
    )
    subparsers.add_parser(
        "isolation", help="Verify the security-lab containment boundary"
    ).set_defaults(func=command_isolation)

    web = subparsers.add_parser("web", help="Open a loopback Proxmox web tunnel")
    web.add_argument("--port", type=int, default=8006)
    web.set_defaults(func=command_web)

    root = subparsers.add_parser("root", help="Run a root command on pve-node3")
    root.add_argument("command", nargs=argparse.REMAINDER)
    root.set_defaults(func=command_passthrough)

    ha = subparsers.add_parser("ha", help="Control Home Assistant VM 300")
    ha_subparsers = ha.add_subparsers(dest="ha_command", required=True)
    add_common_vm_commands(ha_subparsers, "ha_command", HA_VMID)
    ha_subparsers.add_parser("snapshots").set_defaults(
        func=lambda args: command_snapshots(args, HA_VMID)
    )

    lab = subparsers.add_parser("lab", help="Control isolated Kali VM 330")
    lab_subparsers = lab.add_subparsers(dest="lab_command", required=True)
    add_common_vm_commands(lab_subparsers, "lab_command", KALI_VMID)
    lab_subparsers.add_parser("shell").set_defaults(func=command_kali_shell)
    lab_run = lab_subparsers.add_parser("run")
    lab_run.add_argument("command", nargs=argparse.REMAINDER)
    lab_run.set_defaults(func=command_kali_run)
    lab_wait = lab_subparsers.add_parser("wait")
    lab_wait.add_argument("--timeout", type=int, default=180)
    lab_wait.set_defaults(func=command_kali_wait)
    lab_subparsers.add_parser("console").set_defaults(
        func=lambda args: command_console(args, KALI_VMID)
    )
    lab_subparsers.add_parser("snapshots").set_defaults(
        func=lambda args: command_snapshots(args, KALI_VMID)
    )
    lab_subparsers.add_parser("restore-starter").set_defaults(
        func=lambda args: restore_snapshot(args, KALI_VMID, KALI_BASELINE)
    )

    target = subparsers.add_parser(
        "target", help="Control isolated Metasploitable VM 331"
    )
    target_subparsers = target.add_subparsers(dest="target_command", required=True)
    add_common_vm_commands(target_subparsers, "target_command", TARGET_VMID)
    target_wait = target_subparsers.add_parser("wait")
    target_wait.add_argument("--timeout", type=int, default=180)
    target_wait.set_defaults(func=command_target_wait)
    target_subparsers.add_parser("console").set_defaults(
        func=lambda args: command_console(args, TARGET_VMID)
    )
    target_subparsers.add_parser("snapshots").set_defaults(
        func=lambda args: command_snapshots(args, TARGET_VMID)
    )
    target_subparsers.add_parser("restore-clean").set_defaults(
        func=lambda args: restore_snapshot(args, TARGET_VMID, TARGET_BASELINE)
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from exc


if __name__ == "__main__":
    main()
