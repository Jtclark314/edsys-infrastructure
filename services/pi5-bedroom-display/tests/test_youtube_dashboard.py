import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from youtube_dashboard import FOLDERS, ROOT, URL, build_view, extend, remove


PI = "media_player.pi_fixture"
TV = "media_player.lg_fixture"


def walk(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def visible(value, states):
    if isinstance(value, dict):
        if value.get("type") == "conditional":
            for condition in value["conditions"]:
                state = states.get(condition["entity"], "unknown")
                if "state" in condition and state not in condition["state"]:
                    return
                if "state_not" in condition and state in condition["state_not"]:
                    return
        yield value
        for key, child in value.items():
            if key != "conditions":
                yield from visible(child, states)
    elif isinstance(value, list):
        for child in value:
            yield from visible(child, states)


def fixture():
    return {"title": "Owner title", "custom": {"keep": [1, 2]}, "views": [
        {"title": "Player", "path": "player", "custom": True, "sections": [
            {"type": "grid", "custom": "keep", "cards": [{"type": "markdown", "content": "Original"}]}]},
        {"path": "night", "cards": [{"type": "custom:fixture", "settings": {"keep": True}}]},
        {"path": "owner-tab", "unknown": {"retain": True}},
    ]}


class YouTubeDashboardTests(unittest.TestCase):
    def test_latest_dashboard_is_preserved_and_extension_is_idempotent(self):
        original = fixture()
        before = copy.deepcopy(original)
        after, counts = extend(original, PI, TV)
        self.assertEqual(original, before)
        self.assertEqual(counts, {"views_added": 1, "links_added": 1})
        self.assertEqual(after["custom"], original["custom"])
        self.assertEqual(after["views"][2:], original["views"][1:])
        player_before = copy.deepcopy(after["views"][0])
        player_before["sections"][0]["cards"].pop()
        self.assertEqual(player_before, original["views"][0])
        self.assertEqual(extend(after, PI, TV), (after, {"views_added": 0, "links_added": 0}))

    def test_only_fixed_folder_routes_and_explicit_targets_are_configured(self):
        view = build_view(PI, TV)
        folder_uris = set()
        for node in walk(view):
            action = node.get("tap_action", {})
            if action.get("action") != "perform-action":
                continue
            self.assertIn(action["target"], ({"entity_id": PI}, {"entity_id": TV}))
            data = action.get("data", {})
            if action["perform_action"] == "kodi.call_method":
                self.assertEqual(action["target"], {"entity_id": PI})
                if data["method"] == "GUI.ActivateWindow" and data.get("parameters"):
                    self.assertEqual(data["window"], "videos")
                    self.assertEqual(data["parameters"][1:], ["return"])
                    folder_uris.add(data["parameters"][0])
        self.assertEqual(folder_uris, {uri for _, _, uri in FOLDERS})
        self.assertIn(ROOT, folder_uris)
        self.assertTrue(all("/sign/" not in uri and "/config/" not in uri and "?" not in uri
                            for uri in folder_uris))
        self.assertNotIn("Addons.ExecuteAddon", str(view))

    def test_pi_disconnection_hides_pi_commands_and_retains_independent_lg(self):
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                nodes = list(visible(build_view(PI, TV), {PI: state, TV: "on"}))
                actions = [n["tap_action"] for n in nodes if "tap_action" in n]
                self.assertFalse(any(a.get("target", {}).get("entity_id") == PI for a in actions))
                self.assertFalse(any(n.get("type") == "media-control" for n in nodes))
                self.assertTrue(any(a.get("perform_action") == "media_player.volume_up" for a in actions))
                self.assertTrue(any("Pi connection unavailable" in n.get("content", "") for n in nodes))

    def test_lg_disconnection_keeps_pi_and_wake_but_hides_input_and_volume(self):
        for state in ("off", "unknown", "unavailable"):
            with self.subTest(state=state):
                nodes = list(visible(build_view(PI, TV), {PI: "playing", TV: state}))
                actions = [n["tap_action"] for n in nodes if "tap_action" in n]
                self.assertTrue(any(a.get("perform_action") == "kodi.call_method" for a in actions))
                self.assertEqual([a["perform_action"] for a in actions
                                  if a.get("target", {}).get("entity_id") == TV], ["media_player.turn_on"])
                self.assertTrue(any(a.get("navigation_path") == "/edsys-pi/lg-remote" for a in actions))

    def test_conflicting_owned_view_or_link_refuses_to_overwrite(self):
        after, _ = extend(fixture(), PI, TV)
        view_changed = copy.deepcopy(after)
        view_changed["views"][1]["title"] = "Owner-edited YouTube"
        link_changed = copy.deepcopy(after)
        link_changed["views"][0]["sections"][0]["cards"][-1]["name"] = "Owner-edited link"
        duplicate = copy.deepcopy(after)
        duplicate["views"].append(copy.deepcopy(duplicate["views"][1]))
        for current in (view_changed, link_changed, duplicate):
            with self.subTest(current=current), self.assertRaises(ValueError):
                extend(current, PI, TV)
            with self.assertRaises(ValueError):
                remove(current, PI, TV)

    def test_recovery_retains_later_unrelated_changes(self):
        original = fixture()
        after, _ = extend(original, PI, TV)
        after["custom"]["later"] = True
        after["views"][2]["new_owner_field"] = "keep"
        after["views"][0]["sections"][0]["cards"].insert(0, {"type": "markdown", "content": "Later"})
        expected = copy.deepcopy(original)
        expected["custom"]["later"] = True
        expected["views"][1]["new_owner_field"] = "keep"
        expected["views"][0]["sections"][0]["cards"].insert(0, {"type": "markdown", "content": "Later"})
        self.assertEqual(remove(after, PI, TV), expected)

    def test_invalid_bindings_or_ambiguous_parent_are_rejected(self):
        for bad in ("all", [PI], "media_player.example; other", "sensor.example", None):
            with self.subTest(binding=bad), self.assertRaises(ValueError):
                build_view(bad, TV)
        with self.assertRaises(ValueError):
            build_view(PI, PI)
        for bad in ({}, {"views": []}, {"views": [{"path": "player"}, {"path": "player"}]}):
            with self.subTest(dashboard=bad), self.assertRaises(ValueError):
                extend(bad, PI, TV)
        self.assertEqual(URL, "/edsys-pi/youtube")


if __name__ == "__main__":
    unittest.main()
