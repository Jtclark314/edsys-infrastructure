import copy
import unittest
from build import update_dashboard


class RemoteDashboard(unittest.TestCase):
    def setUp(self):
        self.original = {"views": [{"path": "media", "cards": [{"type": "markdown", "content": "keep"}]},
            {"path": "master-bedroom", "sections": [{"type": "grid", "cards": [{"type": "media-control", "entity": "media_player.fixture"}]},
                                                       {"type": "grid", "cards": [{"type": "media-control", "entity": "media_player.plex_fixture"}]}]}]}

    def test_extends_only_selected_view_and_preserves_original_controls(self):
        before = copy.deepcopy(self.original)
        result = update_dashboard(self.original, "media_player.fixture", ["Plex"])
        self.assertEqual(self.original, before)
        self.assertEqual(result["views"][0], before["views"][0])
        self.assertEqual(result["views"][1]["sections"][1], before["views"][1]["sections"][0])
        self.assertEqual(result["views"][1]["sections"][3], before["views"][1]["sections"][1])

    def test_all_new_actions_target_only_the_bound_tv(self):
        result = update_dashboard(self.original, "media_player.fixture", ["Plex", "Unreviewed App"])
        actions = []
        def walk(item):
            if isinstance(item, dict):
                if item.get("action") == "perform-action": actions.append(item)
                for value in item.values(): walk(value)
            elif isinstance(item, list):
                for value in item: walk(value)
        walk(result)
        self.assertGreater(len(actions), 30)
        self.assertTrue(all(a["data"]["entity_id"] == "media_player.fixture" for a in actions))
        self.assertEqual({a["data"]["button"] for a in actions if a["data"].get("button", "x").isdigit()}, set("0123456789"))
        self.assertNotIn("Unreviewed App", str(result))

    def test_duplicate_or_ambiguous_view_is_rejected(self):
        result = update_dashboard(self.original, "media_player.fixture", [])
        with self.assertRaises(ValueError): update_dashboard(result, "media_player.fixture", [])
        self.original["views"].append(self.original["views"][1])
        with self.assertRaises(ValueError): update_dashboard(self.original, "media_player.fixture", [])


if __name__ == "__main__": unittest.main()
