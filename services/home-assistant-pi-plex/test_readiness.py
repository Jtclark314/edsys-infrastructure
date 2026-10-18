import copy
import unittest

from build import button
from full_dashboard import build
from readiness import PI_MESSAGE, TV_MESSAGE, repair_dashboard


PI = "media_player.pi_fixture"
TV = "media_player.tv_fixture"
OTHER = "media_player.other_room"


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def visible(value, states):
    """Evaluate the native state conditions used by these fixture dashboards."""
    if isinstance(value, dict):
        if value.get("type") == "conditional":
            for condition in value["conditions"]:
                state = states.get(condition["entity"], "unknown")
                for key in ("state", "state_not"):
                    if key not in condition:
                        continue
                    expected = condition[key]
                    matches = state in expected if isinstance(expected, list) else state == expected
                    if matches != (key == "state"):
                        return
        yield value
        for key, child in value.items():
            if key != "conditions":
                yield from visible(child, states)
    elif isinstance(value, list):
        for child in value:
            yield from visible(child, states)


def taps(nodes):
    return [node["tap_action"] for node in nodes if isinstance(node.get("tap_action"), dict)]


def binding():
    return {"player": {"entity_id": PI}, "tv": {"entity_id": TV},
            "screen_sensor": "sensor.bedroom_pi_screen",
            "sources": {key: {"entity_id": entity} for key, entity in (
                ("family", "calendar.family_fixture"), ("home", "calendar.home_fixture"),
                ("reminders", "todo.fixture"), ("bedside", "light.bedside_fixture"))},
            "cameras": {}, "tv_sources": ["Plex", "PC"]}


