"""Focused explicit-target HA actions through the existing approved helper.

No arbitrary services/data/URLs or automatic retries. Caller authorization for the
particular target/action is still required; discovering targets grants none.
"""
from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any

import operations as ops

DOMAINS = frozenset({"light", "input_boolean", "fan", "media_player", "scene"})
POWER_DOMAINS = frozenset({"light", "input_boolean", "fan"})
# Home Assistant MediaPlayerEntityFeature values; see the owning README's
# upstream reference. A present domain service alone is not entity support.
MEDIA = {"play": ("media_play", 16384), "pause": ("media_pause", 1),
         "stop": ("media_stop", 4096), "volume": ("volume_set", 4),
         "mute": ("volume_mute", 8), "on": ("turn_on", 128),
         "off": ("turn_off", 256)}
BRIGHTNESS_MODES = frozenset({"brightness", "color_temp", "hs", "xy", "rgb", "rgbw", "rgbww", "white"})


def _target(entity_id: str, allowed: frozenset[str]) -> tuple[str, str]:
    entity_id = ops.entity(entity_id)
    domain, name = entity_id.split(".", 1)
    if domain not in allowed:
        raise ops.OperationsError("This focused tool does not support that entity domain")
    if "all" in name.split("_"):
        raise ops.OperationsError("Broad all-target entities are not supported")
    return entity_id, domain


