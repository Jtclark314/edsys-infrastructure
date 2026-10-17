import copy
import unittest

from full_dashboard import build
from weather_dashboard import (
    NEW_HOUSEHOLD, NEW_PLANNED, OLD_HOUSEHOLD, OLD_PLANNED,
    SCREEN_SERVICE, TARGETS, WEATHER_NOTE, extend,
)


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def section(dashboard, path):
    view = next(view for view in dashboard["views"] if view["path"] == path)
    return next(item for item in view["sections"] if any(
        card.get("heading") == TARGETS[path] for card in item["cards"]))


def fixture():
    views = []
    for path, heading in TARGETS.items():
        cards = [{"type": "heading", "heading": heading},
                 {"type": "markdown", "content": "Owner's retained card", "custom_metadata": [path]}]
        if path != "household":
            cards.append({"type": "grid", "columns": 2, "cards": [
                {"type": "button", "name": "Owner's Dismiss", "tap_action": {
                    "action": "perform-action", "perform_action": SCREEN_SERVICE,
                    "data": {"action": "clear"}}}]})
        else:
            cards.append({"type": "markdown", "content": OLD_HOUSEHOLD})
        views.append({"path": path, "owner_metadata": {"retained": True}, "sections": [
            {"type": "grid", "cards": cards, "unknown_section_option": 42},
            {"type": "grid", "cards": [{"type": "markdown", "content": "Unrelated section"}]}]})
    views.extend([
        {"path": "capabilities", "sections": [{"type": "grid", "cards": [
            {"type": "heading", "heading": "Still planned"},
            {"type": "markdown", "content": OLD_PLANNED + "\n\nKeep this owner note."}]}]},
        {"path": "owner-view", "unknown": {"all": ["retained"]}, "cards": [
            {"type": "button", "name": "Existing control", "tap_action": {
                "action": "perform-action", "perform_action": "fixture.original_service"}}]},
    ])
    return {"title": "Owner title", "views": views, "owner_dashboard_metadata": {"version": 99}}


