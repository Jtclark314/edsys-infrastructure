"""Build native Lovelace config; private entity bindings and deployment stay local.

Required bindings: current_weather, forecast_weather, observation_time, and
station_sensors=[{"entity": "sensor.example", "name": "Temperature"}].
Optional bindings: alerts (sensor entity), radar (reviewed card configuration),
and stale_after_minutes (positive integer; default 30).

This emits dashboard configuration for lovelace/config/save, not an internal
.storage wrapper or sidebar registration. It performs no Home Assistant calls.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import tempfile


def _entity(value: object, domain: str, field: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(
        rf"{domain}\.[a-z0-9_]+", value
    ):
        raise ValueError(f"{field} must be a {domain} entity ID")
    return value


def _markdown(title: str, content: str, entities: list[str] | None = None) -> dict:
    card = {"type": "markdown", "title": title, "content": content}
    if entities:
        card["entity_id"] = entities
    return card


def _section(title: str, icon: str, cards: list[dict]) -> dict:
    return {"type": "grid", "cards": [
        {"type": "heading", "heading": title, "heading_style": "title",
         "icon": icon}, *cards,
    ]}


def _freshness(timestamp: str, minutes: int) -> dict:
    # now() keeps the warning current even when the source stops updating.
    content = """Source: AcuRite station via Weather Underground.

{% set stamp = as_timestamp(states('__TIMESTAMP__'), none) %}
{% if stamp is none %}
**Observation time unavailable — freshness unknown.**
{% else %}
{% set age = as_timestamp(now()) - stamp %}
Station observed: {{ stamp | timestamp_custom('%b %d, %I:%M:%S %p %Z', true) }}.
{% if age < -300 %}
**Observation time is in the future — freshness unknown.**
{% elif age > __SECONDS__ %}
**Stale observation** — {{ (age / 60) | round(0) | int }} minutes old
(more than __MINUTES__ minutes).
{% else %}
Observation age: {{ ([0, age / 60] | max) | round(0) | int }} minutes.
{% endif %}
{% endif %}"""
    for key, value in {"__TIMESTAMP__": timestamp,
                       "__SECONDS__": str(minutes * 60),
                       "__MINUTES__": str(minutes)}.items():
        content = content.replace(key, value)
    return _markdown("Station update", content, [timestamp])


def _alerts(entity: str) -> dict:
    content = """Source: National Weather Service alerts.

{% set feed = states('__ALERTS__') %}
{% set alerts = state_attr('__ALERTS__', 'Alerts') %}
{% if feed in ['unknown', 'unavailable'] %}
**Alert feed unavailable — alert status unknown.**
{% elif alerts is not sequence or alerts is string or alerts is mapping %}
**Alert details unavailable — alert status unknown.**
{% else %}
Active alerts reported by this feed: **{{ alerts | count }}**.
{% if alerts | count == 0 %}
This count describes the entries returned by the alert feed.
{% endif %}
{% for alert in alerts %}
{% if alert is mapping %}

### {{ alert.get('Event', 'Weather alert') | e }}
{{ alert.get('Headline', '') | e }}

{% if alert.get('Severity') %}Severity: {{ alert.get('Severity') | e }}.
{% endif %}
{% if alert.get('Sent') %}Issued: {{ alert.get('Sent') | e }}.
{% endif %}
{% if alert.get('Onset') %}Starts: {{ alert.get('Onset') | e }}.
{% endif %}
{% if alert.get('Expires') %}Expires: {{ alert.get('Expires') | e }}.
{% endif %}
{% if alert.get('AreasAffected') %}Affected areas: {{ alert.get('AreasAffected') | e }}.
{% endif %}
{{ alert.get('Description', '') | e }}

