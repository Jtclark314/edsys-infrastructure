"""Pure sanitization and fail-closed replay namespace guards."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any


TRACE_SCHEMA = "edsys.sanitized-trace.v1"
TEST_ROOT = "edsys/test/v1/replay"
RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
SAFE_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
SANITIZED_SOURCE_RE = re.compile(r"^source-[0-9a-f]{16}$")
ACTUATOR_SEGMENTS = frozenset({"command", "commands", "actuator", "actuators", "actuation", "control"})
REJECTED_PAYLOAD_KEYS = frozenset({"action", "command", "commands", "target", "parameters", "service_data"})
ALLOWED_SCALAR_KEYS = frozenset({"schema", "metric", "value", "unit", "state", "availability", "quality"})
ALLOWED_TAG_KEYS = frozenset({"location_class", "sensor_type", "channel_class"})


class GuardError(ValueError):
    pass


def _segments(topic: str) -> list[str]:
    if not isinstance(topic, str) or not 1 <= len(topic) <= 512 or "\x00" in topic:
        raise GuardError("invalid topic")
    segments = topic.lower().split("/")
    if any(not segment or segment in {".", ".."} or "+" in segment or "#" in segment for segment in segments):
        raise GuardError("invalid topic")
    if ACTUATOR_SEGMENTS.intersection(segments):
        raise GuardError("actuator topic rejected")
    return segments


def _source_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def sanitize_topic(topic: str) -> str:
    segments = _segments(topic)
    if segments[:4] == ["edsys", "v1", "telemetry", "environment"]:
        category = "telemetry/environment"
        suffix = segments[4:]
    elif segments[:4] == ["edsys", "v1", "telemetry", "energy"]:
        category = "telemetry/energy"
        suffix = segments[4:]
    elif segments[:4] == ["edsys", "v1", "telemetry", "rf"]:
        category = "telemetry/rf"
        suffix = segments[4:]
    elif segments[:4] == ["edsys", "v1", "telemetry", "highrate"]:
        category = "telemetry/highrate"
        suffix = segments[4:]
    elif segments[:3] == ["edsys", "v1", "state"]:
        category = "state"
        suffix = segments[3:]
    elif segments[:3] == ["edsys", "v1", "availability"]:
        category = "availability"
        suffix = segments[3:]
    else:
        raise GuardError("topic is outside the record allowlist")
    if not suffix:
        raise GuardError("topic has no source")
    return f"{category}/source-{_source_digest('/'.join(suffix))}"


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise GuardError("payload contains a non-finite number")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str) and len(value) <= 256:
        return value
    raise GuardError("payload contains an unsafe value")


def sanitize_payload(payload: bytes) -> dict[str, Any]:
    if len(payload) > 65536:
        raise GuardError("payload too large")
    try:
        raw = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GuardError("payload must be JSON") from exc
    if not isinstance(raw, dict) or REJECTED_PAYLOAD_KEYS.intersection(raw):
        raise GuardError("command-like payload rejected")

    sanitized: dict[str, Any] = {}
    for key in ALLOWED_SCALAR_KEYS:
        if key in raw:
            sanitized[key] = _safe_scalar(raw[key])
    if "source" in raw:
        if not isinstance(raw["source"], str) or not raw["source"]:
            raise GuardError("invalid source")
        sanitized["source"] = "source-" + _source_digest(raw["source"])
    tags = raw.get("tags")
    if tags is not None:
        if not isinstance(tags, dict):
            raise GuardError("tags must be an object")
        safe_tags = {key: _safe_scalar(value) for key, value in tags.items() if key in ALLOWED_TAG_KEYS}
        if safe_tags:
            sanitized["tags"] = safe_tags
    if not sanitized:
        raise GuardError("payload contains no approved fields")
    return sanitized


def build_event(offset_ms: int, source_topic: str, payload: bytes) -> dict[str, Any]:
    if not isinstance(offset_ms, int) or isinstance(offset_ms, bool) or offset_ms < 0:
        raise GuardError("invalid event offset")
    return {
        "kind": "event",
        "offset_ms": offset_ms,
        "topic": sanitize_topic(source_topic),
        "payload": sanitize_payload(payload),
    }


def replay_topic(run_id: str, relative_topic: str) -> str:
    if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
        raise GuardError("invalid replay run id")
    segments = _segments(relative_topic)
    if segments[:2] == ["edsys", "v1"] or relative_topic.startswith("/"):
        raise GuardError("absolute or production topic rejected")
    if any(not SAFE_SEGMENT_RE.fullmatch(segment) for segment in segments):
        raise GuardError("invalid sanitized topic")
    destination = f"{TEST_ROOT}/{run_id}/{relative_topic}"
    if not destination.startswith(f"{TEST_ROOT}/"):
        raise GuardError("replay escaped test namespace")
    return destination


def validate_trace_event(event: Any) -> tuple[int, str, dict[str, Any]]:
    if not isinstance(event, dict) or set(event) != {"kind", "offset_ms", "topic", "payload"}:
        raise GuardError("invalid trace event shape")
    if event["kind"] != "event":
        raise GuardError("invalid trace event kind")
    offset = event["offset_ms"]
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise GuardError("invalid event offset")
    topic = event["topic"]
    replay_topic("validation", topic)
    payload = event["payload"]
    allowed_keys = ALLOWED_SCALAR_KEYS | {"source", "tags"}
    if (
        not isinstance(payload, dict)
        or not payload
        or not set(payload).issubset(allowed_keys)
        or REJECTED_PAYLOAD_KEYS.intersection(payload)
    ):
        raise GuardError("unsafe trace payload")
    for key in ALLOWED_SCALAR_KEYS:
        if key in payload:
            _safe_scalar(payload[key])
    if "source" in payload and (
        not isinstance(payload["source"], str)
        or not SANITIZED_SOURCE_RE.fullmatch(payload["source"])
    ):
        raise GuardError("trace source is not sanitized")
    if "tags" in payload:
        tags = payload["tags"]
        if (
            not isinstance(tags, dict)
            or not tags
            or not set(tags).issubset(ALLOWED_TAG_KEYS)
        ):
            raise GuardError("unsafe trace tags")
        for value in tags.values():
            _safe_scalar(value)
    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > 65536:
        raise GuardError("trace payload too large")
    return offset, topic, payload
