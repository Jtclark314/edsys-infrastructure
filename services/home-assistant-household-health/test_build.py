"""Semantic synthetic checks; no HA account, endpoint or state writes."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from types import SimpleNamespace
import unittest

from jinja2 import Environment, StrictUndefined

from build import DASHBOARD_METADATA, add_navigation, build_dashboard, reconcile_dashboard


def state(entity, value, **attributes):
    return SimpleNamespace(entity_id=entity, domain=entity.split(".")[0], state=value,
                           attributes=attributes, name=attributes.get("friendly_name", entity),
                           last_updated=datetime(2026, 1, 1, tzinfo=timezone.utc))


class States:
    def __init__(self, values):
        self.values = {item.entity_id: item for item in values}

    def __iter__(self):
        return iter(self.values.values())

    def __getitem__(self, item):
        return self.values.get(item)


def number(value):
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def render(template, values):
    env = Environment(undefined=StrictUndefined)
    env.filters["to_json"] = json.dumps
    env.filters["timestamp_custom"] = lambda value, fmt, local: datetime.fromtimestamp(value, timezone.utc).strftime(fmt)
    env.globals.update(states=States(values), is_number=number,
                       now=lambda: datetime(2026, 1, 2, tzinfo=timezone.utc),
                       as_timestamp=lambda value: value.timestamp())
    return env.from_string(template).render()


def walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk(item)


def cards(config):
    return [item for item in walk(config) if item.get("type") == "custom:auto-entities"]


def rows(config, title, values):
    match = [item for item in cards(config) if item["card"]["title"] == title]
    return json.loads(render(match[0]["filter"]["template"], values))


class HealthDashboardTests(unittest.TestCase):
    def setUp(self):
        self.bindings = {
            "repair_sensors": {"active": "sensor.active_example", "ignored": "sensor.ignored_example", "total": "sensor.total_example"},
            "excluded_entities": [{"entity": "sensor.retired_example", "reason": "retired"}],
            "watched_entities": ["media_player.endpoint_example", "sensor.missing_example"],
            "diagnostic_entities": ["sensor.diagnostic_example"],
        }

    def test_generated_rows_are_read_only_even_for_actuator_domains(self):
        config = build_dashboard(self.bindings)
        values = [state("switch.example", "unavailable"), state("button.example", "unavailable"),
                  state("script.example", "unknown"), state("update.example", "on")]
        self.assertTrue(DASHBOARD_METADATA["require_admin"])
        self.assertEqual(config["views"][0]["type"], "sections")
        first_section = config["views"][0]["sections"][0]
        self.assertEqual([item.get("title") for item in first_section["cards"][1:3]],
                         ["Current reports", "Repair reports"])
        self.assertFalse(any(section["cards"][0].get("heading") == "Repairs"
                             for section in config["views"][0]["sections"]))
        self.assertGreaterEqual(len(cards(config)), 6)
        for card in cards(config):
            self.assertFalse(card["card"]["show_header_toggle"])
            for row in json.loads(render(card["filter"]["template"], values)):
                self.assertEqual(row["type"], "simple-entity")
                self.assertEqual(row["tap_action"], {"action": "more-info"})
                self.assertEqual(row["hold_action"], {"action": "none"})
                self.assertEqual(row["double_tap_action"], {"action": "none"})
        for item in walk(config):
            if "action" in item:
                self.assertIn(item["action"], {"more-info", "none", "navigate"})
            self.assertFalse(set(item) & {"service", "service_data", "perform_action"})

    def test_unknown_missing_and_excluded_are_distinguished(self):
        bindings = deepcopy(self.bindings)
        original = deepcopy(bindings)
        config = build_dashboard(bindings)
        values = [state("sensor.retired_example", "unavailable"),
                  state("sensor.unavailable_example", "unavailable"),
                  state("media_player.endpoint_example", "unknown"),
                  state("media_player.off_example", "off")]
        self.assertEqual(bindings, original)
        self.assertEqual([row["entity"] for row in rows(config, "Unavailable now", values)], ["sensor.unavailable_example"])
        self.assertEqual([row["entity"] for row in rows(config, "Not reporting", values)], ["media_player.endpoint_example"])
        source = next(item for item in walk(config) if item.get("title") == "Selected sources" and item.get("type") == "markdown")
        text = render(source["content"], values)
        self.assertIn("not reporting", text)
        self.assertIn("missing_example — missing", text)
        self.assertIn("diagnostic_example — missing", text)
        self.assertNotIn("retired_example", text)
        summary = next(item for item in walk(config) if item.get("title") == "Current reports")
        self.assertIn("**1 unavailable**", render(summary["content"], values))

    def test_battery_rules_never_coerce_missing_voltage_nan_or_bad_range_to_low(self):
        config = build_dashboard({})
        values = [state("sensor.good_low_example", "12", device_class="battery", unit_of_measurement="%"),
                  state("sensor.zero_example", "0", device_class="battery", unit_of_measurement="%"),
                  state("sensor.good_example", "76", device_class="battery", unit_of_measurement="%"),
                  state("sensor.missing_example", "unknown", device_class="battery", unit_of_measurement="%"),
                  state("sensor.nan_example", "nan", device_class="battery", unit_of_measurement="%"),
                  state("sensor.volts_example", "3.1", device_class="battery", unit_of_measurement="V"),
                  state("sensor.negative_example", "-1", device_class="battery", unit_of_measurement="%"),
                  state("sensor.range_example", "101", device_class="battery", unit_of_measurement="%"),
                  state("sensor.battery_named_only_example", "1", unit_of_measurement="%"),
                  state("binary_sensor.low_example", "on", device_class="battery"),
                  state("binary_sensor.unknown_example", "unavailable", device_class="battery")]
        low = {row["entity"] for row in rows(config, "Low battery reports", values)}
        self.assertEqual(low, {"sensor.good_low_example", "sensor.zero_example", "binary_sensor.low_example"})
        unchecked = {row["entity"] for row in rows(config, "Battery readings to check", values)}
        self.assertEqual(unchecked, {"sensor.missing_example", "sensor.nan_example", "sensor.volts_example",
                                    "sensor.negative_example", "sensor.range_example", "binary_sensor.unknown_example"})

    def test_pending_updates_and_connection_semantics(self):
        config = build_dashboard({})
        values = [state("update.available_example", "on"), state("update.unknown_example", "unknown"),
                  state("update.none_example", "off"), state("binary_sensor.connected_example", "off", device_class="connectivity"),
                  state("binary_sensor.problem_example", "on", device_class="problem"),
                  state("light.off_example", "off"), state("binary_sensor.motion_example", "on", device_class="motion")]
        self.assertEqual([row["entity"] for row in rows(config, "Updates reported available", values)], ["update.available_example"])
        self.assertEqual({row["entity"] for row in rows(config, "Connection & problem reports", values)},
                         {"binary_sensor.connected_example", "binary_sensor.problem_example"})

    def test_normal_unknown_action_trigger_voice_and_image_states_do_not_alert(self):
        domains = ("button", "scene", "event", "notify", "stt", "tts", "conversation", "ai_task", "image")
        values = [state(f"{domain}.unused_example", "unknown") for domain in domains]
        values += [state("sensor.measurement_example", "unknown"),
                   state("button.disconnected_example", "unavailable")]
        config = build_dashboard({"watched_entities": ["button.missing_example"]})
        self.assertEqual([row["entity"] for row in rows(config, "Not reporting", values)],
                         ["sensor.measurement_example"])
        self.assertEqual([row["entity"] for row in rows(config, "Unavailable now", values)],
                         ["button.disconnected_example"])
        summary = next(item for item in walk(config) if item.get("title") == "Current reports")
        text = render(summary["content"], values)
        self.assertIn("**1 unavailable**", text)
        self.assertIn("**1 not reporting**", text)
        self.assertIn("9 unknown action", text)
        selected = next(item for item in walk(config) if item.get("title") == "Selected sources" and item.get("type") == "markdown")
        self.assertIn("button.missing_example — missing", render(selected["content"], values))
        fallback = build_dashboard({"auto_entities_available": False})
        unknown = next(item for item in walk(fallback) if item.get("title") == "Not reporting")
        basic_text = render(unknown["content"], values)
        self.assertIn("sensor.measurement_example", basic_text)
        self.assertNotIn("unused_example", basic_text)

    def test_repair_counts_missing_unknown_unavailable_and_invalid_are_not_zero(self):
        config = build_dashboard(self.bindings)
        repair = next(item for item in walk(config) if item.get("title") == "Repair reports")
        text = render(repair["content"], [state("sensor.active_example", "unknown"), state("sensor.ignored_example", "unavailable")])
        self.assertIn("not reporting", text)
        self.assertIn("unavailable", text)
        self.assertIn("not present", text)
        text = render(repair["content"], [state("sensor.active_example", "-1"), state("sensor.ignored_example", "1.2"), state("sensor.total_example", "nan")])
        self.assertEqual(text.count("invalid count"), 3)
        text = render(repair["content"], [state("sensor.active_example", "0"), state("sensor.ignored_example", "3"), state("sensor.total_example", "3")])
        self.assertIn("**Active issues:** 0", text)
        self.assertIn("Ignored issues remain issues", text)

    def test_native_fallback_uses_identical_selection_without_custom_dependency(self):
        config = build_dashboard({"auto_entities_available": False})
        self.assertFalse(cards(config))
        values = [state("sensor.example", "unknown"), state("sensor.battery_example", "unknown", device_class="battery", unit_of_measurement="%")]
        texts = [render(item["content"], values) for item in walk(config) if item.get("type") == "markdown"]
        self.assertTrue(any("sensor.example" in text for text in texts))
        self.assertFalse(any("all healthy" in text.lower() for text in texts))

    def test_fail_closed_private_binding_schema(self):
        malformed = [
            {"repair_sensors": {"active": "all"}},
            {"repair_sensors": {"active": "button.example"}},
            {"repair_sensors": {"active": "sensor.example", "total": "sensor.example"}},
            {"watched_entities": ["sensor.*"]},
            {"watched_entities": ["sensor.example", "sensor.example"]},
            {"diagnostic_entities": ["switch.example"]},
            {"excluded_entities": [{"entity": "sensor.example", "reason": "unverified"}]},
            {"excluded_entities": [{"entity": "sensor.example", "reason": []}]},
            {"excluded_entities": [{"entity": "sensor.example", "reason": "retired", "private_data": "bad"}]},
            {"watched_entities": ["sensor.example"], "excluded_entities": [{"entity": "sensor.example", "reason": "disabled"}]},
            {"battery_threshold": False}, {"battery_threshold": 0}, {"auto_entities_available": 1},
            {"unknown_field": "not accepted"},
        ]
        for bindings in malformed:
            with self.subTest(bindings=bindings), self.assertRaises(ValueError):
                build_dashboard(bindings)

    def test_navigation_is_narrow_idempotent_and_conflicts_are_refused(self):
        baseline = {"title": "Existing", "views": [
            {"path": "plans", "type": "sections", "sections": [{"type": "grid", "cards": [{"type": "markdown", "content": "Existing"}]}]},
            {"path": "other", "type": "sections", "sections": [{"type": "grid", "cards": [{"type": "tile", "entity": "switch.example"}]}]},
        ]}
        original = deepcopy(baseline)
        candidate = add_navigation(baseline)
        self.assertEqual(baseline, original)
        self.assertEqual(candidate["views"][1], baseline["views"][1])
        self.assertEqual(candidate["views"][0]["sections"][0]["cards"][:-1], baseline["views"][0]["sections"][0]["cards"])
        self.assertEqual(add_navigation(candidate), candidate)
        candidate["views"][0]["sections"][0]["cards"][-1]["name"] = "Later change"
        with self.assertRaises(ValueError):
            add_navigation(candidate)
        baseline["views"].append(deepcopy(baseline["views"][0]))
        with self.assertRaises(ValueError):
            add_navigation(baseline)
        with self.assertRaises(ValueError):
            add_navigation({"views": [{"path": "plans", "sections": []}]})

    def test_reconcile_preserves_later_unrelated_edits_and_refuses_owned_conflicts(self):
        previous = build_dashboard(self.bindings)
        desired = build_dashboard({**self.bindings, "battery_threshold": 15})
        current = deepcopy(previous)
        current["views"][0]["title"] = "Owner title"
        current["views"][0]["sections"][-1]["cards"].append({"type": "markdown", "content": "Later notes"})
        current["views"][0]["sections"].append({"type": "grid", "cards": []})
        current["views"].append({"path": "later", "type": "sections", "sections": []})
        merged = reconcile_dashboard(current, previous, desired)
        self.assertEqual(merged["views"][0]["title"], "Owner title")
        self.assertEqual(merged["views"][0]["sections"][-2:], current["views"][0]["sections"][-2:])
        self.assertEqual(merged["views"][1], current["views"][1])
        self.assertEqual(merged["views"][0]["sections"][2], desired["views"][0]["sections"][2])
        self.assertEqual(reconcile_dashboard(merged, previous, desired), merged)
        self.assertEqual(reconcile_dashboard(merged, desired, previous), current)
        conflict = deepcopy(current)
        conflict["views"][0]["sections"][2]["cards"].append({"type": "markdown", "content": "Later battery note"})
        with self.assertRaises(ValueError):
            reconcile_dashboard(conflict, previous, desired)
        self.assertNotEqual(conflict, current)
        conflict = deepcopy(current)
        conflict["views"][0]["sections"].append(deepcopy(conflict["views"][0]["sections"][2]))
        with self.assertRaises(ValueError):
            reconcile_dashboard(conflict, previous, desired)


if __name__ == "__main__":
    unittest.main()
