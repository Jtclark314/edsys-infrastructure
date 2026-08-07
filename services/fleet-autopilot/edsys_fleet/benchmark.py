from __future__ import annotations

import hashlib
import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import yaml

from .config import FleetConfig
from .io import write_json_atomic
from .notify import notify_critical_failure
from .proxmox import ProxmoxClient
from .runner import CommandRunner
from .store import FleetStore, sanitize_evidence


CONTRACT_PATH = Path(__file__).with_name("capability-contract.yml")
BROWSER_PROBE = Path(__file__).with_name("probes") / "browser_probe.cjs"
MCP_PROBE = Path(__file__).with_name("probes") / "codex_mcp_probe.py"


class BenchmarkError(RuntimeError):
    pass


class CapabilityBenchmark:
    def __init__(self, config: FleetConfig, runner: CommandRunner | None = None):
        self.config = config
        self.runner = runner or CommandRunner(60)
        self.store = FleetStore(config.state_root)
        self.contract = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
        self.proxmox = ProxmoxClient(config, self.runner)

    def run(self, suite: str, host_id: str = "9950x", triggered_by: str = "cli") -> dict[str, Any]:
        if suite not in self.contract.get("suites", {}):
            raise BenchmarkError(f"Unknown benchmark suite: {suite}")
        if host_id != "9950x":
            raise BenchmarkError("Direct benchmark execution is currently local to the canonical 9950x hub")
        self.store.reconcile_stale_benchmarks()
        contract_version = str(self.contract["contract_version"])
        run_id = self.store.start_benchmark(suite, contract_version, host_id, triggered_by)
        artifact_dir = self.config.private_artifact_root / "benchmarks" / run_id
        artifact_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        notification: dict[str, Any] = {"configured": False, "delivered": False}
        critical_alert_sent = False
        probes: list[tuple[str, str, bool, Callable[[Path], tuple[bool, dict[str, Any], str]]]] = [
            ("codex_authority", "outside_project_file", True, self._outside_project_file),
            ("codex_authority", "network_login_shell_admin", True, self._authority),
            ("docker", "digest_pinned_disposable", True, self._docker),
            ("gpu_media", "nvidia_vulkan_nvenc", True, self._gpu_media),
            ("gpu_media", "nvidia_container_runtime", True, self._nvidia_container),
            ("browser", "chrome_playwright_webgpu", True, self._browser),
            ("mcp", "inventory_and_proxmox_read", True, self._mcp),
            ("remote_hosts", "key_only_temp_network_admin", True, self._remote_hosts),
            ("documents", "docx_pdf_render_validate", True, self._documents),
            ("infrastructure", "proxmox_quorum_canary", True, self._infrastructure),
        ]
        for category, probe, critical, function in probes:
            started = time.monotonic()
            try:
                passed, evidence, cleanup = function(artifact_dir)
                status = "passed" if passed else "failed"
            except Exception as exc:
                status = "failed"
                evidence = {"error": f"{type(exc).__name__}: {exc}"}
                cleanup = "unknown"
            self.store.add_benchmark_result(
                run_id,
                {
                    "category": category,
                    "probe": probe,
                    "status": status,
                    "critical": critical,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "evidence": evidence,
                    "artifact_ref": str(artifact_dir) if category in {"browser", "documents"} else None,
                    "cleanup_status": cleanup,
                },
            )
            if critical and status != "passed" and not critical_alert_sent:
                notification = notify_critical_failure(
                    title="EdSys Fleet benchmark failed",
                    message=(
                        f"{suite} capability benchmark {run_id} failed at "
                        f"{category}/{probe} on {host_id}. Acceptance is blocked."
                    ),
                )
                critical_alert_sent = True
        if suite == "ultra":
            started = time.monotonic()
            try:
                passed, evidence, cleanup = self._ultra(artifact_dir)
            except Exception as exc:
                passed = False
                evidence = {"error": f"{type(exc).__name__}: {exc}"}
                cleanup = "unknown"
            self.store.add_benchmark_result(
                run_id,
                {
                    "category": "codex_authority",
                    "probe": "real_gpt_5_6_sol_ultra_priority",
                    "status": "passed" if passed else "failed",
                    "critical": True,
                    "elapsed_ms": round((time.monotonic() - started) * 1000),
                    "evidence": evidence,
                    "artifact_ref": str(artifact_dir),
                    "cleanup_status": cleanup,
                },
            )
            if not passed and not critical_alert_sent:
                notification = notify_critical_failure(
                    title="EdSys Fleet Ultra benchmark failed",
                    message=(
                        f"Weekly Ultra capability benchmark {run_id} failed on {host_id}. "
                        "Acceptance is blocked."
                    ),
                )
                critical_alert_sent = True
        completed = self.store.finish_benchmark(
            run_id,
            summary={
                "suite": suite,
                "host": host_id,
                "artifact_dir": str(artifact_dir),
                "notification": notification,
            },
        )
        self._publish_latest(completed)
        self._prune_retention()
        return completed

    def _publish_latest(self, completed: dict[str, Any]) -> None:
        all_recent = self.store.list_benchmarks(128)
        recent = [
            item
            for item in all_recent
            if item.get("suite") == "deterministic" and item.get("host_id") == "9950x"
        ]
        consecutive = self._scheduled_daily_streak(recent)
        keys = (
            "id", "suite", "host_id", "status", "score", "critical_failures",
            "started_at", "completed_at", "contract_version",
        )

        def compact(value: dict[str, Any] | None) -> dict[str, Any] | None:
            return {key: value.get(key) for key in keys} if value else None

        latest_ultra = next(
            (item for item in all_recent if item.get("suite") == "ultra"), None
        )
        summary = {
            "schema_version": 1,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "latest": compact(completed),
            "latest_deterministic": compact(recent[0] if recent else None),
            "latest_ultra": compact(latest_ultra),
            "consecutive_deterministic_passes": consecutive,
        }
        write_json_atomic(self.config.state_root / "benchmark-latest.json", summary, mode=0o640)

    @staticmethod
    def _scheduled_daily_streak(runs: list[dict[str, Any]]) -> int:
        zone = ZoneInfo("America/New_York")
        by_day: dict[Any, bool] = {}
        for item in runs:
            if item.get("triggered_by") != "systemd-daily" or not item.get("completed_at"):
                continue
            try:
                day = datetime.fromisoformat(str(item["completed_at"])).astimezone(zone).date()
            except (TypeError, ValueError):
                continue
            by_day[day] = by_day.get(day, True) and item.get("status") == "passed"
        if not by_day:
            return 0
        streak = 0
        expected = max(by_day)
        for day in sorted(by_day, reverse=True):
            if day != expected or not by_day[day]:
                break
            streak += 1
            expected = expected.fromordinal(expected.toordinal() - 1)
        return streak

    def _prune_retention(self) -> None:
        now = datetime.now(timezone.utc)
        raw_cutoff = now - timedelta(days=self.config.raw_benchmark_retention_days)
        benchmark_root = self.config.private_artifact_root / "benchmarks"
        if benchmark_root.is_dir():
            for path in benchmark_root.iterdir():
                if not path.is_dir() or path.is_symlink():
                    continue
                try:
                    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                except FileNotFoundError:
                    continue
                if modified < raw_cutoff:
                    shutil.rmtree(path, ignore_errors=True)
        detail_cutoff = now - timedelta(days=self.config.benchmark_detail_retention_days)
        self.store.prune_benchmark_details(detail_cutoff.isoformat())

    def _command(self, argv: list[str], timeout: int = 60, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
            env=env,
        )

    def _outside_project_file(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        root = Path.home() / ".local" / "state" / "edsys-fleet-benchmark"
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / f"authority-{os.getpid()}.txt"
        payload = b"EdSys Fleet authority canary\n"
        path.write_bytes(payload)
        os.chmod(path, 0o600)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        path.unlink()
        return digest == hashlib.sha256(payload).hexdigest() and not path.exists(), {"sha256": digest}, "passed"

    def _authority(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        doctor_env = dict(os.environ)
        # systemd/automation commonly supplies TERM=dumb, which makes Codex
        # Doctor fail only its presentation check. Exercise Doctor with a
        # capable noninteractive terminal identity so this probe measures Codex
        # authority and health rather than the benchmark runner's UI choice.
        doctor_env["TERM"] = "xterm-256color"
        checks = {
            "network": self._command(["curl", "-fsS", "--max-time", "15", "https://example.com"]),
            "login_shell": self._command(["bash", "-lc", "printf EDSYS_LOGIN_SHELL_OK"]),
            "admin": self._command(["sudo", "-n", "true"]),
            "codex_doctor": self._command(
                ["codex", "doctor", "--summary", "--ascii"],
                timeout=120,
                env=doctor_env,
            ),
        }
        doctor_output = checks["codex_doctor"].stdout
        doctor_contract = {
            "unrestricted_filesystem_network": "unrestricted fs + enabled network" in doctor_output,
            "approval_never": "approval Never" in doctor_output,
            "zero_warn_fail": "0 warn | 0 fail" in doctor_output,
        }
        passed = all(item.returncode == 0 for item in checks.values()) and all(
            doctor_contract.values()
        )
        evidence = {
            name: {"returncode": item.returncode, "stdout_marker": item.stdout.strip()[-120:]}
            for name, item in checks.items()
        }
        evidence["codex_doctor"]["contract"] = doctor_contract
        evidence["codex_doctor"]["term"] = doctor_env["TERM"]
        return passed, evidence, "not_applicable"

    def _docker(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        image = str(self.contract["container_images"]["disposable"])
        name = f"edsys-fleet-benchmark-{os.getpid()}"
        inspect = self._command(["docker", "info", "--format", "{{json .ServerVersion}}"])
        run = self._command(
            [
                "docker",
                "run",
                "--pull=never",
                "--name",
                name,
                "--network",
                "bridge",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=1m",
                image,
                "python",
                "-c",
                "import pathlib,urllib.request; p=pathlib.Path('/tmp/x'); p.write_text('ok'); print(urllib.request.urlopen('https://example.com',timeout=10).status, p.read_text())",
            ],
            timeout=90,
        )
        remove = self._command(["docker", "rm", "-f", name])
        absent = self._command(["docker", "inspect", name])
        passed = inspect.returncode == 0 and run.returncode == 0 and "200 ok" in run.stdout and absent.returncode != 0
        return passed, {"engine": inspect.stdout.strip(), "container_exit": run.returncode, "marker": run.stdout.strip(), "image": image}, "passed" if remove.returncode == 0 or absent.returncode != 0 else "failed"

    def _gpu_media(self, artifact_dir: Path) -> tuple[bool, dict[str, Any], str]:
        output = artifact_dir / "nvenc-canary.mp4"
        nvidia = self._command(["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"])
        vulkan = self._command(["vulkaninfo", "--summary"], timeout=60)
        encode = self._command(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30",
                "-t", "1", "-c:v", "h264_nvenc", "-y", str(output),
            ],
            timeout=90,
        )
        verify = self._command(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height", "-of", "json", str(output)])
        digest = hashlib.sha256(output.read_bytes()).hexdigest() if output.exists() else ""
        output.unlink(missing_ok=True)
        passed = all(item.returncode == 0 for item in (nvidia, vulkan, encode, verify)) and '"codec_name": "h264"' in verify.stdout
        return passed, {"gpu": nvidia.stdout.strip(), "vulkan_summary": vulkan.stdout.strip()[:500], "ffprobe": verify.stdout.strip(), "artifact_sha256": digest}, "passed" if not output.exists() else "failed"

    def _nvidia_container(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        image = str(self.contract["container_images"]["nvidia"])
        result = self._command(
            ["docker", "run", "--pull=never", "--rm", "--gpus", "all", "--entrypoint", "nvidia-smi", image, "--query-gpu=name", "--format=csv,noheader"],
            timeout=120,
        )
        return result.returncode == 0 and bool(result.stdout.strip()), {"image": image, "gpu": result.stdout.strip(), "returncode": result.returncode}, "passed"

    def _browser(self, artifact_dir: Path) -> tuple[bool, dict[str, Any], str]:
        npm_root = self._command(["npm", "root", "-g"])
        env = dict(os.environ)
        env.update(
            {
                "NODE_PATH": npm_root.stdout.strip(),
                "FLEET_BENCHMARK_ARTIFACT_DIR": str(artifact_dir),
                "FLEET_PRIVATE_CANARY": str(self.contract["canaries"]["private_url"]),
                "FLEET_PUBLIC_CANARY": str(self.contract["canaries"]["public_url"]),
            }
        )
        result = self._command(["node", str(BROWSER_PROBE)], timeout=120, env=env)
        value = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {"error": result.stderr.strip()}
        return result.returncode == 0 and value.get("status") == "passed", value, "passed"

    def _mcp(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        probe = self._command(["python3", str(MCP_PROBE)], timeout=180)
        try:
            value = json.loads(probe.stdout)
        except json.JSONDecodeError:
            value = {"status": "failed", "error": probe.stderr[-500:]}
        calls = dict(value.get("calls") or {})
        required = {"browser", "cloudflare", "code_intelligence", "github", "openai_docs", "proxmox"}
        passed = (
            probe.returncode == 0
            and value.get("status") == "passed"
            and value.get("model_call") is False
            and required == set(calls)
            and all(bool(item.get("passed")) for item in calls.values())
            and bool(calls.get("cloudflare", {}).get("authenticated"))
            and bool(calls.get("github", {}).get("authenticated"))
        )
        return passed, value, calls.get("browser", {}).get("cleanup", "unknown")

    def _remote_hosts(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        evidence: dict[str, Any] = {}
        passed = True
        linux = "set -e; p=\"$HOME/.local/state/edsys-fleet-remote-$$\"; printf ok >\"$p\"; test \"$(cat \"$p\")\" = ok; rm -f \"$p\"; curl -fsS --max-time 10 https://example.com >/dev/null; sudo -n true; printf EDSYS_REMOTE_OK"
        windows = "$ErrorActionPreference='Stop';$p=Join-Path $env:LOCALAPPDATA ('EdSys-Private\\fleet-remote-'+$PID);[IO.File]::WriteAllText($p,'ok');if((Get-Content $p -Raw)-ne 'ok'){throw 'marker'};Remove-Item $p -Force;Invoke-WebRequest https://example.com -UseBasicParsing -TimeoutSec 10|Out-Null;'EDSYS_REMOTE_OK'"
        import base64
        for host, command in (
            ("edcore-ops", linux),
            ("basecamp", f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {base64.b64encode(windows.encode('utf-16le')).decode()}"),
            ("nimo-laptop", f"powershell.exe -NoProfile -NonInteractive -EncodedCommand {base64.b64encode(windows.encode('utf-16le')).decode()}"),
        ):
            result = self.runner.ssh(host, command, timeout=90)
            ok = result.ok and "EDSYS_REMOTE_OK" in result.stdout
            evidence[host] = {"passed": ok, "elapsed_ms": result.elapsed_ms}
            passed = passed and ok
        evidence["work-laptop"] = {"status": "dormant", "critical": False}
        return passed, evidence, "passed"

    def _documents(self, artifact_dir: Path) -> tuple[bool, dict[str, Any], str]:
        work = Path(tempfile.mkdtemp(prefix="edsys-fleet-docs-"))
        try:
            markdown = work / "canary.md"
            docx = work / "canary.docx"
            pdf = work / "canary.pdf"
            markdown.write_text("# EdSys Fleet Document Canary\n\nRendered and validated.\n", encoding="utf-8")
            docx_result = self._command(["pandoc", str(markdown), "-o", str(docx)], timeout=90)
            profile = work / "lo-profile"
            docx_pdf = self._command(
                [
                    "libreoffice",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(work),
                    str(docx),
                ],
                timeout=120,
            )
            text = self._command(["pdftotext", str(pdf), "-"])
            render_prefix = work / "render"
            render = self._command(["pdftoppm", "-f", "1", "-singlefile", "-png", "-r", "96", str(pdf), str(render_prefix)], timeout=90)
            rendered = work / "render.png"
            evidence_copy = artifact_dir / "document-canary-render.png"
            if rendered.exists():
                shutil.copy2(rendered, evidence_copy)
            passed = docx_result.returncode == 0 and docx_pdf.returncode == 0 and docx.exists() and pdf.exists() and text.returncode == 0 and "EdSys Fleet Document Canary" in text.stdout and render.returncode == 0 and rendered.exists()
            return passed, {"docx_bytes": docx.stat().st_size if docx.exists() else 0, "pdf_bytes": pdf.stat().st_size if pdf.exists() else 0, "render_sha256": hashlib.sha256(rendered.read_bytes()).hexdigest() if rendered.exists() else ""}, "passed"
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _infrastructure(self, artifact_dir: Path) -> tuple[bool, dict[str, Any], str]:
        lock_path = self.config.state_root / "benchmark-proxmox-canary.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            deadline = time.monotonic() + 120
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise BenchmarkError("Timed out waiting for the Proxmox canary lock")
                    time.sleep(1)
            try:
                return self._infrastructure_locked(artifact_dir)
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _infrastructure_locked(self, _: Path) -> tuple[bool, dict[str, Any], str]:
        cluster = self.proxmox.cluster_status()
        resources = self.proxmox.resources("vm")
        canary = self.contract["canaries"]["proxmox"]
        guest = next((item for item in resources if int(item.get("vmid") or 0) == int(canary["vmid"])), None)
        nodes = [item for item in cluster if item.get("type") == "node"]
        cluster_row = next((item for item in cluster if item.get("type") == "cluster"), {})
        preflight = bool(cluster_row.get("quorate")) and len(nodes) == 4 and all(bool(item.get("online")) for item in nodes) and bool(guest)
        if not preflight:
            return False, {"quorate": bool(cluster_row.get("quorate")), "nodes_online": sum(1 for item in nodes if item.get("online")), "canary_present": bool(guest)}, "not_applicable"
        node = str(canary["node"])
        vmid = int(canary["vmid"])
        guest_type = str(canary.get("type") or "lxc")
        if guest.get("node") != node or guest.get("type") != guest_type or guest.get("name") != canary.get("name"):
            return False, {"canary_identity_mismatch": True, "observed": {key: guest.get(key) for key in ("node", "type", "name", "vmid")}}, "not_applicable"
        initial = str(guest.get("status") or "stopped")
        snapshot_name = f"fleet-bench-{os.getpid()}"
        snapshot_created = False
        cleanup = "unknown"
        upids: list[str] = []
        try:
            if initial != "running":
                upid = str(self.proxmox.guest_action(node, vmid, "start", guest_type))
                self.proxmox.wait_task(node, upid, timeout=300)
                upids.append(upid)
            marker = self.runner.ssh(
                node,
                f"pct exec {vmid} -- sh -c 'umask 077; install -d -m 700 "
                "/var/lib/edsys-fleet-canary; printf baseline > "
                "/var/lib/edsys-fleet-canary/marker'",
                timeout=60,
            )
            if not marker.ok:
                raise BenchmarkError("Could not write the disposable canary marker")
            upid = str(
                self.proxmox.create_snapshot(
                    node, vmid, snapshot_name, "Fleet deterministic canary", guest_type
                )
            )
            self.proxmox.wait_task(node, upid, timeout=600)
            upids.append(upid)
            snapshot_created = True
            changed = self.runner.ssh(
                node,
                f"pct exec {vmid} -- sh -c 'printf changed > "
                "/var/lib/edsys-fleet-canary/marker'",
                timeout=60,
            )
            if not changed.ok:
                raise BenchmarkError("Could not mutate the disposable canary marker")
            upid = str(self.proxmox.rollback_snapshot(node, vmid, snapshot_name, guest_type))
            self.proxmox.wait_task(node, upid, timeout=600)
            upids.append(upid)
            status = self.proxmox.guest_status(node, vmid, guest_type)
            if status.get("status") != "running":
                upid = str(self.proxmox.guest_action(node, vmid, "start", guest_type))
                self.proxmox.wait_task(node, upid, timeout=300)
                upids.append(upid)
            restored = self.runner.ssh(
                node, f"pct exec {vmid} -- cat /var/lib/edsys-fleet-canary/marker", timeout=60
            )
            if not restored.ok or restored.stdout.strip() != "baseline":
                raise BenchmarkError("Canary snapshot rollback did not restore the marker")
            upid = str(self.proxmox.delete_snapshot(node, vmid, snapshot_name, guest_type))
            self.proxmox.wait_task(node, upid, timeout=600)
            upids.append(upid)
            snapshot_created = False
            removed = self.runner.ssh(
                node,
                f"pct exec {vmid} -- sh -c 'unlink /var/lib/edsys-fleet-canary/marker; "
                "rmdir /var/lib/edsys-fleet-canary'",
                timeout=60,
            )
            if not removed.ok:
                raise BenchmarkError("Could not remove the disposable canary marker")
            if initial != "running":
                upid = str(self.proxmox.guest_action(node, vmid, "stop", guest_type))
                self.proxmox.wait_task(node, upid, timeout=300)
                upids.append(upid)
            cleanup = "passed"
            return True, {
                "quorate": True,
                "nodes_online": 4,
                "canary_present": True,
                "canary_initial_status": initial,
                "marker_restored": True,
                "snapshot_deleted": True,
                "task_count": len(upids),
            }, cleanup
        finally:
            if snapshot_created:
                try:
                    upid = str(self.proxmox.delete_snapshot(node, vmid, snapshot_name, guest_type))
                    self.proxmox.wait_task(node, upid, timeout=600)
                    cleanup = "passed"
                except Exception:
                    cleanup = "failed"
            if initial != "running":
                try:
                    status = self.proxmox.guest_status(node, vmid, guest_type)
                    if status.get("status") == "running":
                        self.runner.ssh(
                            node,
                            f"pct exec {vmid} -- sh -c 'test ! -e "
                            "/var/lib/edsys-fleet-canary/marker || "
                            "unlink /var/lib/edsys-fleet-canary/marker; "
                            "rmdir /var/lib/edsys-fleet-canary 2>/dev/null || true'",
                            timeout=60,
                        )
                        upid = str(self.proxmox.guest_action(node, vmid, "stop", guest_type))
                        self.proxmox.wait_task(node, upid, timeout=300)
                except Exception:
                    cleanup = "failed"

    def _ultra(self, artifact_dir: Path) -> tuple[bool, dict[str, Any], str]:
        output = artifact_dir / "ultra-last-message.txt"
        evidence_path = artifact_dir / "ultra-evidence.json"
        workspace = Path.home() / ".local" / "state" / "edsys-fleet-benchmark" / (
            f"ultra-{os.getpid()}"
        )
        required = [
            "browser_mcp",
            "proxmox_mcp",
            "code_intelligence_mcp",
            "github_mcp",
            "cloudflare_mcp",
            "openai_docs_mcp",
            "login_shell",
            "network",
            "docker",
            "nvidia_gpu",
            "outside_project_file",
            "docx",
            "pdf",
            "spreadsheet",
            "presentation",
            "cleanup",
        ]
        prompt = f"""Run the EdSys Fleet weekly real-model capability control without changing production state.

Use these enabled paths for one safe read each: Playwright/browser MCP, Proxmox MCP cluster status, EdSys code-intelligence index status, authenticated GitHub profile, authenticated Cloudflare account list, and OpenAI Developer Docs search. Also prove a login shell, an outbound HTTPS request, a disposable Docker operation, NVIDIA GPU access, and a temporary file outside the repository.

Under the dedicated temporary directory {workspace}, generate a DOCX, PDF, spreadsheet, and presentation. Validate each artifact, including rendering the document, PDF, and presentation and opening/inspecting the spreadsheet. Remove that entire temporary directory after validation. Do not place temporary artifacts anywhere else and do not modify tracked source or production services.

Write one retained, sanitized JSON evidence file to {evidence_path}. It must be smaller than 32 KiB and have exactly this shape: {{"schema_version":1,"controls":[{{"id":"control_id","status":"passed|failed","detail":"short non-secret evidence"}}],"cleanup_passed":true|false}}. Include each of these control IDs exactly once: {", ".join(required)}. Never include credentials, tokens, auth headers, private account identifiers, environment values, raw prompts, or file contents. This evidence file is a permitted benchmark artifact and must not be deleted.

Reply exactly EDSYS_ULTRA_BENCHMARK_OK only if every control passed, the temporary directory is absent, and the evidence file is valid. Otherwise still write the evidence with the exact failed control and reply exactly EDSYS_ULTRA_BENCHMARK_FAILED."""
        command = [
            "codex", "exec", "--ephemeral", "--skip-git-repo-check",
            "--dangerously-bypass-approvals-and-sandbox", "-m", "gpt-5.6-sol",
            "-c", 'model_reasoning_effort="ultra"', "-c", 'service_tier="priority"',
            "-c", 'web_search="live"', "-C", str(Path.home() / "code" / "EdSys-Master"),
            "-o", str(output), prompt,
        ]
        result = self._command(command, timeout=int(self.contract["timeouts"]["model_seconds"]))
        last = output.read_text(encoding="utf-8").strip() if output.exists() else ""
        model_evidence: dict[str, Any] = {}
        evidence_error = ""
        try:
            if evidence_path.stat().st_size > 32768:
                raise ValueError("model evidence exceeded 32 KiB")
            value = json.loads(evidence_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict) or value.get("schema_version") != 1:
                raise ValueError("model evidence schema is invalid")
            controls = value.get("controls")
            if not isinstance(controls, list):
                raise ValueError("model evidence controls are missing")
            rows = {
                str(item.get("id")): item
                for item in controls
                if isinstance(item, dict) and item.get("id")
            }
            if len(rows) != len(controls) or set(rows) != set(required):
                raise ValueError("model evidence control inventory is invalid")
            model_evidence = sanitize_evidence(value)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            evidence_error = f"{type(exc).__name__}: {exc}"
        controls_passed = bool(model_evidence) and all(
            item.get("status") == "passed" for item in model_evidence.get("controls", [])
        )
        model_cleanup_passed = bool(model_evidence.get("cleanup_passed")) and not workspace.exists()
        passed = (
            result.returncode == 0
            and last == "EDSYS_ULTRA_BENCHMARK_OK"
            and controls_passed
            and model_cleanup_passed
            and not evidence_error
        )
        forced_cleanup = workspace.exists()
        if forced_cleanup:
            shutil.rmtree(workspace)
        output.unlink(missing_ok=True)
        cleanup_passed = not workspace.exists() and not output.exists()
        evidence = {
            "model": "gpt-5.6-sol",
            "reasoning": "ultra",
            "service_tier": "priority",
            "exact_response": last,
            "returncode": result.returncode,
            "controls_passed": controls_passed,
            "model_cleanup_passed": model_cleanup_passed,
            "forced_cleanup": forced_cleanup,
            "cleanup_passed": cleanup_passed,
            "model_evidence": model_evidence,
        }
        if evidence_error:
            evidence["evidence_error"] = evidence_error
        return passed, evidence, "passed" if cleanup_passed else "failed"
