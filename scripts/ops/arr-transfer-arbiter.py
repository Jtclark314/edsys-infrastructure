#!/usr/bin/env python3
"""Fail-closed transfer arbitration for SABnzbd and qBittorrent.

SABnzbd has priority.  The controller never releases one downloader until the
other downloader's paused state has been positively confirmed.  Runtime job
names, URLs containing credentials, and API responses are never logged.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


MODES = ("auto", "hold", "sab-only", "qbit-only")
VERSION = 1
SAB_BOOT_SAFETY = {
    "start_paused": "1",
    "pause_on_post_processing": "1",
    # Keeping this disabled prevents normal runtime resume calls from rewriting
    # start_paused back to 0.  The arbiter owns the live pause state instead.
    "preserve_paused_state": "0",
}


class DependencyError(RuntimeError):
    """A dependency cannot currently be observed."""


class ActionError(RuntimeError):
    """A requested safety transition could not be confirmed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def log(message: str) -> None:
    print(f"{utc_now()} {message}", flush=True)


def parse_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise DependencyError(f"invalid-{field}")


def count_value(value: Any, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        raise DependencyError(f"invalid-{field}")
    if isinstance(value, (list, dict)):
        return len(value)
    if isinstance(value, (int, float)):
        return max(0, int(value))
    if isinstance(value, str):
        try:
            return max(0, int(float(value.strip() or "0")))
        except ValueError as exc:
            raise DependencyError(f"invalid-{field}") from exc
    raise DependencyError(f"invalid-{field}")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


@dataclass(frozen=True)
class Config:
    sab_url: str
    sab_config: Path
    sab_container: str
    qbit_container: str
    qbit_compose: Path
    docker_binary: str
    poll_seconds: float
    idle_grace_seconds: float
    boot_check_seconds: float
    http_timeout_seconds: float
    command_timeout_seconds: float
    verify_timeout_seconds: float
    runtime_dir: Path
    state_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        config = cls(
            sab_url=os.getenv(
                "ARR_TRANSFER_SAB_URL", "http://127.0.0.1:8085/api"
            ),
            sab_config=Path(
                os.getenv(
                    "ARR_TRANSFER_SAB_CONFIG",
                    "/srv/ssd1/docker/appdata/sabnzbd/sabnzbd.ini",
                )
            ),
            sab_container=os.getenv("ARR_TRANSFER_SAB_CONTAINER", "sabnzbd"),
            qbit_container=os.getenv("ARR_TRANSFER_QBIT_CONTAINER", "qbittorrent"),
            qbit_compose=Path(
                os.getenv(
                    "ARR_TRANSFER_QBIT_COMPOSE", "/opt/arr-vpn/docker-compose.yml"
                )
            ),
            docker_binary=os.getenv("ARR_TRANSFER_DOCKER", "/usr/bin/docker"),
            poll_seconds=float(os.getenv("ARR_TRANSFER_POLL_SECONDS", "2")),
            idle_grace_seconds=float(
                os.getenv("ARR_TRANSFER_IDLE_GRACE_SECONDS", "60")
            ),
            boot_check_seconds=float(
                os.getenv("ARR_TRANSFER_BOOT_CHECK_SECONDS", "60")
            ),
            http_timeout_seconds=float(
                os.getenv("ARR_TRANSFER_HTTP_TIMEOUT_SECONDS", "5")
            ),
            command_timeout_seconds=float(
                os.getenv("ARR_TRANSFER_COMMAND_TIMEOUT_SECONDS", "15")
            ),
            verify_timeout_seconds=float(
                os.getenv("ARR_TRANSFER_VERIFY_TIMEOUT_SECONDS", "15")
            ),
            runtime_dir=Path(
                os.getenv("ARR_TRANSFER_RUNTIME_DIR", "/run/arr-transfer-arbiter")
            ),
            state_dir=Path(
                os.getenv("ARR_TRANSFER_STATE_DIR", "/var/lib/arr-transfer-arbiter")
            ),
        )
        if config.poll_seconds < 0.5:
            raise ValueError("poll interval must be at least 0.5 seconds")
        if config.idle_grace_seconds < 0:
            raise ValueError("idle grace cannot be negative")
        if config.boot_check_seconds <= 0:
            raise ValueError("boot safety check interval must be positive")
        if min(
            config.http_timeout_seconds,
            config.command_timeout_seconds,
            config.verify_timeout_seconds,
        ) <= 0:
            raise ValueError("timeouts must be positive")
        for label, container in (
            ("SABnzbd", config.sab_container),
            ("qBittorrent", config.qbit_container),
        ):
            if not container or any(
                char
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
                for char in container
            ):
                raise ValueError(f"invalid {label} container name")
        return config

    @property
    def state_path(self) -> Path:
        return self.state_dir / "state.json"

    @property
    def status_path(self) -> Path:
        return self.runtime_dir / "status.json"


@dataclass(frozen=True)
class SabSnapshot:
    queue_paused: bool
    post_processing_paused: bool
    queue_count: int
    post_processing_count: int
    post_processing_control_confirmed: bool = True

    @property
    def has_work(self) -> bool:
        return self.queue_count > 0 or self.post_processing_count > 0

    @property
    def fully_paused(self) -> bool:
        return (
            self.queue_paused
            and self.post_processing_paused
            and self.post_processing_control_confirmed
            and self.post_processing_count == 0
        )


@dataclass(frozen=True)
class QbitSnapshot:
    status: str
    running: bool
    paused: bool
    restart_policy: str

    @property
    def active(self) -> bool:
        return self.running and not self.paused

    @property
    def quiesced(self) -> bool:
        return self.paused or self.status in {"created", "exited", "dead"}


@dataclass(frozen=True)
class StepResult:
    decision: str
    sab: SabSnapshot
    qbit: QbitSnapshot
    idle_seconds: float | None = None


def read_ini_misc(path: Path, wanted: set[str]) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise DependencyError("sab-config-unreadable") from exc
    section = ""
    values: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            continue
        if section != "misc" or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        normalized = key.strip().lower()
        if normalized in wanted:
            values[normalized] = value.strip()
    return values


class SabClient:
    def __init__(
        self,
        config: Config,
        *,
        opener: Callable[..., Any] = urlopen,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.opener = opener
        self.runner = runner
        self.sleeper = sleeper
        self._last_instance: str | None = None
        self._post_processing_instance: str | None = None
        self._post_processing_paused: bool | None = None

    def _instance_token(self) -> str:
        template = "{{.State.StartedAt}}|{{.RestartCount}}|{{.State.Running}}"
        try:
            result = self.runner(
                [
                    self.config.docker_binary,
                    "inspect",
                    "--format",
                    template,
                    self.config.sab_container,
                ],
                text=True,
                capture_output=True,
                timeout=self.config.command_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyError("docker-api-unavailable") from exc
        fields = result.stdout.strip().split("|") if result.returncode == 0 else []
        if len(fields) != 3 or fields[2].strip().lower() != "true":
            raise DependencyError("sab-container-unavailable")
        return "|".join(fields[:2])

    def _api_key(self) -> str:
        key = read_ini_misc(self.config.sab_config, {"api_key"}).get("api_key", "")
        if not key:
            raise DependencyError("sab-api-key-missing")
        return key

    def _request(self, mode: str, **parameters: Any) -> dict[str, Any]:
        query = urlencode(
            {
                "mode": mode,
                "output": "json",
                "apikey": self._api_key(),
                **parameters,
            }
        )
        request = Request(
            f"{self.config.sab_url}?{query}",
            headers={"User-Agent": "EdSys-arr-transfer-arbiter/1.0"},
        )
        try:
            with self.opener(
                request, timeout=self.config.http_timeout_seconds
            ) as response:
                payload = json.load(response)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise DependencyError("sab-api-unavailable") from exc
        if not isinstance(payload, dict):
            raise DependencyError("sab-api-invalid-response")
        return payload

    def _command(self, mode: str, **parameters: Any) -> None:
        payload = self._request(mode, **parameters)
        if payload.get("status") is False:
            raise ActionError(f"sab-{mode}-rejected")

    def snapshot(self) -> SabSnapshot:
        instance_before = self._instance_token()
        queue_payload = self._request("queue", limit=1)
        history_payload = self._request("history", limit=1)
        instance_after = self._instance_token()
        if instance_before != instance_after:
            raise DependencyError("sab-instance-changed")
        self._last_instance = instance_after
        queue = queue_payload.get("queue")
        history = history_payload.get("history")
        if not isinstance(queue, dict) or not isinstance(history, dict):
            raise DependencyError("sab-api-invalid-response")
        queue_counts = [
            count_value(queue.get("noofslots_total"), "sab-queue-count"),
            count_value(queue.get("noofslots"), "sab-queue-count"),
            count_value(queue.get("slots"), "sab-queue-count"),
        ]
        post_processing_control_confirmed = (
            self._post_processing_instance == instance_after
            and self._post_processing_paused is not None
        )
        return SabSnapshot(
            queue_paused=parse_bool(queue.get("paused"), "sab-queue-paused"),
            post_processing_paused=bool(
                post_processing_control_confirmed
                and self._post_processing_paused is True
            ),
            queue_count=max(queue_counts),
            post_processing_count=count_value(
                history.get("ppslots"), "sab-post-processing-count"
            ),
            post_processing_control_confirmed=post_processing_control_confirmed,
        )

    def _set_post_processing_state(self, paused: bool) -> None:
        instance_before = self._instance_token()
        self._command("pause_pp" if paused else "resume_pp")
        instance_after = self._instance_token()
        if instance_before != instance_after:
            self._post_processing_instance = None
            self._post_processing_paused = None
            raise DependencyError("sab-instance-changed")
        self._post_processing_instance = instance_after
        self._post_processing_paused = paused

    def _set_pause_state(
        self, paused: bool, snapshot: SabSnapshot | None = None
    ) -> SabSnapshot:
        deadline = time.monotonic() + self.config.verify_timeout_seconds
        current = snapshot
        while time.monotonic() < deadline:
            if current is None:
                current = self.snapshot()
            if (
                current.queue_paused is paused
                and current.post_processing_control_confirmed
                and current.post_processing_paused is paused
                and (not paused or current.post_processing_count == 0)
            ):
                return current
            post_processing_known_target = (
                self._post_processing_instance == self._last_instance
                and self._post_processing_paused is paused
            )
            if paused:
                if not current.queue_paused:
                    self._command("pause")
                if not post_processing_known_target:
                    self._set_post_processing_state(True)
            else:
                if not post_processing_known_target:
                    self._set_post_processing_state(False)
                if current.queue_paused:
                    self._command("resume")
            self.sleeper(0.5)
            current = None
        raise ActionError("sab-pause-state-unconfirmed")

    def pause(self, snapshot: SabSnapshot | None = None) -> SabSnapshot:
        return self._set_pause_state(True, snapshot)

    def resume(self, snapshot: SabSnapshot | None = None) -> SabSnapshot:
        return self._set_pause_state(False, snapshot)

    def configure_boot_safety(self) -> SabSnapshot:
        current = read_ini_misc(self.config.sab_config, set(SAB_BOOT_SAFETY))
        for keyword, value in SAB_BOOT_SAFETY.items():
            if current.get(keyword) != value:
                self._command(
                    "set_config", section="misc", keyword=keyword, value=value
                )
        deadline = time.monotonic() + self.config.verify_timeout_seconds
        while time.monotonic() < deadline:
            values = read_ini_misc(self.config.sab_config, set(SAB_BOOT_SAFETY))
            if all(
                values.get(key) == value
                for key, value in SAB_BOOT_SAFETY.items()
            ):
                return self.pause()
            self.sleeper(0.5)
        raise ActionError("sab-boot-safety-unconfirmed")

    def boot_safety_configured(self) -> bool:
        values = read_ini_misc(self.config.sab_config, set(SAB_BOOT_SAFETY))
        return all(
            values.get(key) == value for key, value in SAB_BOOT_SAFETY.items()
        )


class QbitClient:
    def __init__(
        self,
        config: Config,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.config = config
        self.runner = runner
        self.sleeper = sleeper

    def _run(self, arguments: list[str], timeout: float | None = None) -> str:
        try:
            result = self.runner(
                [self.config.docker_binary, *arguments],
                text=True,
                capture_output=True,
                timeout=timeout or self.config.command_timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DependencyError("docker-api-unavailable") from exc
        if result.returncode != 0:
            raise ActionError("docker-command-failed")
        return result.stdout.strip()

    def snapshot(self) -> QbitSnapshot:
        template = (
            "{{json .State.Status}}|{{json .State.Running}}|"
            "{{json .State.Paused}}|{{json .HostConfig.RestartPolicy.Name}}"
        )
        try:
            output = self._run(
                ["inspect", "--format", template, self.config.qbit_container]
            )
            fields = output.split("|")
            if len(fields) != 4:
                raise ValueError
            status, running, paused, restart_policy = [json.loads(item) for item in fields]
            if not isinstance(status, str) or not isinstance(restart_policy, str):
                raise ValueError
            if not isinstance(running, bool) or not isinstance(paused, bool):
                raise ValueError
        except ActionError as exc:
            raise DependencyError("qbit-container-unavailable") from exc
        except (ValueError, json.JSONDecodeError) as exc:
            raise DependencyError("qbit-inspect-invalid") from exc
        return QbitSnapshot(status, running, paused, restart_policy)

    def _wait(self, predicate: Callable[[QbitSnapshot], bool]) -> QbitSnapshot:
        deadline = time.monotonic() + self.config.verify_timeout_seconds
        last: QbitSnapshot | None = None
        while time.monotonic() < deadline:
            try:
                last = self.snapshot()
            except DependencyError:
                self.sleeper(0.5)
                continue
            if predicate(last):
                return last
            self.sleeper(0.5)
        raise ActionError("qbit-state-unconfirmed")

    def _best_effort_action(self, arguments: list[str], timeout: float | None = None) -> None:
        try:
            self._run(arguments, timeout=timeout)
        except (DependencyError, ActionError):
            return

    def quiesce(self) -> QbitSnapshot:
        current = self.snapshot()
        if not current.quiesced:
            self._best_effort_action(["pause", self.config.qbit_container])
            try:
                current = self._wait(lambda item: item.quiesced)
            except ActionError:
                self._best_effort_action(
                    ["stop", "--time", "15", self.config.qbit_container],
                    timeout=max(20, self.config.command_timeout_seconds),
                )
                try:
                    current = self._wait(lambda item: item.quiesced)
                except ActionError:
                    self._best_effort_action(["kill", self.config.qbit_container])
                    current = self._wait(lambda item: item.quiesced)
        if current.restart_policy != "no":
            self._run(
                ["update", "--restart=no", self.config.qbit_container]
            )
            current = self._wait(
                lambda item: item.quiesced and item.restart_policy == "no"
            )
        if not current.quiesced or current.restart_policy != "no":
            raise ActionError("qbit-quiesce-unconfirmed")
        return current

    def permit(self) -> QbitSnapshot:
        current = self.snapshot()
        if current.restart_policy != "no":
            raise ActionError("qbit-restart-policy-unsafe")
        if current.active:
            return current
        if current.running and current.paused:
            self._run(["unpause", self.config.qbit_container])
        elif current.status in {"created", "exited", "dead"}:
            self._run(
                ["start", self.config.qbit_container],
                timeout=max(60, self.config.command_timeout_seconds),
            )
        else:
            raise ActionError("qbit-state-not-permittable")
        return self._wait(lambda item: item.active and item.restart_policy == "no")


class StateStore:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.lock_path = config.runtime_dir / "state.lock"

    def _default(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "mode": "auto",
            "fault_latched": False,
            "fault_reason": None,
            "updated_at": utc_now(),
        }

    def load(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.config.state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            payload = self._default()
            atomic_write_json(self.config.state_path, payload)
        except (OSError, json.JSONDecodeError) as exc:
            raise DependencyError("state-file-invalid") from exc
        if not isinstance(payload, dict) or payload.get("version") != VERSION:
            raise DependencyError("state-file-invalid")
        if payload.get("mode") not in MODES or not isinstance(
            payload.get("fault_latched"), bool
        ):
            raise DependencyError("state-file-invalid")
        return payload

    def update(self, **changes: Any) -> dict[str, Any]:
        self.config.runtime_dir.mkdir(parents=True, exist_ok=True)
        with self.lock_path.open("a", encoding="utf-8") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            payload = self.load()
            payload.update(changes)
            payload["updated_at"] = utc_now()
            atomic_write_json(self.config.state_path, payload)
            return payload

    def set_mode(self, mode: str) -> dict[str, Any]:
        if mode not in MODES:
            raise ValueError("invalid mode")
        return self.update(mode=mode)

    def latch_fault(self, reason: str) -> dict[str, Any]:
        return self.update(
            fault_latched=True,
            fault_reason=reason,
            fault_at=utc_now(),
        )

    def reset_fault(self) -> dict[str, Any]:
        return self.update(
            fault_latched=False,
            fault_reason=None,
            fault_reset_at=utc_now(),
        )


class Controller:
    def __init__(self, config: Config, sab: SabClient, qbit: QbitClient) -> None:
        self.config = config
        self.sab = sab
        self.qbit = qbit
        self.idle_since: float | None = None

    def activate_sab(self) -> StepResult:
        qbit = self.qbit.quiesce()
        # Re-read SAB after the qBittorrent transition rather than trusting the
        # pre-decision snapshot across a handoff.
        sab = self.sab.resume()
        return StepResult("sab-active", sab, qbit)

    def activate_qbit(self, snapshot: SabSnapshot | None = None) -> StepResult:
        # A second snapshot below makes the pause proof fresh at the point
        # qBittorrent is released, even when the decision snapshot is reused.
        sab = snapshot or self.sab.snapshot()
        if not sab.fully_paused:
            self.qbit.quiesce()
            sab = self.sab.pause(sab)
        sab = self.sab.snapshot()
        if not sab.fully_paused:
            self.qbit.quiesce()
            raise ActionError("sab-pause-state-unconfirmed")
        qbit = self.qbit.permit()
        return StepResult("qbit-active", sab, qbit)

    def hold(self) -> StepResult:
        qbit = self.qbit.quiesce()
        sab = self.sab.pause()
        return StepResult("hold", sab, qbit)

    def step(self, mode: str, now: float | None = None) -> StepResult:
        if mode == "hold":
            self.idle_since = None
            return self.hold()
        if mode == "sab-only":
            self.idle_since = None
            return self.activate_sab()
        if mode == "qbit-only":
            self.idle_since = None
            return self.activate_qbit()
        if mode != "auto":
            raise ActionError("invalid-mode")

        current_time = time.monotonic() if now is None else now
        sab = self.sab.snapshot()
        if sab.has_work:
            self.idle_since = None
            return self.activate_sab()
        if self.idle_since is None:
            self.idle_since = current_time
        idle_seconds = max(0.0, current_time - self.idle_since)
        if idle_seconds < self.config.idle_grace_seconds:
            result = self.activate_sab()
            return StepResult(
                "sab-idle-grace", result.sab, result.qbit, idle_seconds
            )
        result = self.activate_qbit(sab)
        return StepResult("qbit-active", result.sab, result.qbit, idle_seconds)


def sd_notify(message: str) -> None:
    address = os.getenv("NOTIFY_SOCKET")
    if not address:
        return
    if address.startswith("@"):
        address = "\0" + address[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as client:
            client.connect(address)
            client.sendall(message.encode("utf-8"))
    except OSError:
        return


def status_payload(
    state: dict[str, Any],
    *,
    service_state: str,
    result: StepResult | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": VERSION,
        "timestamp": utc_now(),
        "service_state": service_state,
        "mode": state.get("mode"),
        "fault_latched": state.get("fault_latched", False),
        "fault_reason": state.get("fault_reason"),
        "healthy": service_state not in {"degraded", "fault-latched"}
        and not state.get("fault_latched", False),
    }
    if detail:
        payload["detail"] = detail
    if result:
        payload["decision"] = result.decision
        payload["idle_seconds"] = (
            round(result.idle_seconds, 1)
            if result.idle_seconds is not None
            else None
        )
        payload["sab"] = asdict(result.sab) | {"has_work": result.sab.has_work}
        payload["qbittorrent"] = asdict(result.qbit) | {
            "active": result.qbit.active,
            "quiesced": result.qbit.quiesced,
        }
    return payload


def write_runtime_status(config: Config, payload: dict[str, Any]) -> None:
    atomic_write_json(config.status_path, payload)


def acquire_daemon_lock(config: Config) -> Any:
    config.runtime_dir.mkdir(parents=True, exist_ok=True)
    handle = (config.runtime_dir / "daemon.lock").open("a", encoding="utf-8")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SystemExit("another arbiter process is already running") from exc
    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()))
    handle.flush()
    return handle


def run_daemon(config: Config) -> int:
    _lock = acquire_daemon_lock(config)
    store = StateStore(config)
    sab = SabClient(config)
    qbit = QbitClient(config)
    controller = Controller(config, sab, qbit)
    stop_event = threading.Event()
    last_log_key: tuple[Any, ...] | None = None
    next_boot_safety_check = 0.0

    def stop_handler(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    state = store.load()

    try:
        try:
            initial_qbit = qbit.quiesce()
            initial_sab = sab.configure_boot_safety()
            initial = StepResult("hold", initial_sab, initial_qbit)
            payload = status_payload(state, service_state="hold", result=initial)
        except DependencyError as exc:
            try:
                qbit.quiesce()
            except (DependencyError, ActionError):
                state = store.latch_fault("initial-failsafe-unconfirmed")
            payload = status_payload(
                state, service_state="degraded", detail=str(exc)
            )
        except ActionError as exc:
            state = store.latch_fault(str(exc))
            payload = status_payload(
                state, service_state="fault-latched", detail=str(exc)
            )
        write_runtime_status(config, payload)
        sd_notify("READY=1\nSTATUS=fail-closed controller active")
        log("controller ready; initial fail-closed reconciliation completed")

        while not stop_event.is_set():
            result: StepResult | None = None
            detail: str | None = None
            try:
                state = store.load()
                current_time = time.monotonic()
                if current_time >= next_boot_safety_check:
                    if not sab.boot_safety_configured():
                        qbit.quiesce()
                        sab.configure_boot_safety()
                    next_boot_safety_check = (
                        current_time + config.boot_check_seconds
                    )
                if state["fault_latched"]:
                    try:
                        result = controller.hold()
                        service_state = "fault-latched"
                    except (DependencyError, ActionError) as exc:
                        service_state = "fault-latched"
                        detail = str(exc)
                else:
                    result = controller.step(state["mode"])
                    service_state = result.decision
            except DependencyError as exc:
                detail = str(exc)
                service_state = "degraded"
                try:
                    qbit.quiesce()
                except (DependencyError, ActionError):
                    state = store.latch_fault("dependency-failsafe-unconfirmed")
                    service_state = "fault-latched"
            except ActionError as exc:
                detail = str(exc)
                state = store.latch_fault(str(exc))
                service_state = "fault-latched"
                try:
                    result = controller.hold()
                except (DependencyError, ActionError):
                    result = None

            payload = status_payload(
                state, service_state=service_state, result=result, detail=detail
            )
            write_runtime_status(config, payload)
            log_key = (
                payload["service_state"],
                payload.get("mode"),
                payload.get("fault_latched"),
                payload.get("fault_reason"),
                payload.get("detail"),
            )
            if log_key != last_log_key:
                log(
                    "state="
                    f"{payload['service_state']} mode={payload['mode']} "
                    f"fault={str(payload['fault_latched']).lower()}"
                )
                last_log_key = log_key
            sd_notify(
                "WATCHDOG=1\n"
                f"STATUS={payload['service_state']} mode={payload['mode']}"
            )
            stop_event.wait(config.poll_seconds)
    except Exception as exc:
        log(f"fatal internal controller error: {type(exc).__name__}")
        try:
            store.latch_fault("internal-controller-error")
        except Exception:
            pass
        raise
    finally:
        sd_notify("STOPPING=1\nSTATUS=applying fail-safe stop posture")
        try:
            controller.hold()
            state = store.load()
            write_runtime_status(
                config,
                status_payload(state, service_state="stopped-fail-safe"),
            )
            log("controller stopped with both clients held")
        except (DependencyError, ActionError):
            log("controller stop fail-safe could not be fully confirmed")
    return 0


def fail_safe(config: Config) -> int:
    controller = Controller(config, SabClient(config), QbitClient(config))
    try:
        result = controller.hold()
    except (DependencyError, ActionError) as exc:
        print(f"fail-safe not confirmed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "result": "fail-safe-confirmed",
                "sab_paused": result.sab.fully_paused,
                "qbittorrent_quiesced": result.qbit.quiesced,
                "qbittorrent_restart_policy": result.qbit.restart_policy,
            },
            sort_keys=True,
        )
    )
    return 0


def configure_compose_policy(path: Path) -> bool:
    try:
        original = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ActionError("qbit-compose-unreadable") from exc
    lines = original.splitlines(keepends=True)
    start: int | None = None
    end = len(lines)
    for index, line in enumerate(lines):
        if line.rstrip("\r\n") == "  qbittorrent:":
            if start is not None:
                raise ActionError("qbit-compose-service-ambiguous")
            start = index
            continue
        if start is not None and index > start:
            stripped = line.rstrip("\r\n")
            if stripped.startswith("  ") and not stripped.startswith("    "):
                end = index
                break
    if start is None:
        raise ActionError("qbit-compose-service-missing")
    matches = [
        index
        for index in range(start + 1, end)
        if lines[index].lstrip().startswith("restart:")
        and len(lines[index]) - len(lines[index].lstrip()) == 4
    ]
    if len(matches) != 1:
        raise ActionError("qbit-compose-restart-ambiguous")
    index = matches[0]
    newline = "\r\n" if lines[index].endswith("\r\n") else "\n"
    if lines[index].rstrip("\r\n") == '    restart: "no"':
        return False
    lines[index] = f'    restart: "no"{newline}'
    updated = "".join(lines)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    stat = path.stat()
    try:
        tmp.write_text(updated, encoding="utf-8")
        os.chmod(tmp, stat.st_mode & 0o7777)
        os.chown(tmp, stat.st_uid, stat.st_gid)
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return True


def preflight(config: Config) -> tuple[bool, dict[str, Any]]:
    checks: dict[str, Any] = {
        "sab_config_present": config.sab_config.is_file(),
        "qbit_compose_present": config.qbit_compose.is_file(),
        "docker_binary_present": bool(
            Path(config.docker_binary).is_file() or shutil.which(config.docker_binary)
        ),
    }
    try:
        sab = SabClient(config).snapshot()
        checks["sab_api_reachable"] = True
        checks["sab_queue_paused"] = sab.queue_paused
        checks["sab_post_processing_count"] = sab.post_processing_count
        # The post-processor proof is deliberately daemon-local because it is
        # tied to the synchronous command acknowledgement and container
        # instance observed by that process.  Use `status` for the live proof;
        # a fresh read-only preflight must not report a misleading false value.
        checks["sab_post_processing_proof"] = "see-runtime-status"
        checks["sab_boot_safety_configured"] = (
            SabClient(config).boot_safety_configured()
        )
    except DependencyError:
        checks["sab_api_reachable"] = False
    try:
        qbit = QbitClient(config).snapshot()
        checks["qbit_container_present"] = True
        checks["qbit_restart_policy"] = qbit.restart_policy
        checks["qbit_quiesced"] = qbit.quiesced
    except (DependencyError, ActionError):
        checks["qbit_container_present"] = False
    required = (
        checks.get("sab_config_present"),
        checks.get("qbit_compose_present"),
        checks.get("docker_binary_present"),
        checks.get("sab_api_reachable"),
        checks.get("qbit_container_present"),
        checks.get("sab_boot_safety_configured"),
        checks.get("qbit_restart_policy") == "no",
    )
    return all(required), checks


def print_status(config: Config, check: bool) -> int:
    try:
        payload = json.loads(config.status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {
            "healthy": False,
            "service_state": "status-unavailable",
            "timestamp": utc_now(),
        }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not check or payload.get("healthy") is True else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="run the long-lived controller")
    status = subparsers.add_parser("status", help="print sanitized runtime status")
    status.add_argument("--check", action="store_true", help="exit nonzero if degraded")
    set_mode = subparsers.add_parser("set-mode", help="set the persistent operating mode")
    set_mode.add_argument("mode", choices=MODES)
    subparsers.add_parser("reset-fault", help="clear a latched safety fault")
    subparsers.add_parser("fail-safe", help="pause and confirm both clients")
    subparsers.add_parser("preflight", help="run read-only dependency checks")
    subparsers.add_parser(
        "configure-boot-safety",
        help="persist SAB boot pause settings and hold both clients",
    )
    compose = subparsers.add_parser(
        "configure-compose", help="set the qBittorrent Compose restart policy to no"
    )
    compose.add_argument("--path", type=Path)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = Config.from_env()
    if args.command == "run":
        return run_daemon(config)
    if args.command == "status":
        return print_status(config, args.check)
    if args.command == "set-mode":
        payload = StateStore(config).set_mode(args.mode)
        print(json.dumps({"mode": payload["mode"], "updated_at": payload["updated_at"]}))
        return 0
    if args.command == "reset-fault":
        payload = StateStore(config).reset_fault()
        print(
            json.dumps(
                {
                    "fault_latched": payload["fault_latched"],
                    "updated_at": payload["updated_at"],
                }
            )
        )
        return 0
    if args.command == "fail-safe":
        return fail_safe(config)
    if args.command == "preflight":
        ok, checks = preflight(config)
        print(json.dumps({"ok": ok, "checks": checks}, indent=2, sort_keys=True))
        return 0 if ok else 1
    if args.command == "configure-boot-safety":
        qbit = QbitClient(config).quiesce()
        sab = SabClient(config).configure_boot_safety()
        print(
            json.dumps(
                {
                    "sab_boot_safety": "confirmed",
                    "sab_paused": sab.fully_paused,
                    "qbittorrent_quiesced": qbit.quiesced,
                    "qbittorrent_restart_policy": qbit.restart_policy,
                },
                sort_keys=True,
            )
        )
        return 0
    if args.command == "configure-compose":
        path = args.path or config.qbit_compose
        changed = configure_compose_policy(path)
        print(json.dumps({"changed": changed, "path": str(path)}))
        return 0
    raise AssertionError("unreachable")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DependencyError, ActionError, ValueError) as exc:
        print(f"arr-transfer-arbiter: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
