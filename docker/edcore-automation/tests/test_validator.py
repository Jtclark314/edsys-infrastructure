"""Standard-library tests for the fail-closed command-envelope validator."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from uuid import UUID, uuid4


STACK_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_SOURCE = STACK_ROOT / "runtime" / "src"
sys.path.insert(0, str(RUNTIME_SOURCE))

from automation_runtime.validator import (  # noqa: E402
    Policy,
    REDIRECT_PARAMETER_KEYS,
    ValidationError,
    validate_command,
)


NOW = datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)
TARGET = "ha/light/living_room"
ACTION = "turn_on"
VALID_RULE = {
    "target": TARGET,
    "action": ACTION,
    "parameters": {
        "required": ["brightness_pct", "mode"],
        "properties": {
            "brightness_pct": {"type": "integer", "minimum": 0, "maximum": 100},
            "mode": {
                "type": "string",
                "max_length": 8,
                "enum": ["normal", "quiet"],
            },
            "transition": {"type": "number", "minimum": 0, "maximum": 10},
            "verify": {"type": "boolean"},
        },
    },
}


def load_policy(payload: object) -> Policy:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return Policy.load(path)


def one_rule_policy(rule: object | None = None, *, ttl: int = 300) -> dict[str, object]:
    return {
        "schema": "edsys.command-policy.v1",
        "max_ttl_seconds": ttl,
        "allowed": [deepcopy(VALID_RULE if rule is None else rule)],
    }


class ValidatorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = load_policy(one_rule_policy())

    def envelope(self, **changes: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": "edsys.command.request.v1",
            "id": str(uuid4()),
            "created_at": (NOW - timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
            "expires_at": (NOW + timedelta(seconds=59)).isoformat().replace("+00:00", "Z"),
            "target": TARGET,
            "action": ACTION,
            "parameters": {
                "brightness_pct": 35,
                "mode": "normal",
                "transition": 0.5,
                "verify": True,
            },
            "correlation_id": "flow-42",
        }
        payload.update(changes)
        return payload

    def assert_rejected(self, expected_code: str, payload: object) -> None:
        with self.assertRaises(ValidationError) as caught:
            validate_command(payload, self.policy, now=NOW)
        self.assertEqual(caught.exception.code, expected_code)

    def test_valid_scalar_command_is_normalized_for_the_ha_namespace(self) -> None:
        payload = self.envelope()
        command = validate_command(payload, self.policy, now=NOW)

        self.assertEqual(command.command_id, payload["id"])
        self.assertEqual(command.output_topic, "edsys/v1/command/ha/light/living_room")
        self.assertEqual(
            command.output_payload(),
            {
                "schema": "edsys.command.v1",
                "id": payload["id"],
                "created_at": "2026-08-22T17:59:59Z",
                "expires_at": "2026-08-22T18:00:59Z",
                "target": TARGET,
                "action": ACTION,
                "parameters": {
                    "brightness_pct": 35,
                    "mode": "normal",
                    "transition": 0.5,
                    "verify": True,
                },
                "correlation_id": "flow-42",
            },
        )

    def test_optional_fields_and_optional_schema_properties_may_be_omitted(self) -> None:
        payload = self.envelope(parameters={"brightness_pct": 35, "mode": "quiet"})
        payload.pop("correlation_id")
        command = validate_command(payload, self.policy, now=NOW)
        self.assertNotIn("correlation_id", command.output_payload())
        self.assertEqual(command.parameters, {"brightness_pct": 35, "mode": "quiet"})

    def test_envelope_shape_and_schema_are_exact(self) -> None:
        self.assert_rejected("invalid_envelope", [])

        missing = self.envelope()
        missing.pop("parameters")
        self.assert_rejected("invalid_envelope", missing)

        self.assert_rejected("invalid_envelope", self.envelope(unexpected=True))
        self.assert_rejected("unsupported_schema", self.envelope(schema="edsys.command.request.v2"))

    def test_id_must_be_canonical_lowercase_uuid4(self) -> None:
        self.assert_rejected("invalid_id", self.envelope(id="not-a-uuid"))
        self.assert_rejected(
            "invalid_id",
            self.envelope(id=str(UUID("00000000-0000-1000-8000-000000000000"))),
        )
        self.assert_rejected("invalid_id", self.envelope(id=str(uuid4()).upper()))

    def test_timestamp_and_expiry_fail_closed(self) -> None:
        cases = [
            ("invalid_timestamp", {"created_at": "2026-08-22T17:59:59"}),
            (
                "future_command",
                {"created_at": "2026-08-22T18:00:31Z", "expires_at": "2026-08-22T18:01:00Z"},
            ),
            (
                "invalid_expiry",
                {"created_at": "2026-08-22T18:00:00Z", "expires_at": "2026-08-22T18:00:00Z"},
            ),
            (
                "ttl_exceeded",
                {"created_at": "2026-08-22T17:59:59Z", "expires_at": "2026-08-22T18:05:00Z"},
            ),
            (
                "expired",
                {"created_at": "2026-08-22T17:58:00Z", "expires_at": "2026-08-22T18:00:00Z"},
            ),
        ]
        for code, changes in cases:
            with self.subTest(code=code):
                self.assert_rejected(code, self.envelope(**changes))

    def test_target_action_and_policy_are_bounded(self) -> None:
        self.assert_rejected("invalid_target", self.envelope(target="ha/light/LivingRoom"))
        self.assert_rejected("invalid_action", self.envelope(action="turn-on"))
        self.assert_rejected("unauthorized", self.envelope(target="ha/lock/front_door"))
        self.assert_rejected("unauthorized", self.envelope(action="toggle"))

    def test_required_extra_and_nonscalar_parameters_fail_closed(self) -> None:
        self.assert_rejected(
            "invalid_parameters",
            self.envelope(parameters={"brightness_pct": 35}),
        )
        self.assert_rejected(
            "invalid_parameters",
            self.envelope(parameters={"brightness_pct": 35, "mode": "normal", "extra": True}),
        )
        for value in ([], {}, None):
            with self.subTest(nonscalar=value):
                self.assert_rejected(
                    "invalid_parameters",
                    self.envelope(parameters={"brightness_pct": 35, "mode": value}),
                )

    def test_numeric_types_ranges_and_nonfinite_values_are_rejected(self) -> None:
        for value in (-1, 101, 35.5, True):
            with self.subTest(integer=value):
                self.assert_rejected(
                    "invalid_parameters",
                    self.envelope(parameters={"brightness_pct": value, "mode": "normal"}),
                )
        for value in (-0.01, 10.01, True, float("nan"), float("inf"), float("-inf")):
            with self.subTest(number=value):
                self.assert_rejected(
                    "invalid_parameters",
                    self.envelope(
                        parameters={"brightness_pct": 35, "mode": "normal", "transition": value}
                    ),
                )

        command = validate_command(
            self.envelope(parameters={"brightness_pct": 35, "mode": "normal", "transition": 2}),
            self.policy,
            now=NOW,
        )
        self.assertEqual(command.parameters["transition"], 2)

    def test_string_values_must_be_exact_reviewed_enum_members(self) -> None:
        for value in ("boost", "NORMAL", "", 1, True):
            with self.subTest(enum=value):
                self.assert_rejected(
                    "invalid_parameters",
                    self.envelope(parameters={"brightness_pct": 35, "mode": value}),
                )

    def test_authority_redirect_parameter_keys_are_always_rejected(self) -> None:
        expected_redirects = {
            "area_id",
            "device_id",
            "domain",
            "entity_id",
            "service",
            "service_data",
            "target",
            "targets",
            "topic",
        }
        self.assertEqual(REDIRECT_PARAMETER_KEYS, expected_redirects)
        for key in sorted(expected_redirects):
            parameters = {"brightness_pct": 35, "mode": "normal", key: "redirect"}
            with self.subTest(redirect_key=key):
                self.assert_rejected(
                    "redirect_parameter",
                    self.envelope(parameters=parameters),
                )

    def test_parameter_shape_correlation_and_envelope_size_are_bounded(self) -> None:
        self.assert_rejected(
            "parameters_too_large",
            self.envelope(parameters={"brightness_pct": 35, "mode": "x" * 4097}),
        )
        deep: object = "normal"
        for _ in range(9):
            deep = {"child": deep}
        self.assert_rejected(
            "parameters_too_deep",
            self.envelope(parameters={"brightness_pct": 35, "mode": deep}),
        )
        self.assert_rejected("invalid_correlation_id", self.envelope(correlation_id="has whitespace"))
        self.assert_rejected(
            "envelope_too_large",
            self.envelope(unexpected="x" * 65536),
        )


class PolicyLoadTestCase(unittest.TestCase):
    def assert_invalid_policy(self, payload: object) -> None:
        with self.assertRaises(ValidationError) as caught:
            load_policy(payload)
        self.assertEqual(caught.exception.code, "invalid_policy")

    def test_policy_loader_accepts_only_exact_scalar_schema_rules(self) -> None:
        policy = load_policy(one_rule_policy(ttl=120))
        rule = policy.rule_for(TARGET, ACTION)
        self.assertIsNotNone(rule)
        self.assertEqual(rule.required, {"brightness_pct", "mode"})
        self.assertEqual(set(rule.properties), {"brightness_pct", "mode", "transition", "verify"})
        self.assertIsNone(policy.rule_for(TARGET, "toggle"))
        self.assertIsNone(policy.rule_for("ha/light/kitchen", ACTION))

    def test_production_policy_starts_with_no_authorized_command(self) -> None:
        policy = Policy.load(STACK_ROOT / "runtime" / "config" / "policy.json")
        self.assertEqual(policy.rules, ())
        self.assertIsNone(policy.rule_for(TARGET, ACTION))
        payload = {
            "schema": "edsys.command.request.v1",
            "id": str(uuid4()),
            "created_at": "2026-08-22T17:59:59Z",
            "expires_at": "2026-08-22T18:00:59Z",
            "target": TARGET,
            "action": ACTION,
            "parameters": {},
        }
        with self.assertRaises(ValidationError) as caught:
            validate_command(payload, policy, now=NOW)
        self.assertEqual(caught.exception.code, "unauthorized")

    def test_policy_rejects_top_level_and_rule_ambiguity(self) -> None:
        valid = one_rule_policy(ttl=120)
        duplicate = deepcopy(VALID_RULE)
        cases = [
            {**valid, "schema": "edsys.command-policy.v2"},
            {**valid, "max_ttl_seconds": True},
            {**valid, "max_ttl_seconds": 3601},
            {**valid, "allowed": "not-a-list"},
            {**valid, "allowed": [deepcopy(VALID_RULE), duplicate]},
            {**valid, "allowed": [{"target": TARGET, "action": ACTION}]},
            {
                **valid,
                "allowed": [{**deepcopy(VALID_RULE), "actions": [ACTION]}],
            },
            {**valid, "allowed": [{**deepcopy(VALID_RULE), "extra": True}]},
            {**valid, "extra": "not permitted"},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                self.assert_invalid_policy(payload)

    def test_parameter_schema_requires_exact_required_and_properties(self) -> None:
        valid = deepcopy(VALID_RULE)
        cases: list[dict[str, object]] = []
        for parameters in (
            {"required": ["brightness_pct", "mode"]},
            {"properties": deepcopy(valid["parameters"]["properties"])},
            {
                **deepcopy(valid["parameters"]),
                "extra": True,
            },
            {
                "required": ["brightness_pct", "brightness_pct"],
                "properties": deepcopy(valid["parameters"]["properties"]),
            },
            {
                "required": ["missing"],
                "properties": deepcopy(valid["parameters"]["properties"]),
            },
        ):
            rule = deepcopy(valid)
            rule["parameters"] = parameters
            cases.append(rule)
        for rule in cases:
            with self.subTest(rule=rule):
                self.assert_invalid_policy(one_rule_policy(rule))

        for redirect_key in sorted(REDIRECT_PARAMETER_KEYS):
            rule = deepcopy(valid)
            rule["parameters"]["properties"][redirect_key] = {"type": "boolean"}
            with self.subTest(policy_redirect=redirect_key):
                self.assert_invalid_policy(one_rule_policy(rule))

    def test_scalar_property_schemas_reject_missing_extra_or_unsupported_fields(self) -> None:
        invalid_specs = [
            {},
            {"type": "boolean", "extra": True},
            {"type": "integer", "minimum": 0},
            {"type": "number", "minimum": 0, "maximum": 1, "extra": True},
            {"type": "string", "max_length": 8},
            {"type": "object"},
            {"type": "array"},
        ]
        for spec in invalid_specs:
            rule = deepcopy(VALID_RULE)
            rule["parameters"]["properties"]["verify"] = spec
            with self.subTest(spec=spec):
                self.assert_invalid_policy(one_rule_policy(rule))

    def test_numeric_policy_ranges_must_be_finite_ordered_and_typed(self) -> None:
        invalid_specs = [
            {"type": "integer", "minimum": 2, "maximum": 1},
            {"type": "integer", "minimum": 0.0, "maximum": 1},
            {"type": "integer", "minimum": False, "maximum": 1},
            {"type": "number", "minimum": float("nan"), "maximum": 1},
            {"type": "number", "minimum": 0, "maximum": float("inf")},
            {"type": "number", "minimum": 0, "maximum": float("-inf")},
        ]
        for spec in invalid_specs:
            rule = deepcopy(VALID_RULE)
            rule["parameters"]["properties"]["transition"] = spec
            with self.subTest(spec=spec):
                self.assert_invalid_policy(one_rule_policy(rule))

    def test_string_policy_enums_are_nonempty_unique_and_bounded(self) -> None:
        invalid_specs = [
            {"type": "string", "max_length": 0, "enum": ["normal"]},
            {"type": "string", "max_length": 257, "enum": ["normal"]},
            {"type": "string", "max_length": True, "enum": ["normal"]},
            {"type": "string", "max_length": 8, "enum": []},
            {"type": "string", "max_length": 8, "enum": ["normal", "normal"]},
            {"type": "string", "max_length": 3, "enum": ["normal"]},
            {"type": "string", "max_length": 8, "enum": [""]},
            {"type": "string", "max_length": 8, "enum": [1]},
        ]
        for spec in invalid_specs:
            rule = deepcopy(VALID_RULE)
            rule["parameters"]["properties"]["mode"] = spec
            with self.subTest(spec=spec):
                self.assert_invalid_policy(one_rule_policy(rule))


if __name__ == "__main__":
    unittest.main()
