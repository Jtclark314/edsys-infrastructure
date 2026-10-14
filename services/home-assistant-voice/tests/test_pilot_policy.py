"""Regression phrases catch wrong-room, negation and media-title confusion."""
import importlib.util
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1] / "custom_components/edsys_voice_knowledge/pilot_policy.py"
spec = importlib.util.spec_from_file_location("pilot_policy", path)
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)


class PilotPhrases(unittest.TestCase):
    def test_owner_selected_main_light_commands(self):
        for text, action in [("Turn on the Gadget Lab lights", "on"), ("Lights out in the lab", "off"),
                             ("Could you please switch off the Gadget Lab light", "off")]:
            with self.subTest(text=text):
                policy.check_control(text, "gadget_lab_light", action, None)

    def test_wrong_mapping_and_passive_questions_cannot_control(self):
        phrases = ["Turn on the desk downlights in the Gadget Lab", "Turn on the garage lights",
                   "Why are the Gadget Lab lights off", "Don't turn off the Gadget Lab lights",
                   "Turn off the Gadget Lab lights tomorrow", "Turn off the Gadget Lab lights and the TV",
                   "If I leave, turn off the Gadget Lab lights", "Turn off the Gadget Lab lights in 30 seconds"]
        for text in phrases:
            with self.subTest(text=text), self.assertRaises(policy.PilotRefusal):
                policy.check_control(text, "gadget_lab_light", "off", None)

    def test_explicit_bedroom_and_operation_are_required(self):
        for text in ["Mute the bedroom TV", "Mute the secondary bedroom TV", "Why is the Master Bedroom TV muted",
                     "Mute the Master Bedroom TV", "Unmute the Master Bedroom TV"]:
            action = "unmute" if text.startswith("Mute") else "mute"
            with self.subTest(text=text), self.assertRaises(policy.PilotRefusal):
                policy.check_control(text, "master_bedroom_tv", action, None)
        policy.check_control("Mute the main bedroom television", "master_bedroom_tv", "mute", None)

    def test_volume_matches_actual_utterance(self):
        policy.check_control("Set the Master Bedroom TV volume to 25 percent", "master_bedroom_tv", "set_volume", .25)
        for value in [None, .8, 25, -1]:
            with self.subTest(value=value), self.assertRaises(policy.PilotRefusal):
                policy.check_control("Set the Master Bedroom TV volume to 25 percent", "master_bedroom_tv", "set_volume", value)

    def test_resume_does_not_substitute_for_new_title(self):
        policy.check_control("Resume Plex in the Master Bedroom", "master_bedroom_plex", "resume", None)
        for text in ["Play Titanic on Master Bedroom Plex", "Play a movie in the Master Bedroom on Plex",
                     "Pause Plex in the Master Bedroom"]:
            with self.subTest(text=text), self.assertRaises(policy.PilotRefusal):
                policy.check_control(text, "master_bedroom_plex", "resume", None)

    def test_camera_choice_and_display_are_explicit(self):
        cameras = {"front_porch": {"aliases": ["front porch"]}, "back_patio": {"aliases": ["back patio"]},
                   "driveway": {"aliases": ["driveway"]}, "driveway_tracking": {"aliases": ["driveway tracking"]}}
        self.assertEqual(policy.choose_camera("Bring up the front porch camera", cameras), "front_porch")
        self.assertEqual(policy.choose_camera("Show the driveway tracking camera", cameras), "driveway_tracking")
        for text in ["Show the back camera", "Turn off the front porch camera", "Show the front porch camera on the TV",
                     "Don't show the front porch camera"]:
            with self.subTest(text=text), self.assertRaises(policy.PilotRefusal):
                policy.choose_camera(text, cameras)
        with self.assertRaises(policy.PilotRefusal):
            policy.choose_camera("Show the front porch camera", cameras, "back_patio")


if __name__ == "__main__":
    unittest.main()
