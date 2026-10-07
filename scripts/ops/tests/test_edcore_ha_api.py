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


def load_worker(monkeypatch):
    import types
    monkeypatch.setitem(__import__("sys").modules, "supervisor.const", types.SimpleNamespace(SOCKET_CORE="/existing/core.sock"))
    monkeypatch.setitem(__import__("sys").modules, "aiohttp", types.SimpleNamespace())
    spec = importlib.util.spec_from_file_location("edcore_ha_worker", PATH.with_name("edcore-ha-remote.py"))
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    return worker


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
