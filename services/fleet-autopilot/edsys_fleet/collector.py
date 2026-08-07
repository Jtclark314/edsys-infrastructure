from __future__ import annotations

import base64
import json
import os
import platform
import shutil
import socket
import time
from pathlib import Path
from typing import Any

from .config import FleetConfig
from .io import read_json, utc_now, write_json_atomic
from .proxmox import ProxmoxClient, ProxmoxError
from .runner import CommandResult, CommandRunner


LINUX_SCRIPT = r"""
set +e
export PATH="$HOME/.local/bin:$PATH"
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1
value() { command -v "$1" >/dev/null 2>&1 && "$@" 2>/dev/null | head -n1 || true; }
printf 'hostname=%s\n' "$(hostname)"
printf 'kernel=%s\n' "$(uname -r)"
printf 'os=%s\n' "$(. /etc/os-release 2>/dev/null; printf '%s' "${PRETTY_NAME:-Linux}")"
printf 'uptime=%s\n' "$(awk '{print int($1)}' /proc/uptime 2>/dev/null)"
printf 'cpu_model=%s\n' "$(lscpu 2>/dev/null | awk -F: '/Model name/{gsub(/^ +/,"",$2);print $2;exit}')"
printf 'cpu_cores=%s\n' "$(nproc 2>/dev/null)"
printf 'load=%s\n' "$(cut -d' ' -f1 /proc/loadavg 2>/dev/null)"
printf 'memory_total=%s\n' "$(awk '/MemTotal/{print $2*1024}' /proc/meminfo 2>/dev/null)"
printf 'memory_available=%s\n' "$(awk '/MemAvailable/{print $2*1024}' /proc/meminfo 2>/dev/null)"
printf 'disk_total=%s\n' "$(df -B1 / 2>/dev/null | awk 'NR==2{print $2}')"
printf 'disk_available=%s\n' "$(df -B1 / 2>/dev/null | awk 'NR==2{print $4}')"
printf 'codex=%s\n' "$(codex --version 2>/dev/null | awk '{print $2}')"
printf 'node=%s\n' "$(node --version 2>/dev/null | sed 's/^v//')"
printf 'npm=%s\n' "$(npm --version 2>/dev/null)"
printf 'docker=%s\n' "$(docker version --format '{{.Server.Version}}' 2>/dev/null)"
printf 'chrome=%s\n' "$(google-chrome --version 2>/dev/null | awk '{print $3}')"
printf 'vivaldi=%s\n' "$(vivaldi --version 2>/dev/null | awk '{print $2}')"
printf 'ollama=%s\n' "$(ollama --version 2>/dev/null | awk '{print $NF}')"
printf 'gpu=%s\n' "$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n1)"
printf 'driver=%s\n' "$(nvidia-smi --query-gpu=driver_version --format=csv,noheader 2>/dev/null | head -n1)"
printf 'gpu_memory_total=%s\n' "$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null | head -n1)"
printf 'gpu_memory_used=%s\n' "$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | head -n1)"
printf 'gpu_utilization=%s\n' "$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits 2>/dev/null | head -n1)"
printf 'sudo=%s\n' "$(sudo -n true >/dev/null 2>&1 && echo yes || echo no)"
"""


