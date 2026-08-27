"""Tests for the pure event-recording and replay namespace guard."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sys
import unittest


STACK_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE = STACK_ROOT / "runtime" / "src"
sys.path.insert(0, str(RUNTIME_SOURCE))

from event_harness.guard import (  # noqa: E402
    GuardError,
    build_event,
    replay_topic,
    sanitize_payload,
    sanitize_topic,
    validate_trace_event,
)


class RecordSanitizerTestCase(unittest.TestCase):
    def test_topic_is_allowlisted_and_source_is_pseudonymized(self) -> None:
        source = "living-room/co2-sensor-serial-123"
        expected_digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(
            sanitize_topic(f"edsys/v1/telemetry/environment/{source}"),
            f"telemetry/environment/source-{expected_digest}",
        )
        self.assertNotIn("living-room", sanitize_topic(f"edsys/v1/telemetry/environment/{source}"))

    def test_topic_suffix_and_payload_source_use_independent_exact_pseudonyms(self) -> None:
        topic_identity = "edge-livingroom/synthetic"
        payload_identity = "edge-livingroom"
        topic_digest = hashlib.sha256(topic_identity.encode("utf-8")).hexdigest()[:16]
        payload_digest = hashlib.sha256(payload_identity.encode("utf-8")).hexdigest()[:16]
        self.assertEqual(topic_digest, "c9bcc96c4b28a88e")
        self.assertEqual(payload_digest, "1eb64c87be9828ee")
        self.assertNotEqual(topic_digest, payload_digest)

        event = build_event(
            0,
            f"edsys/v1/telemetry/environment/{topic_identity}",
            b'{"source":"edge-livingroom","metric":"synthetic-acceptance","value":1}',
        )
        self.assertEqual(
            event["topic"],
            "telemetry/environment/source-c9bcc96c4b28a88e",
        )
        self.assertEqual(event["payload"]["source"], "source-1eb64c87be9828ee")
        self.assertNotEqual(event["topic"].rsplit("/", 1)[1], event["payload"]["source"])
        self.assertNotIn("edge-livingroom", repr(event))

    def test_only_selected_source_namespaces_can_be_recorded(self) -> None:
        accepted = {
            "edsys/v1/telemetry/environment/source": "telemetry/environment/",
            "edsys/v1/telemetry/energy/source": "telemetry/energy/",
            "edsys/v1/telemetry/rf/source": "telemetry/rf/",
            "edsys/v1/telemetry/highrate/source": "telemetry/highrate/",
            "edsys/v1/state/homeassistant/entity": "state/",
            "edsys/v1/availability/host/service": "availability/",
        }
        for topic, prefix in accepted.items():
            with self.subTest(topic=topic):
                self.assertTrue(sanitize_topic(topic).startswith(prefix))

        rejected = (
            "homeassistant/light/living_room/state",
            "frigate/events",
            "edsys/v1/telemetry/other/source",
            "edsys/v1/command/ha/light/living_room",
            "edsys/v1/automation/request/nodered",
            "edsys/v1/state/control/relay",
            "edsys/v1/state/+",
            "edsys/v1/state/#",
        )
        for topic in rejected:
            with self.subTest(topic=topic), self.assertRaises(GuardError):
                sanitize_topic(topic)

    def test_payload_keeps_only_bounded_analytics_fields(self) -> None:
        raw = (
            b'{"schema":"edsys.telemetry.v1","source":"sensor-serial-123",'
            b'"metric":"temperature","value":21.5,"unit":"Cel","quality":"good",'
            b'"tags":{"location_class":"living-space","sensor_type":"environment",'
            b'"device_name":"private-name"},"unknown":"discard-me"}'
        )
        sanitized = sanitize_payload(raw)

        self.assertEqual(sanitized["metric"], "temperature")
        self.assertEqual(sanitized["tags"], {
            "location_class": "living-space",
            "sensor_type": "environment",
        })
        self.assertRegex(sanitized["source"], r"^source-[0-9a-f]{16}$")
        self.assertNotIn("unknown", sanitized)
        self.assertNotIn("device_name", sanitized["tags"])
        self.assertNotIn("sensor-serial-123", str(sanitized))

    def test_command_like_or_unbounded_payload_is_rejected(self) -> None:
        payloads = (
            b'{"action":"turn_on","value":1}',
            b'{"target":"ha/light/living_room","state":"on"}',
            b'{"parameters":{},"value":1}',
            b'not-json',
            b'[]',
            b'{"metric":"' + (b"x" * 257) + b'"}',
            b'{"private_name":"only-unknown-data"}',
            b'{"metric":"temperature","value":NaN}',
            b'{"metric":"temperature","value":Infinity}',
        )
        for payload in payloads:
            with self.subTest(payload=payload[:60]), self.assertRaises(GuardError):
                sanitize_payload(payload)

    def test_build_event_has_exact_replayable_shape(self) -> None:
        event = build_event(
            125,
            "edsys/v1/telemetry/energy/meter-1",
            b'{"metric":"power","value":123.4,"unit":"W","source":"meter-1"}',
        )
        self.assertEqual(set(event), {"kind", "offset_ms", "topic", "payload"})
        self.assertEqual(event["kind"], "event")
        self.assertEqual(event["offset_ms"], 125)
        self.assertTrue(event["topic"].startswith("telemetry/energy/source-"))
        self.assertEqual(validate_trace_event(event), (125, event["topic"], event["payload"]))


class ReplayGuardTestCase(unittest.TestCase):
    def test_destination_is_derived_only_under_the_run_namespace(self) -> None:
        self.assertEqual(
            replay_topic("run-20260822", "telemetry/environment/source-0123456789abcdef"),
            "edsys/test/v1/replay/run-20260822/telemetry/environment/source-0123456789abcdef",
        )

    def test_run_id_and_relative_topic_are_strict(self) -> None:
        rejected = (
            ("UPPER", "telemetry/environment/source-1"),
            ("../escape", "telemetry/environment/source-1"),
            ("run", "/telemetry/environment/source-1"),
            ("run", "edsys/v1/state/source"),
            ("run", "command/ha/light"),
            ("run", "actuator/relay"),
            ("run", "telemetry/+/source"),
            ("run", "telemetry/#"),
            ("run", "telemetry//source"),
        )
        for run_id, topic in rejected:
            with self.subTest(run_id=run_id, topic=topic), self.assertRaises(GuardError):
                replay_topic(run_id, topic)

    def test_trace_event_must_already_satisfy_sanitized_payload_contract(self) -> None:
        base = {
            "kind": "event",
            "offset_ms": 0,
            "topic": "state/source-0123456789abcdef",
            "payload": {"state": "on", "source": "source-0123456789abcdef"},
        }
        self.assertEqual(validate_trace_event(base), (0, base["topic"], base["payload"]))

        unsafe_payloads = (
            {"action": "turn_on"},
            {"password": "must-not-replay"},
            {"metric": {"nested": "not-a-scalar"}},
            {"source": "unredacted-exact-device-name"},
        )
        for payload in unsafe_payloads:
            with self.subTest(payload=payload), self.assertRaises(GuardError):
                validate_trace_event({**base, "payload": payload})

    def test_trace_event_shape_offset_and_topic_are_exact(self) -> None:
        good = {
            "kind": "event",
            "offset_ms": 0,
            "topic": "availability/source-0123456789abcdef",
            "payload": {"availability": "online"},
        }
        invalid = (
            {**good, "extra": True},
            {**good, "kind": "header"},
            {**good, "offset_ms": -1},
            {**good, "offset_ms": True},
            {**good, "topic": "control/relay"},
            {**good, "payload": []},
        )
        for event in invalid:
            with self.subTest(event=event), self.assertRaises(GuardError):
                validate_trace_event(event)


if __name__ == "__main__":
    unittest.main()
