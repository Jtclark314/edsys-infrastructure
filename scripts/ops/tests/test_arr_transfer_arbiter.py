import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import pytest


MODULE_PATH = Path(__file__).parents[1] / "arr-transfer-arbiter.py"
SPEC = importlib.util.spec_from_file_location("arr_transfer_arbiter", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def config(tmp_path: Path, idle_grace: float = 60) -> MODULE.Config:
    return MODULE.Config(
        sab_url="http://127.0.0.1:8085/api",
        sab_config=tmp_path / "sabnzbd.ini",
        sab_container="sabnzbd",
        qbit_container="qbittorrent",
        qbit_compose=tmp_path / "docker-compose.yml",
        docker_binary="/usr/bin/docker",
        poll_seconds=2,
        idle_grace_seconds=idle_grace,
        boot_check_seconds=60,
        http_timeout_seconds=1,
        command_timeout_seconds=1,
        verify_timeout_seconds=1,
        runtime_dir=tmp_path / "run",
        state_dir=tmp_path / "state",
    )


class FakeSab:
    def __init__(self, snapshot, events, fail_pause=False):
        self.current = snapshot
        self.events = events
        self.fail_pause = fail_pause

    def snapshot(self):
        self.events.append("sab.snapshot")
        return self.current

    def pause(self, _snapshot=None):
        self.events.append("sab.pause")
        if self.fail_pause:
            raise MODULE.ActionError("sab-pause-state-unconfirmed")
        self.current = MODULE.SabSnapshot(
            True,
            True,
            self.current.queue_count,
            self.current.post_processing_count,
        )
        return self.current

    def resume(self, _snapshot=None):
        self.events.append("sab.resume")
        self.current = MODULE.SabSnapshot(
            False,
            False,
            self.current.queue_count,
            self.current.post_processing_count,
        )
        return self.current


class FakeQbit:
    def __init__(self, events, fail_quiesce=False):
        self.events = events
        self.fail_quiesce = fail_quiesce
        self.current = MODULE.QbitSnapshot("running", True, False, "no")

    def quiesce(self):
        self.events.append("qbit.quiesce")
        if self.fail_quiesce:
            raise MODULE.ActionError("qbit-quiesce-unconfirmed")
        self.current = MODULE.QbitSnapshot("running", True, True, "no")
        return self.current

    def permit(self):
        self.events.append("qbit.permit")
        self.current = MODULE.QbitSnapshot("running", True, False, "no")
        return self.current


def test_sab_work_quiesces_qbit_before_resuming_sab(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(True, True, 1, 0), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path), sab, qbit)

    result = controller.step("auto", now=100)

    assert result.decision == "sab-active"
    assert events == ["sab.snapshot", "qbit.quiesce", "sab.resume"]
    assert result.qbit.quiesced
    assert not result.sab.fully_paused


def test_idle_grace_keeps_qbit_quiesced_and_sab_available(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(False, False, 0, 0), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path, idle_grace=60), sab, qbit)

    first = controller.step("auto", now=100)
    events.clear()
    second = controller.step("auto", now=159)

    assert first.decision == "sab-idle-grace"
    assert second.decision == "sab-idle-grace"
    assert events == ["sab.snapshot", "qbit.quiesce", "sab.resume"]
    assert second.qbit.quiesced


def test_idle_handoff_orders_quiesce_pause_then_permit(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(False, False, 0, 0), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path, idle_grace=60), sab, qbit)
    controller.idle_since = 100

    result = controller.step("auto", now=160)

    assert result.decision == "qbit-active"
    assert events == [
        "sab.snapshot",
        "qbit.quiesce",
        "sab.pause",
        "sab.snapshot",
        "qbit.permit",
    ]
    assert result.sab.fully_paused
    assert result.qbit.active


def test_sab_pause_failure_never_permits_qbit(tmp_path):
    events = []
    sab = FakeSab(
        MODULE.SabSnapshot(False, False, 0, 0), events, fail_pause=True
    )
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path, idle_grace=0), sab, qbit)

    with pytest.raises(MODULE.ActionError):
        controller.step("auto", now=100)

    assert events == [
        "sab.snapshot",
        "qbit.quiesce",
        "sab.pause",
    ]
    assert "qbit.permit" not in events