WINDOWS_SCRIPT = r"""
$ErrorActionPreference='SilentlyContinue'
$os=Get-CimInstance Win32_OperatingSystem
$cpu=Get-CimInstance Win32_Processor | Select-Object -First 1
$disk=Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'"
$gpu=Get-CimInstance Win32_VideoController | Where-Object {$_.Name -match 'NVIDIA'} | Select-Object -First 1
$codexCmd=Get-Command codex -ErrorAction SilentlyContinue
$nodeCmd=Get-Command node -ErrorAction SilentlyContinue
$npmCmd=Get-Command npm -ErrorAction SilentlyContinue
$dockerCmd=Get-Command docker -ErrorAction SilentlyContinue
$ollamaCmd=Get-Command ollama -ErrorAction SilentlyContinue
$nvidiaCmd=Get-Command nvidia-smi -ErrorAction SilentlyContinue
$codex=if($codexCmd){[string](& $codexCmd.Source --version 2>$null | Select-Object -First 1)}else{$null}
$node=if($nodeCmd){[string](& $nodeCmd.Source --version 2>$null | Select-Object -First 1)}else{$null}
$npm=if($npmCmd){[string](& $npmCmd.Source --version 2>$null | Select-Object -First 1)}else{$null}
$docker=if($dockerCmd){[string](& $dockerCmd.Source version --format '{{.Server.Version}}' 2>$null | Select-Object -First 1)}else{$null}
$chromePath='C:\Program Files\Google\Chrome\Application\chrome.exe'
$chrome=if(Test-Path $chromePath){(Get-Item $chromePath).VersionInfo.ProductVersion}else{$null}
$nvidia=if($nvidiaCmd){[string](& $nvidiaCmd.Source --query-gpu=name,driver_version,memory.total,memory.used,utilization.gpu --format=csv,noheader,nounits 2>$null | Select-Object -First 1)}else{$null}
[pscustomobject]@{
 hostname=$env:COMPUTERNAME
 os="$($os.Caption) $($os.Version)"
 uptime=[int]((Get-Date)-$os.LastBootUpTime).TotalSeconds
 cpu_model=[string]$cpu.Name
 cpu_cores=[int]$cpu.NumberOfLogicalProcessors
 load=$null
 memory_total=[int64]$os.TotalVisibleMemorySize*1024
 memory_available=[int64]$os.FreePhysicalMemory*1024
 disk_total=[int64]$disk.Size
 disk_available=[int64]$disk.FreeSpace
 codex=($codex -replace '^codex-cli\s+','')
 node=($node -replace '^v','')
 npm=$npm
 docker=$docker
 chrome=$chrome
 vivaldi=$null
 ollama=$(if($ollamaCmd){[string](& $ollamaCmd.Source --version 2>$null | Select-Object -First 1) -replace '^.*version\s+',''}else{$null})
 gpu=[string]$gpu.Name
 nvidia=$nvidia
 sudo='administrator'
} | ConvertTo-Json -Compress
"""


