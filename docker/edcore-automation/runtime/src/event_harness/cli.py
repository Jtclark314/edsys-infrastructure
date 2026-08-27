"""Record sanitized events or replay them only into the test namespace."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import ssl
import threading
import time
from typing import Any

import paho.mqtt.client as mqtt

from .guard import GuardError, TRACE_SCHEMA, build_event, replay_topic, validate_trace_event


AVAILABILITY_TOPIC = "edsys/v1/availability/edcore-automation/event-replay"
RECORD_TOPICS = (
    "edsys/v1/telemetry/environment/#",
    "edsys/v1/telemetry/energy/#",
    "edsys/v1/telemetry/rf/#",
    "edsys/v1/telemetry/highrate/#",
    "edsys/v1/state/#",
    "edsys/v1/availability/#",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _availability(status: str) -> str:
    return json.dumps(
        {"schema": "edsys.availability.v1", "status": status, "source": "event-replay", "ts": _now()},
        separators=(",", ":"),
    )


def _client(client_id: str) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=client_id,
        protocol=mqtt.MQTTv5,
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.verify_mode = ssl.CERT_REQUIRED
    context.check_hostname = True
    context.load_verify_locations(os.environ.get("MQTT_CA_FILE", "/run/secrets/automation_ca_cert"))
    context.load_cert_chain(
        os.environ.get("MQTT_CERT_FILE", "/run/secrets/mqtt_client_cert"),
        os.environ.get("MQTT_KEY_FILE", "/run/secrets/mqtt_client_key"),
    )
    client.tls_set_context(context)
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    client.will_set(AVAILABILITY_TOPIC, _availability("offline"), qos=1, retain=False)
    return client


def record(args: argparse.Namespace) -> None:
    output = Path(args.output)
    if output.suffix != ".jsonl" or output.is_symlink():
        raise SystemExit("record output must be a new .jsonl regular path")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.umask(0o077)
    connected = threading.Event()
    complete = threading.Event()
    lock = threading.Lock()
    start = time.monotonic()
    count = 0
    rejected = 0

    with output.open("x", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "header", "trace_schema": TRACE_SCHEMA, "created_utc": _now()}) + "\n")
        handle.flush()
        client = _client(f"event-recorder-{os.getpid()}")

        def on_connect(
            mqtt_client: mqtt.Client,
            userdata: Any,
            flags: mqtt.ConnectFlags,
            reason_code: mqtt.ReasonCode,
            properties: mqtt.Properties | None,
        ) -> None:
            del userdata, flags, properties
            if reason_code.is_failure:
                complete.set()
                return
            for topic in RECORD_TOPICS:
                mqtt_client.subscribe(topic, qos=1)
            mqtt_client.publish(AVAILABILITY_TOPIC, _availability("online"), qos=1, retain=False)
            connected.set()

        def on_message(mqtt_client: mqtt.Client, userdata: Any, message: mqtt.MQTTMessage) -> None:
            nonlocal count, rejected
            del mqtt_client, userdata
            if message.retain:
                rejected += 1
                return
            try:
                event = build_event(int((time.monotonic() - start) * 1000), message.topic, bytes(message.payload))
            except GuardError:
                rejected += 1
                return
            with lock:
                if count >= args.max_events:
                    complete.set()
                    return
                handle.write(json.dumps(event, separators=(",", ":"), ensure_ascii=True) + "\n")
                handle.flush()
                count += 1
                if count >= args.max_events:
                    complete.set()

        client.on_connect = on_connect
        client.on_message = on_message
        client.connect(args.host, args.port, keepalive=60, clean_start=mqtt.MQTT_CLEAN_START_FIRST_ONLY)
        client.loop_start()
        if not connected.wait(15):
            client.disconnect()
            client.loop_stop()
            raise SystemExit("MQTT connection did not become ready")
        complete.wait(args.duration)
        client.publish(AVAILABILITY_TOPIC, _availability("offline"), qos=1, retain=False)
        client.disconnect()
        client.loop_stop()
    print(json.dumps({"recorded": count, "rejected": rejected, "output": str(output)}, separators=(",", ":")))


def _read_trace(path: Path) -> list[tuple[int, str, dict[str, Any]]]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 64 * 1024 * 1024:
        raise SystemExit("trace must be a bounded regular file")
    events: list[tuple[int, str, dict[str, Any]]] = []
    previous_offset = -1
    with path.open("r", encoding="utf-8") as handle:
        try:
            header = json.loads(next(handle))
        except (StopIteration, json.JSONDecodeError) as exc:
            raise SystemExit("trace header is missing or invalid") from exc
        if not isinstance(header, dict) or header.get("kind") != "header" or header.get("trace_schema") != TRACE_SCHEMA:
            raise SystemExit("unsupported trace schema")
        for line_number, line in enumerate(handle, start=2):
            if len(events) >= 100000:
                raise SystemExit("trace contains too many events")
            try:
                event = json.loads(line)
                validated = validate_trace_event(event)
            except (json.JSONDecodeError, GuardError) as exc:
                raise SystemExit(f"invalid trace event on line {line_number}") from exc
            if validated[0] < previous_offset:
                raise SystemExit("trace offsets are not monotonic")
            previous_offset = validated[0]
            events.append(validated)
    return events


def replay(args: argparse.Namespace) -> None:
    events = _read_trace(Path(args.input))
    # Validate every derived destination before opening a network connection.
    destinations = [replay_topic(args.run_id, event[1]) for event in events]
    if args.dry_run:
        print(json.dumps({"validated": len(events), "namespace": f"edsys/test/v1/replay/{args.run_id}"}))
        return

    connected = threading.Event()
    client = _client(f"event-replay-{args.run_id}")

    def on_connect(
        mqtt_client: mqtt.Client,
        userdata: Any,
        flags: mqtt.ConnectFlags,
        reason_code: mqtt.ReasonCode,
        properties: mqtt.Properties | None,
    ) -> None:
        del userdata, flags, properties
        if not reason_code.is_failure:
            mqtt_client.publish(AVAILABILITY_TOPIC, _availability("online"), qos=1, retain=False)
            connected.set()

    client.on_connect = on_connect
    client.connect(args.host, args.port, keepalive=60, clean_start=mqtt.MQTT_CLEAN_START_FIRST_ONLY)
    client.loop_start()
    if not connected.wait(15):
        client.disconnect()
        client.loop_stop()
        raise SystemExit("MQTT connection did not become ready")
    started = time.monotonic()
    for destination, (offset_ms, _topic, payload) in zip(destinations, events, strict=True):
        due = started + (offset_ms / 1000.0 / args.speed)
        if due > time.monotonic():
            time.sleep(due - time.monotonic())
        info = client.publish(
            destination,
            json.dumps(payload, separators=(",", ":"), ensure_ascii=True),
            qos=1,
            retain=False,
        )
        info.wait_for_publish(timeout=5)
        if not info.is_published():
            client.disconnect()
            client.loop_stop()
            raise SystemExit("replay publish timed out")
    client.publish(AVAILABILITY_TOPIC, _availability("offline"), qos=1, retain=False)
    client.disconnect()
    client.loop_stop()
    print(json.dumps({"replayed": len(events), "namespace": f"edsys/test/v1/replay/{args.run_id}"}))


def self_test() -> None:
    safe = replay_topic("self-test", "telemetry/environment/source-0123456789abcdef")
    if safe != "edsys/test/v1/replay/self-test/telemetry/environment/source-0123456789abcdef":
        raise SystemExit("safe replay mapping failed")
    for forbidden in ("edsys/v1/command/ha/light", "command/ha/light", "actuator/relay"):
        try:
            replay_topic("self-test", forbidden)
        except GuardError:
            continue
        raise SystemExit("forbidden replay topic was accepted")
    print("event_harness_self_test=passed")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record_parser = subparsers.add_parser("record")
    record_parser.add_argument("--output", required=True)
    record_parser.add_argument("--duration", type=int, default=300, choices=range(1, 3601), metavar="1..3600")
    record_parser.add_argument("--max-events", type=int, default=10000, choices=range(1, 100001), metavar="1..100000")
    record_parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "mosquitto"))
    record_parser.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "8883")))
    record_parser.set_defaults(func=record)

    replay_parser = subparsers.add_parser("replay")
    replay_parser.add_argument("--input", required=True)
    replay_parser.add_argument("--run-id", required=True)
    replay_parser.add_argument("--speed", type=float, default=1.0, choices=None)
    replay_parser.add_argument("--dry-run", action="store_true")
    replay_parser.add_argument("--host", default=os.environ.get("MQTT_HOST", "mosquitto"))
    replay_parser.add_argument("--port", type=int, default=int(os.environ.get("MQTT_PORT", "8883")))
    replay_parser.set_defaults(func=replay)

    self_test_parser = subparsers.add_parser("self-test")
    self_test_parser.set_defaults(func=lambda args: self_test())

    args = parser.parse_args()
    if args.command == "replay" and not 0.1 <= args.speed <= 100:
        parser.error("--speed must be 0.1..100")
    args.func(args)


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    main()
