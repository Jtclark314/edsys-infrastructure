from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shlex
import subprocess

import pytest


PATH = Path(__file__).resolve().parents[1] / "edcore-ha-api.py"
SPEC = importlib.util.spec_from_file_location("edcore_ha_api", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("argv", [
    ["call-service", "light", "turn_on", "--data", '{"entity_id":"light.test"}'],
    ["websocket", '{"type":"input_boolean/create","name":"test"}'],
    ["websocket", '{"type":"lovelace/config/save","config":{}}'],
])
def test_write_is_rejected_before_transport(argv, monkeypatch):
    monkeypatch.setattr(MODULE, "execute", lambda *args: pytest.fail("Rejected action reached transport"))
    with pytest.raises(SystemExit) as result:
        MODULE.main(argv)
    assert result.value.code == 2


@pytest.mark.parametrize("kind", ["auth", "auth/long_lived_access_token", "supervisor/api", "hassio/test"])
def test_auth_management_not_available_through_core_helper(kind):
    args = MODULE.build_parser().parse_args(["websocket", json.dumps({"type": kind}), "--execute"])
    with pytest.raises(ValueError, match="outside"):
        MODULE.prepare(args)


def test_entity_action_requires_target():
    args = MODULE.build_parser().parse_args(["call-service", "switch", "turn_off", "--execute"])
    with pytest.raises(ValueError, match="explicit"):
        MODULE.prepare(args)


def test_blanket_all_target_rejected():
    args = MODULE.build_parser().parse_args(["call-service", "switch", "turn_off", "--data", '{"entity_id":"all"}', "--execute"])
    with pytest.raises(ValueError, match="blanket"):
        MODULE.prepare(args)


def test_read_only_registry_and_template():
    parser = MODULE.build_parser()
    assert MODULE.prepare(parser.parse_args(["websocket", '{"type":"config/entity_registry/list"}']))["command"]["type"] == "config/entity_registry/list"
    assert MODULE.prepare(parser.parse_args(["template", "{{17*19}}"]))["data"] == {"template": "{{17*19}}"}


@pytest.mark.parametrize("value", ["sun.sun/../../auth", "sun.sun?token=x", "light.test;id", "ALL"])
def test_entity_path_injection_rejected(value):
    with pytest.raises(SystemExit):
        MODULE.build_parser().parse_args(["state", value])


def test_payload_only_on_stdin_and_fixed_privileged_route(monkeypatch):
    payload = {"operation": "template", "method": "POST", "path": "api/template", "data": {"template": "literal $(false) `false`"}}
    def run(argv, **kwargs):
        remote = shlex.split(argv[-1])
        assert remote[:4] == ["qm", "guest", "exec", "300"]
        assert remote[remote.index("--") + 1 : remote.index("--") + 5] == ["docker", "exec", "-i", "hassio_supervisor"]
        assert payload["data"]["template"] not in argv[-1]
        assert json.loads(kwargs["input"]) == payload
        assert argv[-2] == "pve-node3"
        return subprocess.CompletedProcess(argv, 0, json.dumps({"exited": 1, "exitcode": 0, "out-data": json.dumps({"ok": True, "result": "323"})}), "")
    monkeypatch.setattr(MODULE.subprocess, "run", run)
    assert MODULE.execute("pve-node3", payload)["result"] == "323"


@pytest.mark.parametrize("response,pattern", [
    ({"exited": 0}, "did not complete"),
    ({"exited": 1, "out-truncated": 1}, "truncated"),
    ({"exited": 1, "exitcode": 1, "out-data": '{"ok":false,"error":"Home Assistant HTTP 401"}'}, "HTTP 401"),
])
def test_ambiguous_or_failed_guest_result_is_not_success(response, pattern, monkeypatch):
    monkeypatch.setattr(MODULE.subprocess, "run", lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, json.dumps(response), ""))
    with pytest.raises(RuntimeError, match=pattern):
        MODULE.execute("pve-node3", {"operation": "inspect"})


def test_bounded_list_and_json_object_validation():
    parser = MODULE.build_parser()
    for argv in [["entities", "--limit", "1000"], ["websocket", "[]"], ["call-service", "light", "turn_on", "--data", "null"]]:
        with pytest.raises(SystemExit):
            parser.parse_args(argv)


def load_worker(monkeypatch, *, legacy_socket=True):
    import types
    constants = types.SimpleNamespace(SOCKET_CORE="/existing/core.sock") if legacy_socket else types.SimpleNamespace()
    monkeypatch.setitem(__import__("sys").modules, "supervisor.const", constants)
    monkeypatch.setitem(__import__("sys").modules, "aiohttp", types.SimpleNamespace())
    spec = importlib.util.spec_from_file_location("edcore_ha_worker", PATH.with_name("edcore-ha-remote.py"))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


def test_worker_imports_when_current_supervisor_has_no_legacy_socket_constant(monkeypatch):
    assert load_worker(monkeypatch, legacy_socket=False).SOCKET_CORE is None


def test_worker_redacts_credentials_and_camera_url_tokens(monkeypatch):
    worker = load_worker(monkeypatch)
    output = worker.redact({"access_token": "hidden", "nested": {"client_secret": "hidden", "entity_picture": "/api/camera_proxy/camera.test?token=hidden&size=small"}, "state": "recording"})
    assert "hidden" not in json.dumps(output)
    assert output["state"] == "recording"
    assert "size=small" in output["nested"]["entity_picture"]