class FleetCollector:
    def __init__(self, config: FleetConfig, runner: CommandRunner | None = None):
        self.config = config
        self.runner = runner or CommandRunner(config.timeout)
        self.proxmox = ProxmoxClient(config, self.runner)

    def collect(self) -> dict[str, Any]:
        started = time.monotonic()
        hosts = [self.collect_host(item) for item in self.config.hosts]
        proxmox = self.collect_proxmox()
        observations = self.collect_observations()
        drifts = [drift for host in hosts for drift in host.get("drift", [])]
        online = sum(1 for host in hosts if host["status"] != "offline")
        critical = sum(1 for item in drifts if item.get("severity") == "critical")
        warnings = sum(1 for item in drifts if item.get("severity") == "warning")
        snapshot = {
            "schema_version": 1,
            "generated_at": utc_now(),
            "collector": socket.gethostname(),
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "summary": {
                "hosts_total": len(hosts),
                "hosts_online": online,
                "hosts_offline": len(hosts) - online,
                "drift_total": len(drifts),
                "critical_drift": critical,
                "warning_drift": warnings,
                "proxmox_nodes_online": sum(1 for node in proxmox.get("nodes", []) if node.get("online")),
                "proxmox_guests_running": sum(1 for guest in proxmox.get("guests", []) if guest.get("status") == "running"),
            },
            "approved": self.config.approved,
            "hosts": hosts,
            "proxmox": proxmox,
            "observations": observations,
            "capabilities": self._capabilities(hosts, proxmox),
        }
        write_json_atomic(self.config.state_root / "snapshot.json", snapshot, mode=0o640)
        return snapshot

    def collect_host(self, host: dict[str, Any]) -> dict[str, Any]:
        started = time.monotonic()
        if host.get("transport") == "local":
            result = self.runner.run(["bash", "-lc", LINUX_SCRIPT])
            raw = self._parse_lines(result)
        elif host.get("platform") == "windows":
            result = self._powershell(str(host.get("ssh_alias")), WINDOWS_SCRIPT)
            raw = result.json({}) if result.ok else {}
        else:
            encoded = base64.b64encode(LINUX_SCRIPT.encode()).decode()
            result = self.runner.ssh(str(host.get("ssh_alias")), f"echo {encoded} | base64 -d | bash")
            raw = self._parse_lines(result)
        latency = round((time.monotonic() - started) * 1000)
        if not result.ok:
            return {
                **host,
                "status": "offline",
                "reachable": False,
                "checked_at": utc_now(),
                "latency_ms": latency,
                "detail": result.stderr or "host unreachable",
                "versions": {},
                "metrics": {},
                "hardware": {},
                "capabilities": [],
                "drift": [{"host": host["id"], "component": "reachability", "severity": "critical", "current": "offline", "approved": "online"}],
            }
        normalized = self._normalize_host(raw)
        drift = self._drift(host["id"], normalized["versions"])
        status = "warning" if any(item.get("severity") in {"warning", "critical"} for item in drift) else "ok"
        return {
            **host,
            "status": status,
            "reachable": True,
            "checked_at": utc_now(),
            "latency_ms": latency,
            **normalized,
            "drift": drift,
        }

    def collect_proxmox(self) -> dict[str, Any]:
        try:
            cluster = self.proxmox.cluster_status()
            guests = self.proxmox.resources("vm")
            storages = self.proxmox.storages()
            tasks = self.proxmox.recent_tasks(20)
        except ProxmoxError as exc:
            return {"status": "offline", "detail": str(exc), "nodes": [], "guests": [], "storages": [], "tasks": []}
        nodes = []
        cluster_name = "EdSys"
        quorate = False
        for item in cluster:
            if item.get("type") == "cluster":
                cluster_name = item.get("name") or cluster_name
                quorate = bool(item.get("quorate"))
            elif item.get("type") == "node":
                nodes.append({
                    "name": item.get("name"),
                    "online": bool(item.get("online")),
                    "ip": item.get("ip"),
                    "nodeid": item.get("nodeid"),
                })
        clean_guests = []
        for guest in guests:
            maxmem = int(guest.get("maxmem") or 0)
            mem = int(guest.get("mem") or 0)
            clean_guests.append({
                "id": guest.get("id"),
                "vmid": guest.get("vmid"),
                "name": guest.get("name") or guest.get("id"),
                "node": guest.get("node"),
                "type": guest.get("type"),
                "status": guest.get("status"),
                "cpu_percent": round(float(guest.get("cpu") or 0) * 100, 1),
                "memory_percent": round((mem / maxmem * 100), 1) if maxmem else 0,
                "memory_used": mem,
                "memory_total": maxmem,
                "uptime": int(guest.get("uptime") or 0),
            })
        clean_storages = []
        for storage in storages:
            maxdisk = int(storage.get("maxdisk") or 0)
            disk = int(storage.get("disk") or 0)
            clean_storages.append({
                "id": storage.get("id"),
                "node": storage.get("node"),
                "storage": storage.get("storage"),
                "status": storage.get("status"),
                "used_percent": round((disk / maxdisk * 100), 1) if maxdisk else 0,
                "used": disk,
                "total": maxdisk,
            })
        return {
            "status": "ok" if quorate and all(node["online"] for node in nodes) else "warning",
            "cluster": cluster_name,
            "quorate": quorate,
            "nodes": nodes,
            "guests": clean_guests,
            "storages": clean_storages,
            "tasks": [self._sanitize_task(item) for item in tasks],
        }

    def collect_observations(self) -> list[dict[str, Any]]:
        observations: list[dict[str, Any]] = []
        local_root = Path.home() / ".codex" / "plugin-recovery"
        local_states = sorted(local_root.glob("*/observation-state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if local_states:
            state = read_json(local_states[0], {})
            if state:
                observations.append(self._observation("9950x", state))
        nimo_script = r"""
$root=Join-Path $env:LOCALAPPDATA 'EdSys-Private\codex-plugin-observation'
$item=Get-ChildItem $root -Recurse -Filter observation-state.json -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if($item){Get-Content $item.FullName -Raw}else{'{}'}
"""
        nimo_result = self._powershell("nimo-laptop", nimo_script)
        nimo_state = nimo_result.json({}) if nimo_result.ok else {}
        if nimo_state:
            observations.append(self._observation("nimo", nimo_state))
        return observations

    def _powershell(self, alias: str, script: str) -> CommandResult:
        encoded = base64.b64encode(script.encode("utf-16le")).decode()
        return self.runner.ssh(alias, f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {encoded}")

    @staticmethod
    def _parse_lines(result: CommandResult) -> dict[str, str]:
        output: dict[str, str] = {}
        for line in result.stdout.splitlines():
            key, separator, value = line.partition("=")
            if separator:
                output[key.strip()] = value.strip()
        return output

    def _normalize_host(self, raw: dict[str, Any]) -> dict[str, Any]:
        versions = {key: self._clean_version(raw.get(key)) for key in ("codex", "node", "npm", "docker", "chrome", "vivaldi", "ollama")}
        versions = {key: value for key, value in versions.items() if value}
        total_mem = self._int(raw.get("memory_total"))
        avail_mem = self._int(raw.get("memory_available"))
        total_disk = self._int(raw.get("disk_total"))
        avail_disk = self._int(raw.get("disk_available"))
        gpu_name = str(raw.get("gpu") or "").strip()
        nvidia_fields = [part.strip() for part in str(raw.get("nvidia") or "").split(",")]
        driver = str(raw.get("driver") or (nvidia_fields[1] if len(nvidia_fields) > 1 else "")).strip()
        gpu_total = self._int(raw.get("gpu_memory_total") or (nvidia_fields[2] if len(nvidia_fields) > 2 else 0))
        gpu_used = self._int(raw.get("gpu_memory_used") or (nvidia_fields[3] if len(nvidia_fields) > 3 else 0))
        gpu_util = self._float(raw.get("gpu_utilization") or (nvidia_fields[4] if len(nvidia_fields) > 4 else 0))
        capabilities = []
        if versions.get("docker"):
            capabilities.append("Docker")
        if versions.get("ollama"):
            capabilities.append("Ollama")
        if gpu_name:
            capabilities.extend(["NVIDIA GPU", "NVENC", "Vulkan"])
        if versions.get("codex"):
            capabilities.append("Codex")
        if versions.get("chrome"):
            capabilities.append("Chrome")
        if str(raw.get("sudo") or "") in {"yes", "administrator"}:
            capabilities.append("Admin")
        return {
            "detail": str(raw.get("os") or raw.get("hostname") or "online"),
            "os": str(raw.get("os") or "unknown"),
            "hostname": str(raw.get("hostname") or "unknown"),
            "uptime_seconds": self._int(raw.get("uptime")),
            "versions": versions,
            "metrics": {
                "cpu_cores": self._int(raw.get("cpu_cores")),
                "cpu_load": self._float(raw.get("load")),
                "memory_total": total_mem,
                "memory_used": max(0, total_mem - avail_mem),
                "memory_percent": round((total_mem - avail_mem) / total_mem * 100, 1) if total_mem else 0,
                "disk_total": total_disk,
                "disk_used": max(0, total_disk - avail_disk),
                "disk_percent": round((total_disk - avail_disk) / total_disk * 100, 1) if total_disk else 0,
            },
            "hardware": {
                "cpu": str(raw.get("cpu_model") or "unknown"),
                "gpu": gpu_name,
                "driver": driver,
                "gpu_memory_total_mib": gpu_total,
                "gpu_memory_used_mib": gpu_used,
                "gpu_utilization_percent": gpu_util,
            },
            "capabilities": capabilities,
        }

    def _drift(self, host_id: str, versions: dict[str, str]) -> list[dict[str, Any]]:
        output = []
        for component, approved in self.config.approved.items():
            if component not in versions:
                continue
            current = versions[component]
            if current != approved:
                ahead = self._version_key(current) > self._version_key(approved)
                output.append({
                    "host": host_id,
                    "component": component,
                    "severity": "info" if ahead else "warning",
                    "current": current,
                    "approved": approved,
                    "action": "review" if ahead else "upgrade",
                })
        return output

    @staticmethod
    def _version_key(value: str) -> tuple[int, ...]:
        parts = []
        for piece in value.replace("v", "").split("."):
            digits = "".join(ch for ch in piece if ch.isdigit())
            parts.append(int(digits or 0))
        return tuple(parts)

    @staticmethod
    def _clean_version(value: Any) -> str:
        text = str(value or "").strip()
        return text.removeprefix("v")

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(str(value or 0).replace(" MiB", "")))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _float(value: Any) -> float:
        try:
            return round(float(str(value or 0).replace("%", "")), 2)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _sanitize_task(task: dict[str, Any]) -> dict[str, Any]:
        return {key: task.get(key) for key in ("upid", "node", "type", "status", "starttime", "endtime", "user") if task.get(key) is not None}

    @staticmethod
    def _observation(host: str, state: dict[str, Any]) -> dict[str, Any]:
        expected = int(state.get("expected_samples") or 96)
        samples = int(state.get("samples") or 0)
        return {
            "host": host,
            "run_id": state.get("run_id") or state.get("runId") or "unknown",
            "plugin": state.get("plugin") or "cloudflare@openai-curated",
            "status": state.get("status") or "unknown",
            "samples": samples,
            "expected_samples": expected,
            "failures": int(state.get("failures") or 0),
            "progress_percent": round(samples / expected * 100, 1) if expected else 0,
            "deadline": state.get("deadline"),
            "last_sample_at": state.get("last_sample_at"),
        }

    @staticmethod
    def _capabilities(hosts: list[dict[str, Any]], proxmox: dict[str, Any]) -> list[dict[str, Any]]:
        names = ["Codex", "Chrome", "Docker", "NVIDIA GPU", "NVENC", "Vulkan", "Ollama", "Admin"]
        result = []
        for name in names:
            active = [host["id"] for host in hosts if name in host.get("capabilities", [])]
            result.append({"name": name, "status": "ok" if active else "unknown", "hosts": active, "count": len(active)})
        result.append({"name": "Proxmox MCP", "status": proxmox.get("status", "unknown"), "hosts": [node["name"] for node in proxmox.get("nodes", []) if node.get("online")], "count": len(proxmox.get("nodes", []))})
        return result
