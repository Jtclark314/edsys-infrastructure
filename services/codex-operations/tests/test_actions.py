"""Action boundaries: validate before transport; do not retry ambiguous writes."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import actions
import operations as ops


def state(entity="light.desk", current="on", **attrs):
    return {"entity_id": entity, "state": current, "last_updated": "today",
            "attributes": {"friendly_name": "Example", **attrs}}


def services(domain, *names):
    return [{"domain": domain, "services": list(names)}]


class BoundaryTests(unittest.TestCase):
    def test_invalid_inputs_rejected_before_transport(self):
        attempts = [
            (actions.ha_set_power, ("all", "on")),
            (actions.ha_set_power, ("light.all_lights", "off")),
            (actions.ha_set_power, ("light.a,light.b", "on")),
            (actions.ha_set_power, (["light.a"], "on")),
            (actions.ha_set_power, ("switch.heater", "on")),
            (actions.ha_set_power, ("lock.door", "off")),
            (actions.ha_set_power, ("climate.house", "off")),
            (actions.ha_set_power, ("light.desk", "toggle")),
            (actions.ha_set_power, ("light.desk", True)),
            (actions.ha_scene, ("script.test",)),
            (actions.ha_scene, ("scene.all",)),
            (actions.ha_media, ("media_player.tv", "play_media", "https://example.com")),
            (actions.ha_media, ("media_player.tv", "play", "https://example.com")),
            (actions.ha_media, ("media_player.tv", "volume", True)),
            (actions.ha_media, ("media_player.tv", "volume", -0.1)),
            (actions.ha_media, ("media_player.tv", "volume", 1.1)),
            (actions.ha_media, ("media_player.tv", "volume", float("nan"))),
            (actions.ha_media, ("media_player.tv", "volume", float("inf"))),
            (actions.ha_media, ("media_player.tv", "mute", 1)),
            (actions.ha_light, ("light.desk", True)),
            (actions.ha_light, ("light.desk", -1)),
            (actions.ha_light, ("light.desk", 101)),
            (actions.ha_light, ("light.desk", 10 ** 1000)),
            (actions.ha_light, ("light.desk", float("nan"))),
            (actions.action_targets, ("switch",)),
            (actions.action_targets, ("", "password=private")),
            (actions.action_targets, ("", "", True)),
        ]
        for function, args in attempts:
            with self.subTest(function=function.__name__, args=args), patch.object(ops, "ha") as read, patch.object(ops, "module") as load:
                with self.assertRaises(ops.OperationsError):
                    function(*args)
                read.assert_not_called()
                load.assert_not_called()

    def test_targets_filter_actual_feature_support_and_private_attributes(self):
        inventory = [state(supported_color_modes=["brightness"], password="private"),
                     state("media_player.speaker", supported_features=12, volume_level=0.2),
                     state("lock.front", current="locked"),
                     state("light.unavailable", current="unavailable"),
                     state("light.group", entity_id=["light.desk"]),
                     state("light.all_lights"),
                     state("scene.evening", current="unknown")]
        available = services("light", "turn_on", "turn_off") + services("media_player", "volume_set", "volume_mute", "media_play") + services("scene", "turn_on")
        with patch.object(ops, "ha", side_effect=[available, inventory]) as read:
            result = actions.action_targets(limit=2)
        self.assertEqual(result["matching_count"], 3)
        self.assertTrue(result["limited"])
        self.assertEqual(result["targets"][0]["commands"], ["on", "off", "brightness"])
        self.assertEqual(result["targets"][1]["commands"], ["volume", "mute"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(read.call_args.args[0], {"operation": "action-targets", "method": "GET", "path": "api/states"})

    def test_target_query_and_domain_apply_before_limit(self):
        inventory = [state("light.first"), state("fan.second"), state("light.second")]
        with patch.object(ops, "ha", side_effect=[services("light", "turn_on"), inventory]):
            result = actions.action_targets("light", "second", 1)
        self.assertEqual(result["matching_count"], 1)
        self.assertEqual(result["targets"][0]["entity_id"], "light.second")

    def test_unavailable_unsupported_grouped_or_mismatched_entity_never_sends(self):
        attempts = [state(current="unavailable"), state(current="unknown"), state("light.other"),
                    state(entity_id=["light.other"]), state(is_group=True)]
        for before in attempts:
            with self.subTest(before=before), patch.object(ops, "ha", side_effect=[before, services("light", "turn_on")]), patch.object(ops, "module") as load:
                with self.assertRaises(ops.OperationsError):
                    actions.ha_set_power("light.desk", "on")
                load.assert_not_called()
        with patch.object(ops, "ha", side_effect=[state(), services("light", "turn_off")]), patch.object(ops, "module") as load:
            with self.assertRaises(ops.OperationsError):
                actions.ha_set_power("light.desk", "on")
            load.assert_not_called()

    def test_media_requires_live_feature_even_when_service_exists(self):
        with patch.object(ops, "ha", side_effect=[state("media_player.speaker", supported_features=0), services("media_player", "media_play")]), patch.object(ops, "module") as load:
            with self.assertRaises(ops.OperationsError):
                actions.ha_media("media_player.speaker", "play")
            load.assert_not_called()


class ActionTransportTests(unittest.TestCase):
    def setUp(self):
        # Use the actual established helper's prepare(), so expected service
        # payloads exercise its deliberate-execute and explicit-target gate.
        self.helper = ops.module(ops.HA_HELPER, "edsys_actions_test_helper")
        self.helper.execute = Mock(return_value={"ok": True, "result": [{"password": "private-upstream"}]})

    def run_action(self, function, args, before, inventory, after=None):
        with patch.object(ops, "ha", side_effect=[before, inventory, after or before]) as read, patch.object(ops, "module", return_value=self.helper):
            result = function(*args)
        self.assertEqual(read.call_count, 3)
        return result

    def test_explicit_power_payload_and_readback_are_projected(self):
        result = self.run_action(actions.ha_set_power, ("input_boolean.fixture", "on"),
                                 state("input_boolean.fixture", "off"), services("input_boolean", "turn_on"), state("input_boolean.fixture", "on", secret="private"))
        self.helper.execute.assert_called_once_with("pve-node3", {"operation": "call-service", "method": "POST", "path": "api/services/input_boolean/turn_on", "data": {"entity_id": "input_boolean.fixture"}})
        self.assertEqual(result["before"]["state"], "off")
        self.assertEqual(result["after"]["state"], "on")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["physical_acceptance"], "unverified")
        self.assertFalse(result["automatic_retry"])
        self.assertNotIn("private", json.dumps(result))

    def test_fan_payload_has_only_exact_target(self):
        self.run_action(actions.ha_set_power, ("fan.fixture", "off"), state("fan.fixture"), services("fan", "turn_off"))
        self.assertEqual(self.helper.execute.call_args.args[1]["data"], {"entity_id": "fan.fixture"})
        self.assertEqual(self.helper.execute.call_args.args[1]["path"], "api/services/fan/turn_off")

    def test_brightness_payload_and_readback(self):
        result = self.run_action(actions.ha_light, ("light.desk", 35), state(supported_color_modes=["brightness"]), services("light", "turn_on"), state(brightness=89, supported_color_modes=["brightness"]))
        self.assertEqual(self.helper.execute.call_args.args[1]["data"], {"entity_id": "light.desk", "brightness_pct": 35.0})
        self.assertEqual(result["after"]["brightness"], 89)

    def test_onoff_only_light_cannot_accept_brightness(self):
        with patch.object(ops, "ha", side_effect=[state(supported_color_modes=["onoff"]), services("light", "turn_on")]), patch.object(ops, "module") as load:
            with self.assertRaises(ops.OperationsError):
                actions.ha_light("light.desk", 50)
            load.assert_not_called()

    def test_media_payloads_are_fixed_and_typed(self):
        cases = [("volume", 0.3, "volume_set", {"volume_level": 0.3}),
                 ("mute", False, "volume_mute", {"is_volume_muted": False}),
                 ("play", None, "media_play", {}), ("pause", None, "media_pause", {}),
                 ("stop", None, "media_stop", {}), ("on", None, "turn_on", {}), ("off", None, "turn_off", {})]
        for command, value, service, data in cases:
            self.helper.execute.reset_mock()
            with self.subTest(command=command):
                result = self.run_action(actions.ha_media, ("media_player.fixture", command, value),
                                        state("media_player.fixture", supported_features=32767), services("media_player", service),
                                        state("media_player.fixture", supported_features=32767, volume_level=0.3, is_volume_muted=False, media_content_id="private"))
                payload = self.helper.execute.call_args.args[1]
                self.assertEqual(payload["path"], "api/services/media_player/" + service)
                self.assertEqual(payload["data"], {"entity_id": "media_player.fixture", **data})
                self.assertEqual(result["after"]["supported_features"], 32767)
                self.assertNotIn("private", json.dumps(result))

    def test_existing_scene_unknown_state_is_eligible(self):
        result = self.run_action(actions.ha_scene, ("scene.fixture",), state("scene.fixture", "unknown"), services("scene", "turn_on"))
        self.assertEqual(self.helper.execute.call_args.args[1]["path"], "api/services/scene/turn_on")
        self.assertIn("component devices", result["note"])

    def test_ambiguous_write_never_retries_and_returns_available_readback(self):
        self.helper.execute.side_effect = TimeoutError("token=private")
        result = self.run_action(actions.ha_set_power, ("light.desk", "off"), state(), services("light", "turn_off"), state(current="off"))
        self.helper.execute.assert_called_once()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["request_status"], "uncertain")
        self.assertEqual(result["after"]["state"], "off")
        self.assertNotIn("private", json.dumps(result))

    def test_postread_failure_preserves_api_acceptance_but_status_uncertain(self):
        with patch.object(ops, "ha", side_effect=[state(), services("light", "turn_off"), RuntimeError("password=private")]), patch.object(ops, "module", return_value=self.helper):
            result = actions.ha_set_power("light.desk", "off")
        self.helper.execute.assert_called_once()
        self.assertEqual(result["request_status"], "accepted")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["readback_status"], "unavailable")
        self.assertIsNone(result["after"])
        self.assertNotIn("private", json.dumps(result))

    def test_invalid_write_ack_is_uncertain_without_retry(self):
        self.helper.execute.return_value = {"ok": False, "error": "private"}
        result = self.run_action(actions.ha_set_power, ("light.desk", "off"), state(), services("light", "turn_off"))
        self.helper.execute.assert_called_once()
        self.assertEqual(result["status"], "uncertain")
        self.assertNotIn("private", json.dumps(result))


if __name__ == "__main__":
    unittest.main()