def _number(value: Any, minimum: float, maximum: float, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum or not math.isfinite(value):
        raise ops.OperationsError(f"{label} must be a finite number from {minimum} to {maximum}")
    return float(value)


def _state(entity_id: str) -> dict:
    value = ops.ha({"operation": "state", "method": "GET", "path": "api/states/" + entity_id})
    if not isinstance(value, dict) or value.get("entity_id") != entity_id or not isinstance(value.get("attributes"), dict):
        raise ops.OperationsError("Home Assistant did not return the exact requested entity")
    return value


def _services(domain: str = "") -> dict[str, set[str]]:
    value = ops.ha({"operation": "services", "domain": domain or None})
    if not isinstance(value, list):
        raise ops.OperationsError("Home Assistant returned an unsupported service inventory")
    return {row["domain"]: set(row["services"]) for row in value
            if isinstance(row, dict) and row.get("domain") in DOMAINS
            and isinstance(row.get("services"), list)
            and all(isinstance(service, str) for service in row["services"])}


def _available(value: dict) -> bool:
    domain = str(value.get("entity_id", "")).split(".")[0]
    attrs = value.get("attributes", {})
    # A scene's initial unknown state is normal; it does not indicate an offline
    # actuator. Reject entity groups, including active multi-player groups.
    groups = attrs.get("group_members", [])
    return (value.get("state") != "unavailable"
            and (domain == "scene" or value.get("state") != "unknown")
            and (domain == "scene" or not attrs.get("entity_id"))
            and not attrs.get("is_group", False)
            and not (isinstance(groups, list) and len(groups) > 1))


def _commands(value: dict, services: dict[str, set[str]]) -> list[str]:
    entity_id, domain = _target(value.get("entity_id"), DOMAINS)
    if not isinstance(value.get("attributes"), dict) or not _available(value):
        return []
    present = services.get(domain, set())
    if domain in POWER_DOMAINS:
        result = [state for state in ("on", "off") if "turn_" + state in present]
        modes = value["attributes"].get("supported_color_modes", [])
        if domain == "light" and "turn_on" in present and isinstance(modes, list) and BRIGHTNESS_MODES.intersection(str(mode) for mode in modes):
            result.append("brightness")
        return result
    if domain == "scene":
        return ["activate"] if "turn_on" in present else []
    features = value["attributes"].get("supported_features", 0)
    if isinstance(features, bool) or not isinstance(features, int) or features < 0:
        return []
    return [name for name, (service, feature) in MEDIA.items() if service in present and features & feature]


def _projection(value: dict) -> dict:
    name = value.get("attributes", {}).get("friendly_name", "")
    result = {**ops.pick(value, ("entity_id", "state", "last_changed", "last_updated")),
              "name": ops.sanitize(name) if isinstance(name, str) else ""}
    attrs = value.get("attributes", {})
    for field in ("brightness", "volume_level", "is_volume_muted", "supported_features"):
        item = attrs.get(field)
        if isinstance(item, bool) and field == "is_volume_muted":
            result[field] = item
        elif isinstance(item, (int, float)) and not isinstance(item, bool) and 0 <= item <= (1 if field == "volume_level" else 255 if field == "brightness" else 2 ** 63 - 1) and math.isfinite(item) and field != "is_volume_muted":
            result[field] = item
    return ops.sanitize(result)


def action_targets(domain: str = "", query: str = "", limit: int = 20) -> dict:
    """Find eligible explicit entities and supported commands; read-only."""
    ops.bounded_int(limit, 1, 50)
    if not isinstance(domain, str) or (domain and domain not in DOMAINS):
        raise ops.OperationsError("domain must be light, input_boolean, fan, media_player, scene or empty")
    query = ops.text(query, 120, empty=True).casefold()
    services = _services(domain)
    states = ops.ha({"operation": "action-targets", "method": "GET", "path": "api/states"})
    if not isinstance(states, list):
        raise ops.OperationsError("Home Assistant returned an unsupported state inventory")
    targets = []
    for value in states:
        if not isinstance(value, dict) or not isinstance(value.get("attributes"), dict):
            continue
        try:
            entity_id, actual_domain = _target(value.get("entity_id"), DOMAINS)
            if domain and actual_domain != domain:
                continue
            name = str(value["attributes"].get("friendly_name", ""))
            if query not in (entity_id + " " + name).casefold():
                continue
            commands = _commands(value, services)
        except ops.OperationsError:
            continue
        if commands:
            targets.append({**_projection(value), "commands": commands})
    return {"matching_count": len(targets), "limited": len(targets) > limit,
            "targets": targets[:limit], "authorization": "Each particular target/action still needs the user's request; no unattended behavior is configured",
            "limits": "Scenes and helpers may trigger existing automations; review their effects before use. HA readback is not physical acceptance"}


def _perform(entity_id: str, domain: str, command: str, service: str, data: dict) -> dict:
    # All caller-controlled validation happens in the public function before any
    # transport. Re-read existence, availability and actual services per action.
    before = _state(entity_id)
    if command not in _commands(before, _services(domain)):
        raise ops.OperationsError("Action not sent: target is unavailable, grouped or does not support this command")
    helper = ops.module(ops.HA_HELPER, "edsys_actions_ha")
    payload = helper.prepare(SimpleNamespace(operation="call-service", domain=domain,
                        service=service, data={"entity_id": entity_id, **data},
                        execute=True, return_response=False))
    request_status = "accepted"
    try:
        response = helper.execute("pve-node3", payload)
        if not isinstance(response, dict) or response.get("ok") is not True:
            request_status = "uncertain"
    except Exception:
        # A timeout/error after sending is not evidence that the action failed.
        # Do not retry, follow another route or expose the upstream exception.
        request_status = "uncertain"
    try:
        after = _projection(_state(entity_id))
        readback_status = "available"
    except Exception:
        after = None
        readback_status = "unavailable"
    return {"entity_id": entity_id, "command": command, "requested": {"service": domain + "." + service, **data},
            "status": "accepted" if request_status == "accepted" and readback_status == "available" else "uncertain",
            "request_status": request_status,
            "before": _projection(before), "after": after, "readback_status": readback_status,
            "physical_acceptance": "unverified", "automatic_retry": False,
            "note": "Immediate HA state may lag device effects; inspect before any retry. A scene readback does not verify its component devices"}


def ha_set_power(entity_id: str, state: str) -> dict:
    """Set one existing light/helper/fan on or off; no toggle or group targets."""
    entity_id, domain = _target(entity_id, POWER_DOMAINS)
    if not isinstance(state, str) or state not in {"on", "off"}:
        raise ops.OperationsError("state must be on or off")
    return _perform(entity_id, domain, state, "turn_" + state, {})


def ha_light(entity_id: str, brightness_pct: float) -> dict:
    """Set one brightness-capable light to 0..100 percent (0 turns it off)."""
    entity_id, domain = _target(entity_id, frozenset({"light"}))
    brightness = _number(brightness_pct, 0, 100, "brightness_pct")
    return _perform(entity_id, domain, "brightness", "turn_on", {"brightness_pct": brightness})


def ha_media(entity_id: str, command: str, value: float | bool | None = None) -> dict:
    """Control one existing player: play/pause/stop, volume 0..1, mute bool, on/off."""
    entity_id, domain = _target(entity_id, frozenset({"media_player"}))
    if not isinstance(command, str) or command not in MEDIA:
        raise ops.OperationsError("command must be play, pause, stop, volume, mute, on or off")
    if command == "volume":
        data = {"volume_level": _number(value, 0, 1, "value for volume")}
    elif command == "mute":
        if not isinstance(value, bool):
            raise ops.OperationsError("value for mute must be a boolean")
        data = {"is_volume_muted": value}
    else:
        if value is not None:
            raise ops.OperationsError("This command does not accept a value")
        data = {}
    return _perform(entity_id, domain, command, MEDIA[command][0], data)


def ha_scene(entity_id: str) -> dict:
    """Activate one existing scene; no scene creation or arbitrary service data."""
    entity_id, domain = _target(entity_id, frozenset({"scene"}))
    return _perform(entity_id, domain, "activate", "turn_on", {})
