#!/usr/bin/env python3
"""Inspect and operate HA through existing privileged EdCore access.

Use directly or through ``edcore-control ha api``. Writes require --execute;
that flag records intent and does not replace the owner's action authorization.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess
import sys


READ_WS_TYPES = {"get_config", "get_states", "get_services", "config/entity_registry/list", "config/device_registry/list", "config/area_registry/list", "config_entries/get", "lovelace/config", "lovelace/dashboards/list", "input_boolean/list", "input_text/list"}
ENTITY = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z0-9_]+$")
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]*$")
ENTITY_SERVICE_DOMAINS = {"homeassistant", "light", "switch", "cover", "climate", "lock", "fan", "vacuum", "siren", "media_player", "input_boolean", "input_text"}


def json_object(value):
    try:
        data = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError("Expected a JSON object") from exc
    if not isinstance(data, dict):
        raise argparse.ArgumentTypeError("Expected a JSON object")
    return data


def identifier(value):
    if not IDENTIFIER.fullmatch(value):
        raise argparse.ArgumentTypeError("Expected a lowercase HA domain/service identifier")
    return value


def entity_id(value):
    if not ENTITY.fullmatch(value):
        raise argparse.ArgumentTypeError("Expected an explicit domain.entity_id")
    return value


def bounded_limit(value):
    number = int(value)
    if not 1 <= number <= 50:
        raise argparse.ArgumentTypeError("Limit must be 1..50; narrow the query for larger inventories")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="pve-node3", help="Existing SSH alias; no new connection or credentials")
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("inspect", help="Summarized live Core, entities and action inventory")
    entities = sub.add_parser("entities", help="Bounded live entity state listing")
    entities.add_argument("--domain", type=identifier)
    entities.add_argument("--filter")
    entities.add_argument("--limit", type=bounded_limit, default=20)
    state = sub.add_parser("state")
    state.add_argument("entity_id", type=entity_id)
    camera = sub.add_parser("camera", help="Read and discard one camera image; return byte digest only")
    camera.add_argument("entity_id", type=entity_id)
    services = sub.add_parser("services", help="List available action names")
    services.add_argument("--domain", type=identifier)
    template = sub.add_parser("template", help="Read-only template evaluation")
    template.add_argument("template")
    call = sub.add_parser("call-service", help="Execute an explicitly authorized HA action")
    call.add_argument("domain", type=identifier)
    call.add_argument("service", type=identifier)
    call.add_argument("--data", type=json_object, default={})
    call.add_argument("--execute", action="store_true")
    call.add_argument("--return-response", action="store_true")
    ws = sub.add_parser("websocket", help="Registry/dashboard reads or explicitly authorized UI API writes")
    ws.add_argument("command", type=json_object)
    ws.add_argument("--limit", type=bounded_limit, default=20)
    ws.add_argument("--execute", action="store_true")
    return parser


def prepare(args):
    payload = {"operation": args.operation}
    if args.operation in {"entities", "services"}:
        payload["domain"] = args.domain
    if args.operation == "entities":
        payload.update(filter=args.filter, limit=args.limit)
    elif args.operation in {"state", "camera"}:
        if args.operation == "camera" and not args.entity_id.startswith("camera."):
            raise ValueError("Camera read requires a camera entity")
        payload.update(method="GET", path=("api/states/" if args.operation == "state" else "api/camera_proxy/") + args.entity_id)
    elif args.operation == "template":
        payload.update(method="POST", path="api/template", data={"template": args.template})
    elif args.operation == "call-service":
        if not args.execute:
            raise ValueError("Action not sent: call-service requires --execute and prior owner authorization")
        if args.domain in ENTITY_SERVICE_DOMAINS and not any(args.data.get(k) for k in ("entity_id", "device_id", "area_id", "floor_id")):
            raise ValueError("Action not sent: supply an explicit entity/device/area/floor target")
        if args.domain in ENTITY_SERVICE_DOMAINS and any("all" in (v if isinstance(v, list) else [v]) for k, v in args.data.items() if k in ("entity_id", "device_id", "area_id", "floor_id")):
            raise ValueError("Action not sent: blanket all targets are not supported")
        payload.update(method="POST", path=f"api/services/{args.domain}/{args.service}" + ("?return_response" if args.return_response else ""), data=args.data)
    elif args.operation == "websocket":
        command_type = args.command.get("type", "")
        if not isinstance(command_type, str) or not command_type:
            raise ValueError("WebSocket command needs a type")
        if command_type == "auth" or command_type.startswith(("auth/", "supervisor/", "hassio/")):
            raise ValueError("Authentication and Supervisor management are outside this Core API helper")
        if command_type not in READ_WS_TYPES and not args.execute:
            raise ValueError("Action not sent: this WebSocket type requires --execute and prior owner authorization")
        payload.update(command=args.command, limit=args.limit)
    return payload


def execute(host, payload):
    worker = Path(__file__).with_name("edcore-ha-remote.py").read_text()
    command = shlex.join(["qm", "guest", "exec", "300", "--pass-stdin", "1", "--timeout", "40", "--", "docker", "exec", "-i", "hassio_supervisor", "python3", "-c", worker])
    completed = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=10", host, command], input=json.dumps(payload), capture_output=True, text=True, timeout=60)
    if completed.returncode:
        raise RuntimeError(f"Existing SSH/guest route failed (exit {completed.returncode}); no fallback credentials attempted")
    guest = json.loads(completed.stdout)
    if not guest.get("exited"):
        raise RuntimeError("Guest command did not complete within the bound; no retry sent")
    if guest.get("out-truncated"):
        raise RuntimeError("Guest-agent output truncated; narrow the query and retry a read only")
    try:
        result = json.loads(guest.get("out-data", ""))
    except json.JSONDecodeError as exc:
        raise RuntimeError("Guest API worker produced no complete JSON response") from exc
    if guest.get("exitcode") or not result.get("ok"):
        raise RuntimeError(result.get("error", "Guest API worker failed"))
    return result


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = prepare(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        result = execute(args.host, payload)
    except (RuntimeError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        print(json.dumps({"ok": False, "error": str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
