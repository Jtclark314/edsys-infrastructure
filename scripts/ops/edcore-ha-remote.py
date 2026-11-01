"""Bounded HA API worker, executed in the existing Supervisor container.

The transport is Supervisor's protected Core socket, never an exported token.
This file does not run a listener or change any authentication settings.
"""
from __future__ import annotations

import asyncio
import collections
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

import aiohttp

try:
    # Supervisor releases before the per-Core runtime directory used this path.
    from supervisor.const import SOCKET_CORE
except ImportError:
    SOCKET_CORE = None


MAX_RESPONSE = 2 * 1024 * 1024
SECRET_KEYS = {"access_token", "refresh_token", "token", "password", "api_key", "secret", "credentials"}


def core_socket_from_metadata(core, supervisor):
    """Map Core's explicit socket setting into Supervisor's existing bind mount.

    Read only the selected environment name; never return container metadata or
    use a token when the socket is absent. Old releases retain their own known
    constant only when Core has no explicit socket setting.
    """
    values = [item.partition("=")[2] for item in core.get("Config", {}).get("Env", [])
              if isinstance(item, str) and item.partition("=")[0] == "SUPERVISOR_CORE_API_SOCKET"]
    if not values:
        if SOCKET_CORE is not None:
            return Path(SOCKET_CORE)
        raise RuntimeError("Core has no explicit Supervisor socket; no alternate credentials attempted")
    if len(values) != 1:
        raise RuntimeError("Core socket metadata is ambiguous")
    socket = PurePosixPath(values[0])
    if not socket.is_absolute() or ".." in socket.parts or socket.name != "core.sock":
        raise RuntimeError("Core socket metadata is invalid")
    candidates = []
    for mount in supervisor.get("Mounts", []):
        if mount.get("Type") != "bind":
            continue
        source = PurePosixPath(mount.get("Source", ""))
        destination = PurePosixPath(mount.get("Destination", ""))
        if (not source.is_absolute() or not destination.is_absolute()
                or ".." in source.parts or ".." in destination.parts):
            continue
        if socket.is_relative_to(source):
            candidates.append((len(source.parts), destination / socket.relative_to(source)))
    if not candidates:
        raise RuntimeError("Core socket is outside Supervisor's existing bind mounts")
    longest = max(length for length, _ in candidates)
    matches = {path for length, path in candidates if length == longest}
    if len(matches) != 1:
        raise RuntimeError("Supervisor socket mount metadata is ambiguous")
    return Path(matches.pop())


async def resolve_core_socket():
    """Discover only existing socket metadata through the local Docker socket."""
    timeout = aiohttp.ClientTimeout(total=5)
    async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path="/run/docker.sock"), timeout=timeout) as session:
        try:
            core = await request(session, "GET", "containers/homeassistant/json")
            supervisor = await request(session, "GET", "containers/hassio_supervisor/json")
        except Exception as exc:
            raise RuntimeError("Existing Docker socket metadata read failed; no alternate credentials attempted") from exc
    if not isinstance(core, dict) or not isinstance(supervisor, dict):
        raise RuntimeError("Existing Docker socket metadata is invalid")
    socket = core_socket_from_metadata(core, supervisor)
    if not socket.is_socket():
        raise RuntimeError("Existing Supervisor/Core socket is unavailable; no alternate credentials attempted")
    return socket


def redact(value):
    if isinstance(value, dict):
        return {k: "[redacted]" if k.lower() in SECRET_KEYS or k.lower().endswith("_token") or any(part in k.lower() for part in ("password", "secret", "credential")) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"([?&](?:token|access_token|api_key)=)[^&#\s]+", r"\1[redacted]", value, flags=re.I)
    return value


async def request(session, method, path, data=None, *, binary=False):
    async with session.request(method, "http://localhost/" + path, json=data, allow_redirects=False) as response:
        chunks = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            chunks.extend(chunk)
            if len(chunks) > MAX_RESPONSE:
                raise RuntimeError("Home Assistant response exceeded the bounded read limit")
        raw = bytes(chunks)
        if response.status >= 400:
            # Do not echo a response that may include user data or credentials.
            raise RuntimeError(f"Home Assistant HTTP {response.status} for {method} {path.split('?')[0]}")
        if binary:
            return {"http_status": response.status, "content_type": response.headers.get("Content-Type"), "bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        if "json" in response.headers.get("Content-Type", ""):
            return json.loads(raw)
        return raw.decode("utf-8")


async def websocket(session, command):
    async with session.ws_connect("http://localhost/api/websocket", max_msg_size=MAX_RESPONSE, receive_timeout=20) as ws:
        hello = await ws.receive_json()
        if hello.get("type") != "auth_ok":
            raise RuntimeError("Supervisor/Core socket did not authenticate; no alternate credentials attempted")
        await ws.send_json({**command, "id": 1})
        result = await ws.receive_json()
        if result.get("id") != 1 or not result.get("success"):
            error = result.get("error", {})
            raise RuntimeError("Home Assistant WebSocket rejected command: " + str(error.get("code", "unknown_error")))
        return result.get("result")


async def run(payload):
    socket = await resolve_core_socket()
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(socket)), timeout=timeout) as session:
        operation = payload["operation"]
        if operation == "inspect":
            config = await request(session, "GET", "api/config")
            states = await request(session, "GET", "api/states")
            services = await request(session, "GET", "api/services")
            result = {"core_version": config.get("version"), "entity_count": len(states), "domains": dict(collections.Counter(s["entity_id"].split(".")[0] for s in states)), "unavailable_count": sum(s["state"] == "unavailable" for s in states), "service_domains": len(services), "service_count": sum(len(s["services"]) for s in services), "transport": "existing privileged Supervisor/Core Unix socket"}
        elif operation == "entities":
            states = await request(session, "GET", "api/states")
            selected = [s for s in states if (not payload.get("domain") or s["entity_id"].startswith(payload["domain"] + ".")) and (not payload.get("filter") or payload["filter"].lower() in (s["entity_id"] + " " + str(s["attributes"].get("friendly_name", ""))).lower())]
            result = {"matching_count": len(selected), "entities": [{"entity_id": s["entity_id"], "state": s["state"], "name": s["attributes"].get("friendly_name"), "last_updated": s["last_updated"]} for s in selected[:payload["limit"]]], "limited": len(selected) > payload["limit"]}
        elif operation == "services":
            data = await request(session, "GET", "api/services")
            result = [{"domain": s["domain"], "services": sorted(s["services"])} for s in data if not payload.get("domain") or s["domain"] == payload["domain"]]
        elif operation == "websocket":
            result = await websocket(session, payload["command"])
            if isinstance(result, list):
                result = {"matching_count": len(result), "items": result[:payload["limit"]], "limited": len(result) > payload["limit"]}
        else:
            result = await request(session, payload["method"], payload["path"], payload.get("data"), binary=operation == "camera")
        return {"ok": True, "operation": operation, "result": redact(result)}


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        print(json.dumps(asyncio.run(run(payload)), ensure_ascii=True))
    except Exception as exc:
        # No raw traceback, credentials, private API response or session state.
        print(json.dumps({"ok": False, "error": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}))
        sys.exit(1)
