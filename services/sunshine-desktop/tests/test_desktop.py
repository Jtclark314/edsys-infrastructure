import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


d = load("desktop")
f = load("firewall")
SAMPLE = """Screen 0: current 4480 x 1440
DP-0 connected primary 2560x1440+1920+0 (normal left inverted right x axis y axis) 600mm x 400mm
   2560x1440 59.95*+ 144.00
HDMI-0 connected 1920x1080+0+0 (normal left inverted right x axis y axis) 600mm x 400mm
   1920x1080 60.00*+
DP-1 disconnected (normal left inverted right x axis y axis)
"""


class DesktopTests(unittest.TestCase):
    def test_layout(self):
        state = d.parse_layout(SAMPLE)
        self.assertEqual(state[0]["rate"], "59.95")
        self.assertEqual(state[0]["x"], 1920)
        self.assertFalse(state[2]["active"])

    def test_transform_validation(self):
        identity = "Transform: 1.000 0.000 0.000\n 0.000 1.000 0.000\n 0.000 0.000 1.000\n"
        with patch.object(d.subprocess, "run", return_value=SimpleNamespace(stdout=identity)):
            d.identity_scaling()
        for unsupported in ["no transform output", identity.replace("1.000", "2.000", 1)]:
            with patch.object(d.subprocess, "run", return_value=SimpleNamespace(stdout=unsupported)):
                with self.assertRaises(ValueError):
                    d.identity_scaling()

    def test_single_primary(self):
        args = d.single_args(d.parse_layout(SAMPLE))
        self.assertEqual(args[:5], ["/usr/bin/xrandr", "--output", "DP-0", "--mode", "1920x1080"])
        self.assertEqual(args[-3:], ["--output", "HDMI-0", "--off"])

    def test_restore(self):
        state = d.parse_layout(SAMPLE)
        args = d.restore_args(state, state)
        self.assertIn("1920x0", args)
        self.assertIn("2560x1440", args)
        self.assertNotIn("DP-1", args)

    def test_missing_monitor_refuses_restore(self):
        state = d.parse_layout(SAMPLE)
        missing = [dict(o, connected=False) if o["name"] == "HDMI-0" else o for o in state]
        with self.assertRaises(ValueError):
            d.restore_args(state, missing)

    def test_restore_can_recover_transient_missing_primary(self):
        saved = d.parse_layout(SAMPLE)
        current = d.parse_layout(SAMPLE.replace("primary ", ""), require_primary=False)
        self.assertIn("--primary", d.restore_args(saved, current))

    def test_reject_unknown_layout(self):
        for bad in ["", SAMPLE.replace("primary ", ""), SAMPLE.replace("(normal left", "left (normal left", 1)]:
            with self.assertRaises(ValueError):
                d.parse_layout(bad)

    def test_disconnect_event(self):
        self.assertTrue(d.disconnect_event("[2026-09-04 01:00:00]: Info: CLIENT DISCONNECTED"))
        self.assertFalse(d.disconnect_event("Debug: CLIENT DISCONNECTED"))
        self.assertFalse(d.disconnect_event("[x]: Info: CLIENT CONNECTED"))

    def test_guard_scope(self):
        config = {"lan_interface": "eth0", "tailnet_interface": "tailscale0",
                  "lan_clients": ["192.0.2.10"], "tailnet_clients": ["100.64.0.10"]}
        rules = f.render(config)
        self.assertIn("priority -20", rules)
        self.assertIn("tcp dport 47990 counter drop", rules)
        self.assertNotIn("3389", rules)
        self.assertNotIn("flush ruleset", rules)
        self.assertTrue(f.render(config, True).startswith("delete table inet edsys_sunshine\n"))

    def test_guard_rejects_broad_or_injected_peers(self):
        for address in ["192.0.2.0/24", "::1", "192.0.2.10; accept"]:
            with self.assertRaises(ValueError):
                f.render({"lan_interface": "eth0", "tailnet_interface": "tailscale0",
                          "lan_clients": [address], "tailnet_clients": ["100.64.0.10"]})


if __name__ == "__main__":
    unittest.main()