def test_qbit_quiesce_failure_never_resumes_sab(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(True, True, 1, 0), events)
    qbit = FakeQbit(events, fail_quiesce=True)
    controller = MODULE.Controller(config(tmp_path), sab, qbit)

    with pytest.raises(MODULE.ActionError):
        controller.step("auto", now=100)

    assert events == ["sab.snapshot", "qbit.quiesce"]
    assert "sab.resume" not in events


def test_hold_quiesces_qbit_before_pausing_sab(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(False, False, 0, 0), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path), sab, qbit)

    result = controller.step("hold")

    assert result.decision == "hold"
    assert events == ["qbit.quiesce", "sab.pause"]


def test_qbit_only_does_not_repause_qbit_when_sab_already_confirmed_paused(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(True, True, 0, 0), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path), sab, qbit)

    result = controller.step("qbit-only")

    assert result.decision == "qbit-active"
    assert events == ["sab.snapshot", "sab.snapshot", "qbit.permit"]


def test_qbit_is_not_permitted_if_final_sab_pause_proof_changes(tmp_path):
    events = []

    class ChangedSab(FakeSab):
        def snapshot(self):
            self.events.append("sab.snapshot")
            if self.events.count("sab.snapshot") == 1:
                return MODULE.SabSnapshot(True, True, 0, 0)
            return MODULE.SabSnapshot(False, False, 0, 0)

    sab = ChangedSab(MODULE.SabSnapshot(True, True, 0, 0), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path), sab, qbit)

    with pytest.raises(MODULE.ActionError):
        controller.step("qbit-only")

    assert "qbit.permit" not in events
    assert events == ["sab.snapshot", "sab.snapshot", "qbit.quiesce"]


def test_qbit_only_refuses_while_post_processing_work_remains(tmp_path):
    events = []
    sab = FakeSab(MODULE.SabSnapshot(False, False, 0, 1), events)
    qbit = FakeQbit(events)
    controller = MODULE.Controller(config(tmp_path), sab, qbit)

    with pytest.raises(MODULE.ActionError):
        controller.step("qbit-only")

    assert "qbit.permit" not in events
    assert events == [
        "sab.snapshot",
        "qbit.quiesce",
        "sab.pause",
        "sab.snapshot",
        "qbit.quiesce",
    ]


def test_compose_patch_changes_only_qbittorrent_and_is_idempotent(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n"
        "  gluetun:\n"
        "    restart: unless-stopped\n"
        "  qbittorrent:\n"
        "    image: example/qbit\n"
        "    restart: unless-stopped\n"
        "  prowlarr:\n"
        "    restart: unless-stopped\n",
        encoding="utf-8",
    )

    assert MODULE.configure_compose_policy(compose) is True
    assert MODULE.configure_compose_policy(compose) is False
    output = compose.read_text(encoding="utf-8")
    assert output.count('restart: "no"') == 1
    assert output.count("restart: unless-stopped") == 2


def test_compose_patch_refuses_missing_or_ambiguous_restart(tmp_path):
    compose = tmp_path / "docker-compose.yml"
    compose.write_text(
        "services:\n  qbittorrent:\n    image: example/qbit\n",
        encoding="utf-8",
    )
    with pytest.raises(MODULE.ActionError):
        MODULE.configure_compose_policy(compose)


def test_ini_reader_returns_only_requested_misc_values(tmp_path):
    ini = tmp_path / "sabnzbd.ini"
    ini.write_text(
        "legacy preamble\n"
        "[misc]\n"
        "api_key = private-value\n"
        "start_paused = 1\n"
        "[servers]\n"
        "api_key = wrong-section\n",
        encoding="utf-8",
    )
    values = MODULE.read_ini_misc(ini, {"start_paused"})
    assert values == {"start_paused": "1"}
    assert "private-value" not in repr(values)


def test_runtime_status_contains_no_job_names_or_api_material(tmp_path):
    state = {
        "mode": "auto",
        "fault_latched": False,
        "fault_reason": None,
    }
    result = MODULE.StepResult(
        "sab-active",
        MODULE.SabSnapshot(False, False, 2, 1),
        MODULE.QbitSnapshot("running", True, True, "no"),
    )
    payload = MODULE.status_payload(state, service_state="sab-active", result=result)
    rendered = str(payload).lower()
    assert "apikey" not in rendered
    assert "api_key" not in rendered
    assert "slot" not in rendered
    assert payload["sab"]["queue_count"] == 2