class WeatherDashboardTests(unittest.TestCase):
    def test_preserves_input_unknown_cards_metadata_and_original_order(self):
        original = fixture()
        before = copy.deepcopy(original)
        result = extend(original)
        self.assertEqual(original, before)
        self.assertEqual(result["owner_dashboard_metadata"], before["owner_dashboard_metadata"])
        self.assertEqual(result["views"][-1], before["views"][-1])
        for index, path in enumerate(TARGETS):
            self.assertEqual(result["views"][index]["owner_metadata"], before["views"][index]["owner_metadata"])
            self.assertEqual(result["views"][index]["sections"][1], before["views"][index]["sections"][1])
            target = section(result, path)
            self.assertEqual(target["unknown_section_option"], 42)
            expected = copy.deepcopy(section(before, path)["cards"])
            for card in expected:
                if card.get("content") == OLD_HOUSEHOLD:
                    card["content"] = NEW_HOUSEHOLD
            cursor = iter(target["cards"])
            for wanted in expected:
                self.assertTrue(any(card == wanted for card in cursor))
        result["owner_dashboard_metadata"]["version"] = -1
        self.assertEqual(original, before)

    def test_repeat_is_idempotent_and_does_not_share_mutable_input(self):
        original = fixture()
        once = extend(original)
        twice = extend(once)
        self.assertEqual(twice, once)
        twice["views"][0]["owner_metadata"]["retained"] = False
        self.assertTrue(once["views"][0]["owner_metadata"]["retained"])

    def test_fixed_screen_actions_expiry_and_adjacent_dismiss(self):
        original = fixture()
        result = extend(original)
        for path in TARGETS:
            target = section(result, path)
            screen_actions = [obj for obj in walk(target) if obj.get("action") == "perform-action"]
            self.assertTrue(all(obj["perform_action"] == SCREEN_SERVICE for obj in screen_actions))
            self.assertEqual({obj["data"]["action"] for obj in screen_actions},
                             {"weather_forecast", "weather_radar", "clear"})
            weather = [obj for obj in screen_actions if obj["data"]["action"] != "clear"]
            self.assertEqual({obj["data"]["action"]: obj["data"]["duration"] for obj in weather},
                             {"weather_forecast": 90, "weather_radar": 180})
            weather_index = next(i for i, card in enumerate(target["cards"])
                                 if any(obj.get("data", {}).get("action") == "weather_forecast"
                                        for obj in walk(card)))
            nearby = target["cards"][max(0, weather_index - 1):weather_index + 1]
            self.assertTrue(any(obj.get("data", {}).get("action") == "clear" for obj in walk(nearby)))
            for obj in walk(target):
                if obj.get("type") == "button" and "weather_" in str(obj.get("tap_action")):
                    self.assertEqual(obj["hold_action"], {"action": "none"})
                    self.assertEqual(obj["double_tap_action"], {"action": "none"})

    def test_controller_links_and_notes_preserve_screen_acceptance_boundary(self):
        result = extend(fixture())
        links = [obj for obj in walk(section(result, "screen")) if obj.get("action") == "navigate"]
        self.assertEqual({obj["navigation_path"] for obj in links},
                         {"/edsys-weather/overview", "/edsys-weather/radar"})
        self.assertEqual(str(result).count("on this device"), 2)
        for path in TARGETS:
            self.assertIn(WEATHER_NOTE, str(section(result, path)))
        for text in ("HDMI 1", "NOAA KGSP", "time-stamped", "Back", "Dismiss popup",
                     "playback stay unchanged"):
            self.assertIn(text, WEATHER_NOTE)
        capabilities = next(view for view in result["views"] if view["path"] == "capabilities")
        self.assertIn("Weather forecast on TV", str(capabilities))
        self.assertIn("Regional NOAA KGSP radar on TV", str(capabilities))
        self.assertEqual(str(capabilities).count("Screen acceptance pending"), 2)
        self.assertNotIn("custom:", str(result))

    def test_only_exact_known_stale_claims_are_revised(self):
        original = fixture()
        custom = {"type": "markdown", "content": "Owner note: weather sources are not configured for another room."}
        section(original, "household")["cards"].append(custom)
        result = extend(original)
        self.assertIn(custom, section(result, "household")["cards"])
        self.assertNotIn(OLD_HOUSEHOLD, str(result))
        self.assertNotIn(OLD_PLANNED, str(result))
        self.assertIn(NEW_PLANNED + "\\n\\nKeep this owner note.", str(result))
        for deferred in ("AI visual answers", "full-motion camera inset", "real door/temperature",
                         "personal photo sources", "automatic problem notifications"):
            self.assertIn(deferred, str(result))

    def test_missing_ambiguous_or_conflicting_targets_fail_without_input_change(self):
        for modify in (
            lambda value: value["views"].pop(0),
            lambda value: value["views"].append(copy.deepcopy(value["views"][0])),
            lambda value: value["views"][0]["sections"].append(copy.deepcopy(value["views"][0]["sections"][0])),
            lambda value: section(value, "player")["cards"].append({"type":"button", "tap_action": {
                "action":"perform-action", "perform_action":SCREEN_SERVICE,
                "data":{"action":"weather_forecast", "duration":3600}}}),
        ):
            original = fixture()
            modify(original)
            before = copy.deepcopy(original)
            with self.assertRaises(ValueError):
                extend(original)
            self.assertEqual(original, before)

    def test_full_builder_applies_weather_last(self):
        binding = {"player":{"entity_id":"media_player.fixture_pi"},
                   "tv":{"entity_id":"media_player.fixture_tv"},
                   "screen_sensor":"sensor.fixture_screen",
                   "sources":{key:{"entity_id":entity} for key,entity in (
                       ("family","calendar.fixture_family"),("home","calendar.fixture_home"),
                       ("reminders","todo.fixture"),("bedside","light.fixture_bedside"))},
                   "cameras":{},"tv_sources":[]}
        result = build(binding)
        self.assertEqual(extend(result), result)
        for path in TARGETS:
            self.assertIn("weather_forecast", str(section(result, path)))
            self.assertIn("weather_radar", str(section(result, path)))


if __name__ == "__main__":
    unittest.main()