def test_worker_reads_multiple_http_chunks_and_enforces_byte_bound(monkeypatch):
    import asyncio
    worker = load_worker(monkeypatch)
    class Content:
        async def iter_chunked(self, size):
            yield b'{"answer":'
            yield b'323}'
    class Response:
        status = 200
        headers = {"Content-Type": "application/json"}
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Session:
        def request(self, *args, **kwargs): return Response()
    assert asyncio.run(worker.request(Session(), "GET", "api/test")) == {"answer": 323}
    monkeypatch.setattr(worker, "MAX_RESPONSE", 5)
    with pytest.raises(RuntimeError, match="bounded"):
        asyncio.run(worker.request(Session(), "GET", "api/test"))


def test_worker_maps_current_core_socket_from_exact_env_and_supervisor_mount(monkeypatch):
    worker = load_worker(monkeypatch)
    monkeypatch.setattr(worker, "SOCKET_CORE", None)
    core = {"Config": {"Env": ["SUPERVISOR_TOKEN=never-return-this", "SUPERVISOR_CORE_API_SOCKET=/run/supervisor/core/core.sock"]}}
    supervisor = {"Mounts": [{"Type": "bind", "Source": "/run/supervisor", "Destination": "/run/os"}]}
    assert worker.core_socket_from_metadata(core, supervisor) == Path("/run/os/core/core.sock")
    assert core["Config"]["Env"][0] == "SUPERVISOR_TOKEN=never-return-this"


def test_worker_explicit_running_socket_takes_precedence_over_legacy_constant(monkeypatch):
    worker = load_worker(monkeypatch)
    core = {"Config": {"Env": ["SUPERVISOR_CORE_API_SOCKET=/run/supervisor/core.sock"]}}
    supervisor = {"Mounts": [{"Type": "bind", "Source": "/run/supervisor", "Destination": "/run/os"}]}
    assert worker.core_socket_from_metadata(core, supervisor) == Path("/run/os/core.sock")
    assert worker.core_socket_from_metadata({"Config": {"Env": []}}, {}) == Path("/existing/core.sock")


@pytest.mark.parametrize("value", ["", "relative/core.sock", "/run/supervisor/../core.sock", "/run/supervisor/token"])
def test_worker_rejects_invalid_explicit_socket_without_legacy_fallback(value, monkeypatch):
    worker = load_worker(monkeypatch)
    with pytest.raises(RuntimeError, match="invalid"):
        worker.core_socket_from_metadata({"Config": {"Env": ["SUPERVISOR_CORE_API_SOCKET=" + value]}}, {})


def test_worker_rejects_missing_or_ambiguous_socket_metadata(monkeypatch):
    worker = load_worker(monkeypatch)
    monkeypatch.setattr(worker, "SOCKET_CORE", None)
    with pytest.raises(RuntimeError, match="no explicit"):
        worker.core_socket_from_metadata({"Config": {"Env": ["OTHER_SUPERVISOR_CORE_API_SOCKET=/wrong/core.sock"]}}, {})
    core = {"Config": {"Env": ["SUPERVISOR_CORE_API_SOCKET=/run/supervisor/core/core.sock"]}}
    with pytest.raises(RuntimeError, match="outside"):
        worker.core_socket_from_metadata(core, {"Mounts": [{"Type": "bind", "Source": "/run/supervisor-other", "Destination": "/run/os"}]})
    core["Config"]["Env"] *= 2
    with pytest.raises(RuntimeError, match="ambiguous"):
        worker.core_socket_from_metadata(core, {})


def test_worker_uses_most_specific_existing_mount_and_rejects_conflicts(monkeypatch):
    worker = load_worker(monkeypatch)
    core = {"Config": {"Env": ["SUPERVISOR_CORE_API_SOCKET=/run/supervisor/core/core.sock"]}}
    mounts = [{"Type": "bind", "Source": "/run/supervisor", "Destination": "/run/os"},
              {"Type": "bind", "Source": "/run/supervisor/core", "Destination": "/run/core"}]
    assert worker.core_socket_from_metadata(core, {"Mounts": mounts}) == Path("/run/core/core.sock")
    mounts.append({"Type": "bind", "Source": "/run/supervisor/core", "Destination": "/run/conflict"})
    with pytest.raises(RuntimeError, match="ambiguous"):
        worker.core_socket_from_metadata(core, {"Mounts": mounts})


def test_worker_metadata_transport_is_fixed_bounded_and_has_no_credential_fallback(monkeypatch):
    import asyncio
    import types
    worker = load_worker(monkeypatch)
    calls = []
    class Session:
        def __init__(self, *, connector, timeout):
            assert connector == "/run/docker.sock"
            assert timeout == 5
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(worker, "aiohttp", types.SimpleNamespace(ClientTimeout=lambda *, total: total, UnixConnector=lambda *, path: path, ClientSession=Session))
    async def metadata(session, method, path):
        calls.append((method, path))
        if path == "containers/homeassistant/json":
            return {"Config": {"Env": ["SUPERVISOR_CORE_API_SOCKET=/run/supervisor/core/core.sock"]}}
        return {"Mounts": [{"Type": "bind", "Source": "/run/supervisor", "Destination": "/run/os"}]}
    monkeypatch.setattr(worker, "request", metadata)
    monkeypatch.setattr(worker.Path, "is_socket", lambda self: str(self) == "/run/os/core/core.sock")
    assert asyncio.run(worker.resolve_core_socket()) == Path("/run/os/core/core.sock")
    assert calls == [("GET", "containers/homeassistant/json"), ("GET", "containers/hassio_supervisor/json")]
    async def failed(*args):
        raise RuntimeError("private metadata must not escape")
    monkeypatch.setattr(worker, "request", failed)
    with pytest.raises(RuntimeError, match="metadata read failed") as result:
        asyncio.run(worker.resolve_core_socket())
    assert "private metadata" not in str(result.value)
