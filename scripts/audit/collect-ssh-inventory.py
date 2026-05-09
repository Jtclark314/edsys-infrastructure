#!/usr/bin/env python3
"""
Credentialed read-only EdSys SSH inventory collector.

This script intentionally does not store credentials. Provide the password at
runtime through EDSYS_AUDIT_PASSWORD or via prompt. Raw command output is
sanitized before being written to disk.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import getpass
import json
import os
import re
import shlex
import socket
from pathlib import Path

import yaml

try:
    import paramiko
except ImportError as exc:  # pragma: no cover - dependency check for operators
    raise SystemExit("paramiko is required for credentialed SSH audit collection") from exc


DEFAULT_AUDIT_ROOT = Path(r"C:\EdSys-Codex\_local-audits")
SENSITIVE_RE = re.compile(
    r"(password|passwd|token|secret|api[_-]?key|authorization|cookie|private key|cloudflared tunnel run --token|"
    r"BEGIN .*KEY|sshkeys?:|cipassword|rootfs:.*secret|/run/secrets/|\.env)",
    re.IGNORECASE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


HOSTS = [
    {"name": "edcore", "host": "192.168.50.1", "user": "jeremy", "kind": "linux"},
    {"name": "unifi-ap", "host": "192.168.50.2", "user": "jeremy", "kind": "network"},
    {"name": "aruba-switch", "host": "192.168.50.3", "user": "jeremy", "kind": "switch"},
    {"name": "edsys-ingress", "host": "192.168.50.4", "user": "jeremy", "kind": "docker"},
    {"name": "pihole-primary", "host": "192.168.50.5", "user": "jeremy", "kind": "pihole"},
    {"name": "pihole-secondary", "host": "192.168.50.6", "user": "jeremy", "kind": "pihole"},
    {"name": "edsys-voice-old-ip", "host": "192.168.50.7", "user": "jeremy", "kind": "linux"},
    {"name": "voice-node1", "host": "192.168.50.12", "user": "jeremy", "kind": "docker"},
    {"name": "9950x", "host": "192.168.50.50", "user": "jeremy", "kind": "docker"},
    {"name": "pve-node0", "host": "192.168.50.51", "user": "root", "kind": "proxmox"},
    {"name": "pve-node1", "host": "192.168.50.52", "user": "root", "kind": "proxmox"},
    {"name": "pve-node2", "host": "192.168.50.53", "user": "root", "kind": "proxmox"},
    {"name": "master-bedroom-htpc", "host": "192.168.50.54", "user": "jeremy", "kind": "linux"},
    {"name": "edcorelan", "host": "192.168.50.55", "user": "jeremy", "kind": "linux"},
    {"name": "family-services", "host": "192.168.50.78", "user": "jeremy", "kind": "docker"},
    {"name": "arr-server", "host": "192.168.50.201", "user": "jeremy", "kind": "docker"},
]


GENERAL_CMD = r"""
echo '### hostname'; hostname
echo '### hostnamectl'; hostnamectl 2>/dev/null || true
echo '### os-release'; cat /etc/os-release 2>/dev/null || true
echo '### uname'; uname -a 2>/dev/null || true
echo '### ip -br addr'; ip -br addr 2>/dev/null || true
echo '### ip -br link'; ip -br link 2>/dev/null || true
echo '### ip route'; ip route 2>/dev/null || true
echo '### ip neigh'; ip neigh 2>/dev/null || true
echo '### interface macs'; for f in /sys/class/net/*/address; do echo "$f $(cat "$f" 2>/dev/null)"; done
echo '### df -h'; df -h 2>/dev/null || true
echo '### lsblk'; lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL 2>/dev/null || true
echo '### free -h'; free -h 2>/dev/null || true
echo '### uptime'; uptime 2>/dev/null || true
echo '### systemctl failed'; systemctl --failed --no-pager 2>/dev/null || true
echo '### ss -tulpn'; ss -tulpn 2>/dev/null || true
echo '### findmnt'; findmnt 2>/dev/null || true
echo '### selected mounts'; mount | grep -E "nfs|cifs|mergerfs|fuse|/mnt|media|ai-store|downloads" 2>/dev/null || true
"""

DOCKER_CMD = GENERAL_CMD + r"""
echo '### docker ps json'; docker ps --format '{{json .}}' 2>/dev/null || true
echo '### docker compose ls'; docker compose ls 2>/dev/null || true
echo '### docker filtered inspect';
for c in $(docker ps --format '{{.Names}}' 2>/dev/null); do
  docker inspect --format '{{json .Name}}|{{json .Config.Image}}|{{json .State.Status}}|{{json .HostConfig.RestartPolicy}}|{{json .NetworkSettings.Ports}}|{{json .Mounts}}|{{json .NetworkSettings.Networks}}' "$c" 2>/dev/null || true
done
"""

PIHOLE_CMD = GENERAL_CMD + r"""
echo '### pihole status'; pihole status 2>/dev/null || true
echo '### pihole version'; pihole -v 2>/dev/null || true
echo '### pihole network addresses';
sqlite3 /etc/pihole/pihole-FTL.db "SELECT name,ip,hwaddr,interface,lastQuery FROM network_addresses LEFT JOIN network USING (id);" 2>/dev/null || true
"""

EDCORE_EXTRA_CMD = GENERAL_CMD + r"""
echo '### dnsmasq leases'; cat /var/lib/misc/dnsmasq.leases 2>/dev/null || true
echo '### dnsmasq edsys config selected'; grep -R --exclude='*.bak' --exclude='*.log' -nE '^[^#].*(dhcp-host|dhcp-range|dhcp-option|server=|address=)' /etc/dnsmasq* /etc/dnsmasq.d 2>/dev/null || true
echo '### iptables filter'; iptables -S 2>/dev/null || true
echo '### iptables nat'; iptables -t nat -S 2>/dev/null || true
echo '### dnsmasq status'; systemctl status dnsmasq --no-pager 2>/dev/null || true
"""

PROXMOX_CMD = r"""
echo '### hostname'; hostname
echo '### hostnamectl'; hostnamectl 2>/dev/null || true
echo '### pveversion'; pveversion -v 2>/dev/null || true
echo '### pvecm status'; pvecm status 2>/dev/null || true
echo '### cluster resources json'; pvesh get /cluster/resources --output-format json 2>/dev/null || true
echo '### qm list'; qm list 2>/dev/null || true
echo '### pct list'; pct list 2>/dev/null || true
echo '### qm configs';
for id in $(qm list 2>/dev/null | awk 'NR>1 {print $1}'); do
  echo "### qm config $id";
  qm config "$id" 2>/dev/null || true;
done
echo '### pct configs';
for id in $(pct list 2>/dev/null | awk 'NR>1 {print $1}'); do
  echo "### pct config $id";
  pct config "$id" 2>/dev/null || true;
done
echo '### ip -br addr'; ip -br addr 2>/dev/null || true
echo '### ip -br link'; ip -br link 2>/dev/null || true
echo '### bridge link'; bridge link 2>/dev/null || true
echo '### bridge fdb'; bridge fdb show 2>/dev/null || true
echo '### ip neigh'; ip neigh 2>/dev/null || true
echo '### df -h'; df -h 2>/dev/null || true
echo '### lsblk'; lsblk -o NAME,SIZE,FSTYPE,MOUNTPOINTS,MODEL 2>/dev/null || true
echo '### ss -tulpn'; ss -tulpn 2>/dev/null || true
"""

SWITCH_CMD = "\n".join(
    [
        "show system",
        "show version",
        "show mac-address",
        "show arp",
        "show lldp info remote-device",
        "show interfaces brief",
        "show interfaces status",
        "show vlan",
    ]
)


def now_iso() -> str:
    return dt.datetime.now().replace(microsecond=0).isoformat()


def sanitize(text: str, password: str) -> str:
    if password:
        text = text.replace(password, "[REDACTED_PASSWORD]")
    text = ANSI_RE.sub("", text)
    out = []
    for line in text.splitlines():
        if SENSITIVE_RE.search(line):
            out.append("[REDACTED_SENSITIVE_LINE]")
        else:
            out.append(line.rstrip())
    return "\n".join(out).rstrip() + "\n"


def ssh_connect(host: dict, password: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host["host"],
        username=host["user"],
        password=password,
        timeout=8,
        banner_timeout=8,
        auth_timeout=8,
        look_for_keys=True,
        allow_agent=True,
    )
    return client


def run_command(client: paramiko.SSHClient, cmd: str, password: str, sudo: bool = False, timeout: int = 90) -> tuple[int, str]:
    if sudo:
        remote = "sudo -S -p '' sh -lc " + shlex.quote(cmd)
    else:
        remote = "sh -lc " + shlex.quote(cmd)
    stdin, stdout, stderr = client.exec_command(remote, get_pty=sudo, timeout=timeout)
    if sudo:
        stdin.write(password + "\n")
        stdin.flush()
    stdout_text = stdout.read().decode("utf-8", errors="replace")
    stderr_text = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    return exit_code, stdout_text + ("\n### stderr\n" + stderr_text if stderr_text.strip() else "")


def run_switch_commands(client: paramiko.SSHClient, password: str) -> tuple[int, str]:
    channel = client.invoke_shell()
    channel.settimeout(4)
    output = []
    for cmd in SWITCH_CMD.splitlines():
        channel.send(cmd + "\n")
        chunks = []
        while True:
            try:
                data = channel.recv(4096)
            except socket.timeout:
                break
            if not data:
                break
            text = data.decode("utf-8", errors="replace")
            chunks.append(text)
            if len("".join(chunks)) > 200000:
                break
        output.append(f"### {cmd}\n{''.join(chunks)}")
    channel.close()
    return 0, "\n".join(output)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def parse_hostname(output: str) -> str | None:
    match = re.search(r"### hostname\n([^\n]+)", output)
    return match.group(1).strip() if match else None


def parse_os(output: str) -> str | None:
    match = re.search(r'PRETTY_NAME="?([^"\n]+)"?', output)
    if match:
        return match.group(1).strip()
    match = re.search(r"Operating System:\s*(.+)", output)
    return match.group(1).strip() if match else None


def parse_primary_mac(output: str) -> str | None:
    for line in output.splitlines():
        if re.search(r"\b(eth|enp|eno|ens|vmbr0)\S*\b", line) and re.search(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", line, re.I):
            match = re.search(r"([0-9a-f]{2}:){5}[0-9a-f]{2}", line, re.I)
            if match:
                return match.group(0).lower()
    return None


def parse_ip_br_addr(output: str) -> list[dict]:
    interfaces = []
    link_macs = {}
    lines = output.splitlines()

    block = False
    for line in lines:
        if line.startswith("### ip -br link"):
            block = "link"
            continue
        if line.startswith("### ") and block:
            block = False
        if block == "link":
            parts = line.split()
            if len(parts) >= 3 and re.match(r"^([0-9a-f]{2}:){5}[0-9a-f]{2}$", parts[2], re.I):
                link_macs[parts[0]] = parts[2].lower()

    block = False
    for line in lines:
        if line.startswith("### ip -br addr"):
            block = "addr"
            continue
        if line.startswith("### ") and block:
            block = False
        if block == "addr":
            parts = line.split()
            if len(parts) >= 3:
                interfaces.append({"name": parts[0], "state": parts[1], "ip": parts[2], "mac": link_macs.get(parts[0])})
    return interfaces


def parse_docker_containers(output: str) -> list[dict]:
    containers = []
    in_ps = False
    in_inspect = False
    inspect_by_name = {}
    for line in output.splitlines():
        if line.startswith("### docker ps json"):
            in_ps = True
            in_inspect = False
            continue
        if line.startswith("### docker filtered inspect"):
            in_ps = False
            in_inspect = True
            continue
        if line.startswith("### ") and in_ps:
            in_ps = False
        if line.startswith("### ") and in_inspect:
            in_inspect = False
        if in_inspect and line.strip().startswith('"'):
            parts = line.split("|", 6)
            if len(parts) == 7:
                try:
                    name = json.loads(parts[0]).lstrip("/")
                    inspect_by_name[name] = {
                        "image": json.loads(parts[1]),
                        "state": json.loads(parts[2]),
                        "restart_policy": json.loads(parts[3]),
                        "ports": json.loads(parts[4]),
                        "mounts": json.loads(parts[5]),
                        "networks": json.loads(parts[6]),
                    }
                except (json.JSONDecodeError, TypeError):
                    pass
        if not in_ps or not line.strip().startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        containers.append(
            {
                "name": obj.get("Names"),
                "image": obj.get("Image"),
                "status": obj.get("Status"),
                "ports": obj.get("Ports"),
            }
        )
    for item in containers:
        if item["name"] in inspect_by_name:
            item.update(inspect_by_name[item["name"]])
    return containers


def parse_proxmox_resources(output: str) -> list[dict]:
    marker = "### cluster resources json"
    if marker not in output:
        return []
    segment = output.split(marker, 1)[1]
    next_marker = segment.find("\n### ")
    if next_marker >= 0:
        segment = segment[:next_marker]
    segment = segment.strip()
    if not segment:
        return []
    try:
        resources = json.loads(segment)
    except json.JSONDecodeError:
        return []
    keep = []
    for item in resources:
        if item.get("type") in {"qemu", "lxc"}:
            keep.append(
                {
                    "id": item.get("id"),
                    "vmid": item.get("vmid"),
                    "name": item.get("name"),
                    "node": item.get("node"),
                    "type": item.get("type"),
                    "status": item.get("status"),
                    "maxmem": item.get("maxmem"),
                    "maxdisk": item.get("maxdisk"),
                    "uptime": item.get("uptime"),
                }
            )
    return keep


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", default=str(DEFAULT_AUDIT_ROOT))
    parser.add_argument("--password-env", default="EDSYS_AUDIT_PASSWORD")
    args = parser.parse_args()

    password = os.environ.get(args.password_env)
    if not password:
        password = getpass.getpass("SSH password: ")

    audit_root = Path(args.audit_root)
    audit_id = "edsys-credentialed-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    audit_path = audit_root / audit_id
    for folder in ["host-checks", "network", "docker", "proxmox", "pihole", "switch", "sanitized-review"]:
        (audit_path / folder).mkdir(parents=True, exist_ok=True)

    write_text(
        audit_path / "RAW_README.md",
        f"# Credentialed EdSys Audit Output\n\nAudit ID: {audit_id}\nCreated: {now_iso()}\n\n"
        "Outputs in this folder are sanitized before writing. Do not commit this raw folder.\n",
    )
    (audit_root / "LATEST_AUDIT_PATH.txt").write_text(str(audit_path), encoding="utf-8")

    access_rows = []
    device_updates = []
    service_updates = []
    mac_updates = []
    proxmox_resources = []
    containers_by_host = {}

    for host in HOSTS:
        row = {
            "name": host["name"],
            "host": host["host"],
            "user": host["user"],
            "kind": host["kind"],
            "ssh_access": False,
            "exit_code": "",
            "output_file": "",
            "checked_at": now_iso(),
            "error": "",
        }
        try:
            client = ssh_connect(host, password)
            row["ssh_access"] = True
            if host["kind"] == "switch":
                exit_code, output = run_switch_commands(client, password)
                out_rel = Path("switch") / f"{host['name']}-switch_SANITIZED.txt"
            else:
                sudo = host["user"] != "root"
                if host["name"] == "edcore":
                    cmd = EDCORE_EXTRA_CMD
                elif host["kind"] == "docker":
                    cmd = DOCKER_CMD
                elif host["kind"] == "pihole":
                    cmd = PIHOLE_CMD
                elif host["kind"] == "proxmox":
                    cmd = PROXMOX_CMD
                    sudo = False
                else:
                    cmd = GENERAL_CMD
                exit_code, output = run_command(client, cmd, password, sudo=sudo)
                folder = "proxmox" if host["kind"] == "proxmox" else "pihole" if host["kind"] == "pihole" else "docker" if host["kind"] == "docker" else "host-checks"
                out_rel = Path(folder) / f"{host['name']}-ssh-baseline_SANITIZED.txt"
            client.close()

            clean_output = sanitize(output, password)
            out_path = audit_path / out_rel
            write_text(out_path, clean_output)
            row["exit_code"] = exit_code
            row["output_file"] = str(out_path)

            hostname = parse_hostname(clean_output) or host["name"]
            os_name = parse_os(clean_output)
            mac = parse_primary_mac(clean_output)
            interfaces = parse_ip_br_addr(clean_output)
            device_updates.append(
                {
                    "hostname": hostname,
                    "audit_name": host["name"],
                    "ip": host["host"],
                    "mac": mac,
                    "os": os_name,
                    "interfaces": interfaces,
                    "source": f"{audit_id}:{out_rel.as_posix()}",
                    "confidence": "high" if row["ssh_access"] else "low",
                    "last_verified": row["checked_at"],
                    "notes": f"Credentialed SSH as {host['user']} succeeded; output sanitized before write.",
                }
            )
            for iface in interfaces:
                if iface.get("mac"):
                    mac_updates.append(
                        {
                            "mac": iface["mac"],
                            "ip": iface.get("ip"),
                            "hostname": hostname,
                            "source": f"{audit_id}:{out_rel.as_posix()}",
                            "interface_or_switch_port": iface.get("name"),
                            "first_seen": row["checked_at"],
                            "confidence": "high",
                            "notes": f"Local interface on {hostname}",
                        }
                    )
            containers = parse_docker_containers(clean_output)
            if containers:
                containers_by_host[host["name"]] = containers
                for container in containers:
                    service_updates.append(
                        {
                            "name": container.get("name"),
                            "host": host["name"],
                            "ip": host["host"],
                            "runtime": "Docker",
                            "container_name": container.get("name"),
                            "image": container.get("image"),
                            "status": container.get("status"),
                            "ports": container.get("ports"),
                            "restart_policy": container.get("restart_policy"),
                            "mounts": container.get("mounts"),
                            "networks": container.get("networks"),
                            "source": f"{audit_id}:{out_rel.as_posix()}",
                            "confidence": "high",
                            "last_verified": row["checked_at"],
                            "notes": "Filtered docker ps metadata; environment variables and logs were not collected.",
                        }
                    )
            prox = parse_proxmox_resources(clean_output)
            if prox:
                proxmox_resources.extend(prox)
        except Exception as exc:  # noqa: BLE001 - audit should continue host by host
            row["error"] = sanitize(str(exc), password).strip()
        access_rows.append(row)

    proxmox_resources = sorted(
        {
            (item.get("type"), item.get("vmid")): item
            for item in proxmox_resources
        }.values(),
        key=lambda item: (str(item.get("type")), int(item.get("vmid") or 0)),
    )

    write_csv(audit_path / "host-checks" / "credentialed-ssh-access.csv", access_rows)

    proposed_network = {
        "metadata": {
            "audit_id": audit_id,
            "generated": now_iso(),
            "safety": "sanitized credentialed SSH inventory; no credentials or environment values",
        },
        "devices": device_updates,
        "mac_inventory": mac_updates,
        "proxmox_resources": proxmox_resources,
    }
    proposed_services = {
        "metadata": {
            "audit_id": audit_id,
            "generated": now_iso(),
            "safety": "sanitized service inventory from filtered commands",
        },
        "services": service_updates,
        "docker_containers_by_host": containers_by_host,
    }
    proposed_macs = {
        "metadata": {
            "audit_id": audit_id,
            "generated": now_iso(),
            "safety": "sanitized MAC/IP/hostname infrastructure identity",
        },
        "mac_inventory": mac_updates,
    }

    for name, data in [
        ("PROPOSED_NETWORK_UPDATES.yml", proposed_network),
        ("PROPOSED_SERVICE_UPDATES.yml", proposed_services),
        ("PROPOSED_MAC_UPDATES.yml", proposed_macs),
    ]:
        write_text(audit_path / name, yaml.safe_dump(data, sort_keys=False, allow_unicode=False, width=120))
        write_text(audit_path / "sanitized-review" / name, yaml.safe_dump(data, sort_keys=False, allow_unicode=False, width=120))

    unreachable = [r for r in access_rows if not r["ssh_access"]]
    summary_lines = [
        "# Credentialed EdSys SSH Inventory Summary",
        "",
        f"- Audit ID: `{audit_id}`",
        f"- Generated: {now_iso()}",
        "- Safety: sanitized SSH inventory; password was used at runtime only and not written",
        "",
        "## SSH Access",
        "",
        "| Host | IP | User | Access | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in access_rows:
        summary_lines.append(
            f"| {row['name']} | {row['host']} | {row['user']} | {row['ssh_access']} | {row['error'] or 'ok'} |"
        )
    summary_lines.extend(["", "## Docker Containers By Host", ""])
    for host_name, containers in containers_by_host.items():
        summary_lines.append(f"### {host_name}")
        for container in containers:
            summary_lines.append(f"- {container.get('name')} - {container.get('image')} - {container.get('ports') or 'no published ports'}")
        summary_lines.append("")
    summary_lines.extend(["## Proxmox Resources", ""])
    for item in proxmox_resources:
        summary_lines.append(
            f"- {item.get('type')} {item.get('vmid')} `{item.get('name')}` on {item.get('node')} status={item.get('status')}"
        )
    if not proxmox_resources:
        summary_lines.append("- No Proxmox resources collected.")
    summary_lines.extend(["", "## Unreachable Or Failed SSH", ""])
    if unreachable:
        for row in unreachable:
            summary_lines.append(f"- {row['name']} ({row['host']}): {row['error']}")
    else:
        summary_lines.append("- None.")

    summary = "\n".join(summary_lines) + "\n"
    write_text(audit_path / "SANITIZED_SUMMARY.md", summary)
    write_text(audit_path / "sanitized-review" / "SANITIZED_SUMMARY.md", summary)

    manual = [
        "# Manual Commands Still Needed",
        "",
        "- Aruba switch: if SSH failed, run read-only `show mac-address`, `show arp`, `show lldp info remote-device`, `show interfaces brief`, and `show vlan` from the switch CLI.",
        "- UniFi controller: export client/AP inventory from the controller UI if API credentials are not available.",
        "- Verify pve-node1 DHCP reservation MAC mismatch before changing dnsmasq reservations.",
    ]
    write_text(audit_path / "MANUAL_COMMANDS_NEEDED.md", "\n".join(manual) + "\n")
    write_text(audit_path / "sanitized-review" / "MANUAL_COMMANDS_NEEDED.md", "\n".join(manual) + "\n")

    print(audit_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
