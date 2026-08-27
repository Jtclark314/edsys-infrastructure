"""Pure command-envelope and policy validation.

This module does not decide when an automation should run. It enforces the
wire contract and a narrow, reviewed authorization boundary before Home
Assistant receives a requested action.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
from typing import Any
from uuid import UUID


SCHEMA = "edsys.command.request.v1"
TARGET_RE = re.compile(r"^ha/[a-z0-9][a-z0-9_-]*(?:/[a-z0-9][a-z0-9_-]*){1,7}$")
ACTION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
PARAMETER_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REDIRECT_PARAMETER_KEYS = frozenset(
    {
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
)
MAX_JSON_BYTES = 65536
MAX_DEPTH = 8
MAX_CONTAINER_ITEMS = 256


class ValidationError(ValueError):
    """A safe, stable rejection code plus an operator-oriented detail."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _finite_number(value: int | float) -> bool:
    return not isinstance(value, float) or math.isfinite(value)


def _parse_utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or len(value) > 40:
        raise ValidationError("invalid_timestamp", f"{field} must be an RFC3339 string")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValidationError("invalid_timestamp", f"{field} is not RFC3339") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError("invalid_timestamp", f"{field} must include a UTC offset")
    parsed = parsed.astimezone(timezone.utc)
    return parsed


def _validate_shape(value: Any, depth: int = 0) -> None:
    if depth > MAX_DEPTH:
        raise ValidationError("parameters_too_deep", "parameters exceed maximum nesting")
    if isinstance(value, dict):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise ValidationError("parameters_too_large", "too many object members")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise ValidationError("invalid_parameters", "parameter keys must be short strings")
            _validate_shape(child, depth + 1)
    elif isinstance(value, list):
        if len(value) > MAX_CONTAINER_ITEMS:
            raise ValidationError("parameters_too_large", "too many list members")
        for child in value:
            _validate_shape(child, depth + 1)
    elif isinstance(value, str):
        if len(value) > 4096:
            raise ValidationError("parameters_too_large", "parameter string is too long")
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValidationError("invalid_parameters", "non-finite numbers are not valid JSON")
    elif value is not None and not isinstance(value, (bool, int, float)):
        raise ValidationError("invalid_parameters", "unsupported parameter value")


@dataclass(frozen=True)
class ParameterSpec:
    value_type: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    max_length: int | None = None
    enum: frozenset[str] | None = None


@dataclass(frozen=True)
class PolicyRule:
    target: str
    action: str
    properties: dict[str, ParameterSpec]
    required: frozenset[str]