{{ alert.get('Instruction', '') | e }}
{% else %}
Alert details unavailable.
{% endif %}
{% endfor %}
{% endif %}""".replace("__ALERTS__", entity)
    return _markdown("Weather alerts", content, [entity])


def build_dashboard(bindings: dict) -> dict:
    """Return new JSON-compatible HA dashboard config without mutating bindings."""
    if not isinstance(bindings, dict):
        raise ValueError("bindings must be a JSON object")
    daily = _entity(bindings.get("current_weather"), "weather", "current_weather")
    forecast = _entity(bindings.get("forecast_weather"), "weather", "forecast_weather")
    observed = _entity(bindings.get("observation_time"), "sensor", "observation_time")
    minutes = bindings.get("stale_after_minutes", 30)
    if type(minutes) is not int or not 1 <= minutes <= 1440:
        raise ValueError("stale_after_minutes must be an integer from 1 to 1440")
    sensors = bindings.get("station_sensors")
    if not isinstance(sensors, list):
        raise ValueError("station_sensors must be a list of entity/name objects")
    rows = []
    for item in sensors:
        if not isinstance(item, dict):
            raise ValueError("station_sensors must contain entity/name objects")
        entity = _entity(item.get("entity"), "sensor", "station_sensors.entity")
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("station_sensors.name must be nonempty text")
        rows.append({"entity": entity, "name": name,
                     "tap_action": {"action": "more-info"},
                     "hold_action": {"action": "none"},
                     "double_tap_action": {"action": "none"}})

    station = []
    if rows:
        # Native sensor rows preserve unknown/unavailable instead of coercing zero.
        station.append({"type": "entities", "title": "Station measurements",
                        "show_header_toggle": False, "entities": rows})
    else:
        station.append(_markdown("Station measurements", "No measurements selected."))
    at_home = []
    if rows:
        at_home.append({"type": "glance", "title": "Current station measurements",
                        "columns": 2, "show_name": True, "show_state": True,
                        "entities": deepcopy(rows[:4])})
    at_home.extend([
        _freshness(observed, minutes),
        {"type": "heading", "heading": "Daily forecast · Weather Underground"},
        {"type": "weather-forecast", "entity": daily,
         "name": "Daily forecast · Weather Underground", "forecast_type": "daily",
         "show_current": False, "show_forecast": True},
    ])
    alerts = bindings.get("alerts")
    if alerts is not None:
        alerts = _entity(alerts, "sensor", "alerts")
        alert_card = _alerts(alerts)
    else:
        alert_card = _markdown("Weather alerts", "Alert feed not configured — alert status unknown.")
    sections = [
        _section("At home", "mdi:weather-partly-cloudy", at_home),
        _section("Forecast & alerts", "mdi:alert-outline", [
            alert_card,
            {"type": "heading", "heading": "Hourly forecast · NWS"},
            {"type": "markdown", "show_empty": False, "entity_id": [forecast],
             "content": ("{% if states('__FORECAST__') in ['unknown', 'unavailable'] %}"
                         "**Hourly forecast unavailable.**{% endif %}").replace(
                             "__FORECAST__", forecast)},
            {"type": "weather-forecast", "entity": forecast,
             "name": "National Weather Service", "forecast_type": "hourly",
             "show_current": False, "show_forecast": True},
        ]),
        _section("Station observations", "mdi:weather-station", station),
    ]
    views = [{"title": "Overview", "path": "overview", "type": "sections",
              "max_columns": 3, "sections": sections}]
    radar = bindings.get("radar")
    if radar is not None:
        if not isinstance(radar, dict) or not isinstance(radar.get("type"), str) or not radar["type"].strip():
            raise ValueError("radar must be a reviewed card configuration with type")
        try:
            json.dumps(radar, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("radar must be JSON compatible") from exc
        views.append({"title": "Radar", "path": "radar", "type": "panel",
                      "cards": [deepcopy(radar)]})
    return {"title": "Weather", "views": views}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bindings", type=Path, help="Private bindings JSON path")
    parser.add_argument("output", type=Path, help="Private dashboard JSON output path")
    args = parser.parse_args()
    temporary = None
    try:
        dashboard = build_dashboard(json.loads(args.bindings.read_text()))
        # Private bindings can include household names or reviewed radar URLs.
        # Publish only complete JSON, mode 0600, without following an output symlink.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(dashboard, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, args.output)
    except (OSError, ValueError) as exc:
        # Do not echo raw bindings, provider content or private path-bearing errors.
        parser.exit(1, f"Dashboard generation failed ({type(exc).__name__}).\n")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