class ReadinessTests(unittest.TestCase):
    def test_new_dashboard_disconnected_pi_hides_commands_but_preserves_lg_and_navigation(self):
        dashboard = build(binding())
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                nodes = list(visible(dashboard, {PI: state, TV: "on"}))
                actions = taps(nodes)
                self.assertFalse(any(a.get("perform_action") == "kodi.call_method" for a in actions))
                self.assertFalse(any(a.get("perform_action") in (
                    "media_player.media_play", "media_player.media_pause", "media_player.media_stop")
                    and a.get("target", {}).get("entity_id") == PI for a in actions))
                self.assertFalse(any(n.get("type") == "media-control" and n.get("entity") == PI for n in nodes))
                self.assertTrue(any(a.get("perform_action") == "media_player.volume_up" for a in actions))
                self.assertTrue(any(a.get("navigation_path") == "/edsys-pi/lg-remote" for a in actions))
                self.assertTrue(any(n.get("content") == PI_MESSAGE for n in nodes))
                # Cancellation is intentionally available even if the Pi is offline.
                self.assertTrue(any(a.get("data", {}).get("action") == "sleep_cancel" for a in actions))

    def test_new_dashboard_lg_off_does_not_hide_playing_pi_or_wake_and_recovery_links(self):
        dashboard = build(binding())
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                nodes = list(visible(dashboard, {PI: "playing", TV: state}))
                actions = taps(nodes)
                self.assertTrue(any(a.get("perform_action") == "kodi.call_method" for a in actions))
                self.assertTrue(any(a.get("perform_action") == "media_player.media_pause"
                                    and a.get("target", {}).get("entity_id") == PI for a in actions))
                self.assertFalse(any(a.get("perform_action") in (
                    "media_player.volume_up", "media_player.volume_down", "media_player.volume_mute") for a in actions))
                self.assertFalse(any(a.get("data", {}).get("command") == "tv/switchInput" for a in actions))
                self.assertFalse(any(n.get("type") == "tile" and n.get("entity") == TV
                                     and n.get("features") for n in nodes))
                self.assertTrue(any(a.get("perform_action") == "media_player.turn_on" for a in actions))
                self.assertTrue(any(a.get("navigation_path") == "/edsys-pi/player" for a in actions))
                self.assertTrue(any(n.get("content") == TV_MESSAGE for n in nodes))

    def test_idle_and_active_pi_keep_navigation_and_all_original_payloads(self):
        dashboard = build(binding())
        for state in ("idle", "playing", "paused"):
            nodes = list(visible(dashboard, {PI: state, TV: "on"}))
            actions = taps(nodes)
            open_plex = next(a for a in actions if a.get("data", {}).get("method") == "Addons.ExecuteAddon")
            self.assertEqual(open_plex, {"action": "perform-action", "perform_action": "kodi.call_method",
                                        "target": {"entity_id": PI},
                                        "data": {"method": "Addons.ExecuteAddon", "addonid": "script.plexmod"}})
            self.assertTrue(any(a.get("perform_action") == "media_player.volume_mute" for a in actions))
            self.assertFalse(any(n.get("content") in (PI_MESSAGE, TV_MESSAGE) for n in nodes))
        self.assertIn("native TV apps", str(dashboard))
        self.assertIn("Pi Player arrows", str(dashboard))

    def test_latest_media_transform_is_scoped_nested_preserving_and_idempotent(self):
        volume = {"type": "tile", "entity": TV, "name": "Existing volume",
                  "features": [{"type": "media-player-volume-slider"}], "custom_metadata": {"keep": True}}
        legacy_pi = {"type": "conditional", "conditions": [
            {"condition": "state", "entity": PI, "state_not": ["unavailable", "unknown"]}],
            "card": {"type": "grid", "cards": [button("Pause", "pause", "media_player.media_pause", PI)]}}
        other_section = {"type": "grid", "cards": [
            {"type": "heading", "heading": "Browse on the TV"},
            button("Other volume", "volume-plus", "media_player.volume_up", OTHER)]}
        original = {"title": "Latest title", "custom": ["retained"], "views": [
            {"path": "media", "sections": [{"type": "grid", "cards": [
                {"type": "vertical-stack", "cards": [volume]},
                button("Wake TV", "power", "media_player.turn_on", TV)]}, other_section]},
            {"path": "master-bedroom", "sections": [{"type": "grid", "cards": [
                {"type": "button", "name": "Older volume button", "tap_action": {
                    "action": "call-service", "service": "webostv.button",
                    "service_data": {"entity_id": TV, "button": "VOLUMEUP"}}}]}]},
            {"path": "bedroom-pi", "sections": [{"type": "grid", "cards": [legacy_pi]}]},
            {"path": "unknown-user-view", "cards": [copy.deepcopy(volume), copy.deepcopy(legacy_pi)]}]}
        before = copy.deepcopy(original)
        candidate, counts = repair_dashboard(original, PI, TV, dashboard="edsys-media")
        self.assertEqual(original, before)
        self.assertEqual(candidate["custom"], before["custom"])
        self.assertEqual(candidate["views"][-1], before["views"][-1])
        self.assertEqual(candidate["views"][0]["sections"][1], other_section)
        self.assertEqual(taps(walk(candidate)), taps(walk(original)))
        self.assertEqual(next(n for n in walk(candidate) if n.get("name") == "Existing volume"), volume)
        self.assertEqual(counts["views_changed"], 3)
        self.assertEqual(counts["pi_guards_updated"], 1)
        self.assertEqual(counts["tv_controls_guarded"], 2)
        self.assertEqual(counts["status_cards_added"], 3)
        again, again_counts = repair_dashboard(candidate, PI, TV, dashboard="edsys-media")
        self.assertEqual(again, candidate)
        self.assertTrue(all(value == 0 for value in again_counts.values()))
        live_nodes = list(visible({"views": candidate["views"][:3]}, {PI: "off", TV: "off", OTHER: "on"}))
        self.assertFalse(any(a.get("data", {}).get("method") for a in taps(live_nodes)))
        self.assertFalse(any(a.get("perform_action") == "media_player.media_pause" for a in taps(live_nodes)))
        self.assertTrue(any(a.get("perform_action") == "media_player.turn_on" for a in taps(live_nodes)))
        self.assertTrue(any(n.get("name") == "Other volume" for n in live_nodes))

    def test_existing_complete_guard_is_not_nested_or_replaced(self):
        existing = {"type": "conditional", "conditions": [
            {"condition": "state", "entity": TV, "state_not": state}
            for state in ("off", "unknown", "unavailable")], "card": {
                "type": "vertical-stack", "cards": [button("Volume", "volume-plus", "media_player.volume_up", TV)]}}
        original = {"views": [{"path": "player", "cards": [existing]}]}
        candidate, counts = repair_dashboard(original, PI, TV, dashboard="edsys-pi")
        self.assertEqual(candidate["views"][0]["cards"][0], existing)
        self.assertEqual(counts["tv_controls_guarded"], 0)
        self.assertEqual(counts["status_cards_added"], 1)

    def test_ambiguous_targets_unknown_views_and_unrelated_actions_are_untouched(self):
        ambiguous = button("Many targets", "volume-plus", "media_player.volume_up", [TV, OTHER])
        mixed = button("Mixed selectors", "volume-plus", "media_player.volume_up", TV)
        mixed["tap_action"]["target"]["device_id"] = "other-device-fixture"
        mixed_data = button("Mixed data selectors", "volume-plus", "media_player.volume_up", TV, area_id="other-area")
        unrelated = button("Different service", "help", "fixture.inspect", TV)
        original = {"views": [{"path": "media", "cards": [ambiguous, mixed, mixed_data, unrelated]},
                              {"path": "custom", "cards": [button("Volume", "volume-plus", "media_player.volume_up", TV)]}]}
        candidate, counts = repair_dashboard(original, PI, TV, dashboard="edsys-media")
        self.assertEqual(candidate, original)
        self.assertTrue(all(value == 0 for value in counts.values()))
        with self.assertRaises(ValueError):
            repair_dashboard(original, PI, TV, dashboard="unreviewed")
        with self.assertRaises(ValueError):
            repair_dashboard(original, "all", TV, dashboard="edsys-media")
        with self.assertRaises(ValueError):
            repair_dashboard(original, TV, TV, dashboard="edsys-media")

    def test_readiness_transform_of_current_full_build_is_already_complete(self):
        dashboard = build(binding())
        candidate, counts = repair_dashboard(dashboard, PI, TV, dashboard="edsys-pi")
        self.assertEqual(candidate, dashboard)
        self.assertTrue(all(value == 0 for value in counts.values()))

    def test_attribute_dynamic_and_conflicting_conditions_do_not_count_as_connection_guards(self):
        conditions = [
            {"condition": "state", "entity": TV, "attribute": "source", "state_not": ["off", "unknown", "unavailable"]},
            {"condition": "state", "entity": TV, "entity_id": OTHER, "state_not": ["off", "unknown", "unavailable"]},
            {"condition": "state", "entity": TV, "state": "off", "state_not": ["off", "unknown", "unavailable"]},
            {"condition": "state", "entity": TV, "state": "sensor.dynamic_fixture"},
        ]
        for condition in conditions:
            with self.subTest(condition=condition):
                original = {"views": [{"path": "media", "cards": [{"type": "conditional",
                    "conditions": [condition], "card": button("Volume", "volume-plus", "media_player.volume_up", TV)}]}]}
                candidate, counts = repair_dashboard(original, PI, TV, dashboard="edsys-media")
                self.assertEqual(candidate["views"][0]["cards"][0]["conditions"], [condition])
                self.assertEqual(counts["tv_controls_guarded"], 1)


if __name__ == "__main__":
    unittest.main()