@dataclass(frozen=True)
class Policy:
    max_ttl_seconds: int
    rules: tuple[PolicyRule, ...]

    @classmethod
    def load(cls, path: str | Path) -> "Policy":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValidationError("invalid_policy", "policy could not be loaded") from exc
        if (
            not isinstance(raw, dict)
            or set(raw) != {"schema", "max_ttl_seconds", "allowed"}
            or raw.get("schema") != "edsys.command-policy.v1"
        ):
            raise ValidationError("invalid_policy", "unsupported policy schema")
        ttl = raw.get("max_ttl_seconds")
        if not isinstance(ttl, int) or isinstance(ttl, bool) or not 1 <= ttl <= 3600:
            raise ValidationError("invalid_policy", "max_ttl_seconds must be 1..3600")
        allowed = raw.get("allowed")
        if not isinstance(allowed, list) or len(allowed) > 256:
            raise ValidationError("invalid_policy", "allowed must be a bounded list")
        rules: list[PolicyRule] = []
        seen: set[tuple[str, str]] = set()
        for item in allowed:
            if not isinstance(item, dict) or set(item) != {"target", "action", "parameters"}:
                raise ValidationError("invalid_policy", "each rule needs target, action, and parameters only")
            target = item["target"]
            action = item["action"]
            if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
                raise ValidationError("invalid_policy", "policy target is invalid")
            if not isinstance(action, str) or not ACTION_RE.fullmatch(action):
                raise ValidationError("invalid_policy", "policy action is invalid")
            identity = (target, action)
            if identity in seen:
                raise ValidationError("invalid_policy", "policy contains a duplicate target/action")
            seen.add(identity)

            parameter_schema = item["parameters"]
            if not isinstance(parameter_schema, dict) or set(parameter_schema) != {"required", "properties"}:
                raise ValidationError("invalid_policy", "parameters needs required and properties only")
            required = parameter_schema["required"]
            properties = parameter_schema["properties"]
            if (
                not isinstance(required, list)
                or len(required) > 64
                or len(set(required)) != len(required)
                or any(not isinstance(name, str) or not PARAMETER_RE.fullmatch(name) for name in required)
                or not isinstance(properties, dict)
                or len(properties) > 64
            ):
                raise ValidationError("invalid_policy", "parameter schema is not bounded")
            if any(
                not isinstance(name, str)
                or not PARAMETER_RE.fullmatch(name)
                or name in REDIRECT_PARAMETER_KEYS
                for name in properties
            ):
                raise ValidationError("invalid_policy", "parameter property is invalid or redirects authority")
            if not set(required).issubset(properties):
                raise ValidationError("invalid_policy", "required parameters must have properties")

            parsed_properties: dict[str, ParameterSpec] = {}
            for name, spec in properties.items():
                if not isinstance(spec, dict) or "type" not in spec:
                    raise ValidationError("invalid_policy", "every parameter property needs a type")
                value_type = spec["type"]
                if value_type == "boolean":
                    if set(spec) != {"type"}:
                        raise ValidationError("invalid_policy", "boolean parameter schema has extra fields")
                    parsed = ParameterSpec(value_type=value_type)
                elif value_type in {"integer", "number"}:
                    if set(spec) != {"type", "minimum", "maximum"}:
                        raise ValidationError("invalid_policy", "numeric parameter schema needs an exact range")
                    minimum, maximum = spec["minimum"], spec["maximum"]
                    numeric_type = int if value_type == "integer" else (int, float)
                    if (
                        isinstance(minimum, bool)
                        or isinstance(maximum, bool)
                        or not isinstance(minimum, numeric_type)
                        or not isinstance(maximum, numeric_type)
                        or not _finite_number(minimum)
                        or not _finite_number(maximum)
                        or minimum > maximum
                    ):
                        raise ValidationError("invalid_policy", "numeric parameter range is invalid")
                    parsed = ParameterSpec(value_type=value_type, minimum=minimum, maximum=maximum)
                elif value_type == "string":
                    if set(spec) != {"type", "max_length", "enum"}:
                        raise ValidationError("invalid_policy", "string parameter schema needs max_length and enum")
                    max_length, enum = spec["max_length"], spec["enum"]
                    if (
                        not isinstance(max_length, int)
                        or isinstance(max_length, bool)
                        or not 1 <= max_length <= 256
                        or not isinstance(enum, list)
                        or not 1 <= len(enum) <= 64
                        or len(set(enum)) != len(enum)
                        or any(not isinstance(value, str) or not 1 <= len(value) <= max_length for value in enum)
                    ):
                        raise ValidationError("invalid_policy", "string parameter enum is invalid")
                    parsed = ParameterSpec(
                        value_type=value_type,
                        max_length=max_length,
                        enum=frozenset(enum),
                    )
                else:
                    raise ValidationError("invalid_policy", "unsupported parameter type")
                parsed_properties[name] = parsed
            rules.append(
                PolicyRule(
                    target=target,
                    action=action,
                    properties=parsed_properties,
                    required=frozenset(required),
                )
            )
        return cls(max_ttl_seconds=ttl, rules=tuple(rules))

    def rule_for(self, target: str, action: str) -> PolicyRule | None:
        return next((rule for rule in self.rules if rule.target == target and action == rule.action), None)

    def validate_parameters(self, rule: PolicyRule, parameters: dict[str, Any]) -> None:
        keys = set(parameters)
        if keys & REDIRECT_PARAMETER_KEYS:
            raise ValidationError("redirect_parameter", "parameters may not override HA authority")
        if not rule.required.issubset(keys) or not keys.issubset(rule.properties):
            raise ValidationError("invalid_parameters", "parameter keys do not match the reviewed schema")
        for name, value in parameters.items():
            spec = rule.properties[name]
            if spec.value_type == "boolean":
                valid = isinstance(value, bool)
            elif spec.value_type == "integer":
                valid = isinstance(value, int) and not isinstance(value, bool)
            elif spec.value_type == "number":
                valid = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            else:
                valid = isinstance(value, str) and value in (spec.enum or ()) and len(value) <= (spec.max_length or 0)
            if not valid:
                raise ValidationError("invalid_parameters", f"{name} does not match the reviewed type")
            if spec.value_type in {"integer", "number"} and not (
                spec.minimum <= value <= spec.maximum  # type: ignore[operator]
            ):
                raise ValidationError("invalid_parameters", f"{name} is outside the reviewed range")


