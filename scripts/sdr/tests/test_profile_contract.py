#!/usr/bin/env python3
from __future__ import annotations

import json
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class ProfileContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.settings = json.loads((ROOT / "openwebrx-settings.json").read_text())
        cls.bookmarks = json.loads((ROOT / "openwebrx-bookmarks.json").read_text())
        cls.device = cls.settings["sdrs"]["nesdr-smart-v5"]
        cls.profiles = cls.device["profiles"]

    def test_expected_receiver_contract(self) -> None:
        self.assertEqual(self.settings["bandplan_region"], 2)
        self.assertEqual(self.settings["map_type"], "leaflet")
        self.assertTrue(self.settings["services_enabled"])
        self.assertFalse(self.settings["mqtt_enabled"])
        self.assertFalse(self.settings["pskreporter_enabled"])
        self.assertFalse(self.settings["wsprnet_enabled"])
        self.assertEqual(next(iter(self.profiles)), "pass-aprs-2m")
        self.assertEqual(
            self.device["scheduler"]["schedule"], {"0000-0000": "pass-aprs-2m"}
        )
        idle = self.profiles["pass-aprs-2m"]
        self.assertEqual(idle["samp_rate"], 1024000)
        self.assertLess(
            idle["center_freq"] + idle["samp_rate"] / 2,
            145825000,
            "idle APRS passband must exclude the ISS APRS channel to avoid a second Dire Wolf instance",
        )
        self.assertIn("pass-2m-wide", self.profiles)

    def test_profile_coverage_and_offsets(self) -> None:
        self.assertGreaterEqual(len(self.profiles), 50)
        self.assertTrue(any(p["start_mod"] == "adsb" for p in self.profiles.values()))
        self.assertTrue(any(p["start_mod"] == "uat" for p in self.profiles.values()))
        self.assertTrue(any(p["start_mod"] == "hfdl" for p in self.profiles.values()))
        self.assertTrue(any(p["start_mod"] == "fax" for p in self.profiles.values()))
        for profile_id, profile in self.profiles.items():
            half = profile["samp_rate"] / 2
            self.assertLessEqual(profile["center_freq"] - half, profile["start_freq"], profile_id)
            self.assertGreaterEqual(profile["center_freq"] + half, profile["start_freq"], profile_id)
            if profile_id.startswith("enable-"):
                self.assertEqual(profile.get("lfo_offset"), 125000000, profile_id)
                self.assertTrue(profile["name"].startswith("HAM IT UP ENABLE"), profile_id)
            elif profile_id.startswith("pass-"):
                self.assertNotIn("lfo_offset", profile, profile_id)
                self.assertTrue(profile["name"].startswith("PASS-THROUGH"), profile_id)

    def test_bookmarks_are_unique_and_include_observatory_modes(self) -> None:
        keys = [(b["frequency"], b["modulation"]) for b in self.bookmarks]
        self.assertEqual(len(keys), len(set(keys)))
        modes = {b["modulation"] for b in self.bookmarks}
        for mode in {"packet", "eas", "ais", "acars", "vdl2", "adsb", "uat", "ism", "ft8", "wspr", "fax"}:
            self.assertIn(mode, modes)


if __name__ == "__main__":
    unittest.main()
