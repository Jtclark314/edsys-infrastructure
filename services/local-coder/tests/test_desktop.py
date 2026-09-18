"""Boundaries matter because model-generated tool inputs are untrusted."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("desktop", Path(__file__).parents[1] / "desktop_mcp.py")
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopBoundaries(unittest.TestCase):
    def test_operator_displays_rejected(self):
        for display in (":0", ":10", "localhost:90", ":89"):
            with self.subTest(display=display), patch.dict(desktop.os.environ,
                    {"DISPLAY": display, "EDSYS_LOCAL_DESKTOP": "1", "XAUTHORITY": "/tmp/test"}):
                with self.assertRaises(RuntimeError):
                    desktop.guard()

    def test_private_display_requires_authority_and_marker(self):
        with patch.dict(desktop.os.environ, {"DISPLAY": ":90"}, clear=True):
            with self.assertRaises(RuntimeError): desktop.guard()
        with patch.dict(desktop.os.environ, {"DISPLAY": ":90", "EDSYS_LOCAL_DESKTOP": "1", "XAUTHORITY": "/tmp/test"}):
            desktop.guard()

    def test_out_of_bounds_input_never_executes(self):
        with patch.object(desktop, "command") as command:
            for x, y in ((-1, 0), (1001, 0), (0, 1001)):
                with self.assertRaises(desktop.ToolError): desktop.desktop_click(x, y)
            command.assert_not_called()

    def test_normalized_coordinates_map_to_screen(self):
        self.assertEqual(desktop.point(0, 0), (0, 0))
        self.assertEqual(desktop.point(500, 500), (640, 400))
        self.assertEqual(desktop.point(1000, 1000), (1279, 799))
        with patch.object(desktop, "command") as command:
            desktop.desktop_click(497, 808)
            self.assertEqual(command.call_args.args[:4], ("mousemove", "--sync", "636", "646"))

    def test_key_is_not_a_command_language(self):
        with patch.object(desktop, "command") as command:
            for key in ("Return;whoami", "ctrl+a Return", "--window=0", ""):
                with self.assertRaises(desktop.ToolError): desktop.desktop_key(key)
            command.assert_not_called()

    def test_literal_typing_ends_option_parsing(self):
        with patch.object(desktop, "command") as command:
            desktop.desktop_type("--file=/etc/passwd; echo example")
            self.assertEqual(command.call_args.args[-2:], ("--", "--file=/etc/passwd; echo example"))

    def test_drag_validates_both_points(self):
        with patch.object(desktop, "command") as command:
            with self.assertRaises(desktop.ToolError): desktop.desktop_drag(1, 1, 1280, 1)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