@dataclass(frozen=True)
class Command:
    command_id: str
    created_at: datetime
    expires_at: datetime
    target: str
    action: str
    parameters: dict[str, Any]
    correlation_id: str | None

    @property
    def output_topic(self) -> str:
        return f"edsys/v1/command/{self.target}"

    def output_payload(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "schema": "edsys.command.v1",
            "id": self.command_id,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "expires_at": self.expires_at.isoformat().replace("+00:00", "Z"),
            "target": self.target,
            "action": self.action,
            "parameters": self.parameters,
        }
        if self.correlation_id is not None:
            result["correlation_id"] = self.correlation_id
        return result


def validate_command(
    raw: Any,
    policy: Policy,
    *,
    now: datetime | None = None,
    max_clock_skew_seconds: int = 30,
) -> Command:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if not isinstance(raw, dict):
        raise ValidationError("invalid_envelope", "command must be a JSON object")
    encoded = json.dumps(raw, separators=(",", ":"), ensure_ascii=True)
    if len(encoded.encode("utf-8")) > MAX_JSON_BYTES:
        raise ValidationError("envelope_too_large", "command exceeds maximum size")

    required = {"schema", "id", "created_at", "expires_at", "target", "action", "parameters"}
    optional = {"correlation_id"}
    keys = set(raw)
    if not required.issubset(keys) or not keys.issubset(required | optional):
        raise ValidationError("invalid_envelope", "command fields do not match the v1 contract")
    if raw["schema"] != SCHEMA:
        raise ValidationError("unsupported_schema", "unsupported command schema")

    command_id = raw["id"]
    if not isinstance(command_id, str) or len(command_id) != 36:
        raise ValidationError("invalid_id", "id must be a canonical UUIDv4")
    try:
        parsed_uuid = UUID(command_id)
    except ValueError as exc:
        raise ValidationError("invalid_id", "id must be a canonical UUIDv4") from exc
    if parsed_uuid.version != 4 or str(parsed_uuid) != command_id:
        raise ValidationError("invalid_id", "id must be a lowercase canonical UUIDv4")

    created_at = _parse_utc(raw["created_at"], "created_at")
    expires_at = _parse_utc(raw["expires_at"], "expires_at")
    if created_at > now.astimezone(timezone.utc) + timedelta(seconds=max_clock_skew_seconds):
        raise ValidationError("future_command", "created_at exceeds allowed clock skew")
    if expires_at <= created_at:
        raise ValidationError("invalid_expiry", "expires_at must follow created_at")
    if (expires_at - created_at).total_seconds() > policy.max_ttl_seconds:
        raise ValidationError("ttl_exceeded", "command TTL exceeds policy")
    if expires_at <= now.astimezone(timezone.utc):
        raise ValidationError("expired", "command has expired")

    target = raw["target"]
    action = raw["action"]
    if not isinstance(target, str) or not TARGET_RE.fullmatch(target):
        raise ValidationError("invalid_target", "target must be a bounded Home Assistant path")
    if not isinstance(action, str) or not ACTION_RE.fullmatch(action):
        raise ValidationError("invalid_action", "action has an invalid format")
    rule = policy.rule_for(target, action)
    if rule is None:
        raise ValidationError("unauthorized", "target/action is not in the reviewed policy")

    parameters = raw["parameters"]
    if not isinstance(parameters, dict):
        raise ValidationError("invalid_parameters", "parameters must be an object")
    _validate_shape(parameters)
    policy.validate_parameters(rule, parameters)

    correlation_id = raw.get("correlation_id")
    if correlation_id is not None and (
        not isinstance(correlation_id, str)
        or not 1 <= len(correlation_id) <= 128
        or any(character.isspace() for character in correlation_id)
    ):
        raise ValidationError("invalid_correlation_id", "correlation_id has an invalid format")

    return Command(
        command_id=command_id,
        created_at=created_at,
        expires_at=expires_at,
        target=target,
        action=action,
        parameters=parameters,
        correlation_id=correlation_id,
    )