def test_state_store_latches_fault_until_explicit_reset(tmp_path):
    store = MODULE.StateStore(config(tmp_path))
    store.set_mode("auto")
    fault = store.latch_fault("sab-pause-state-unconfirmed")
    assert fault["fault_latched"] is True
    assert store.load()["fault_latched"] is True
    reset = store.reset_fault()
    assert reset["fault_latched"] is False
    assert reset["mode"] == "auto"


def test_boot_safety_keeps_start_paused_independent_of_runtime_resume():
    assert MODULE.SAB_BOOT_SAFETY == {
        "start_paused": "1",
        "pause_on_post_processing": "1",
        "preserve_paused_state": "0",
    }


def test_sab_pause_proof_uses_command_ack_instance_and_zero_pp_work(tmp_path):
    cfg = config(tmp_path)
    cfg.sab_config.write_text("[misc]\napi_key = private-value\n", encoding="utf-8")
    modes = []
    state = {"queue_paused": False, "ppslots": 0}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def opener(request, timeout):
        assert timeout == cfg.http_timeout_seconds
        mode = parse_qs(urlparse(request.full_url).query)["mode"][0]
        modes.append(mode)
        if mode == "queue":
            payload = {
                "queue": {
                    "paused": state["queue_paused"],
                    "noofslots": 0,
                    "noofslots_total": 0,
                    "slots": [],
                }
            }
        elif mode == "history":
            payload = {"history": {"ppslots": state["ppslots"]}}
        elif mode == "pause":
            state["queue_paused"] = True
            payload = {"status": True}
        elif mode == "resume":
            state["queue_paused"] = False
            payload = {"status": True}
        elif mode in {"pause_pp", "resume_pp"}:
            payload = {"status": True}
        else:
            raise AssertionError(f"unexpected API mode {mode}")
        return Response(json.dumps(payload).encode())

    def runner(_arguments, **_kwargs):
        return subprocess.CompletedProcess([], 0, "started-at|0|true\n", "")

    client = MODULE.SabClient(cfg, opener=opener, runner=runner, sleeper=lambda _: None)
    paused = client.pause()
    assert paused.fully_paused
    assert paused.post_processing_control_confirmed
    assert "status" not in modes
    assert modes.index("pause") < modes.index("pause_pp")

    modes.clear()
    resumed = client.resume()
    assert resumed.queue_paused is False
    assert resumed.post_processing_paused is False
    assert resumed.post_processing_control_confirmed
    assert modes.index("resume_pp") < modes.index("resume")

    active_pp = MODULE.SabSnapshot(True, True, 0, 1, True)
    assert not active_pp.fully_paused


def test_sab_resume_accepts_automatic_queue_hold_during_post_processing(tmp_path):
    cfg = config(tmp_path)
    cfg.sab_config.write_text("[misc]\napi_key = private-value\n", encoding="utf-8")
    modes = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def opener(request, timeout):
        assert timeout == cfg.http_timeout_seconds
        mode = parse_qs(urlparse(request.full_url).query)["mode"][0]
        modes.append(mode)
        if mode == "queue":
            payload = {
                "queue": {
                    # pause_on_post_processing intentionally holds downloads.
                    "paused": True,
                    "noofslots": 7,
                    "noofslots_total": 7,
                    "slots": [],
                }
            }
        elif mode == "history":
            payload = {"history": {"ppslots": 1}}
        elif mode == "resume_pp":
            payload = {"status": True}
        elif mode == "resume":
            raise AssertionError("must not fight SAB's post-processing queue hold")
        else:
            raise AssertionError(f"unexpected API mode {mode}")
        return Response(json.dumps(payload).encode())

    def runner(_arguments, **_kwargs):
        return subprocess.CompletedProcess([], 0, "started-at|0|true\n", "")

    client = MODULE.SabClient(cfg, opener=opener, runner=runner, sleeper=lambda _: None)
    resumed = client.resume()

    assert resumed.queue_paused is True
    assert resumed.post_processing_count == 1
    assert resumed.post_processing_paused is False
    assert resumed.post_processing_control_confirmed
    assert "resume_pp" in modes
    assert "resume" not in modes
