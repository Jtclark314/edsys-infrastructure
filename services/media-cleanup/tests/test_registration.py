"""Migration touches only this server's tool allowlist in a disposable config."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest import mock

import register


class RegistrationTests(unittest.TestCase):
    def test_existing_four_tool_registration_migrates_without_other_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            launcher = root / ".local/bin/edsys-media-cleanup-mcp"
            launcher.parent.mkdir(parents=True)
            launcher.write_text("fixture")
            config = root / ".codex/config.toml"
            config.parent.mkdir()
            original = ('model = "fixture-model"\n# preserved comment\n'
                        '[mcp_servers.unrelated]\ncommand = "/fixture/unrelated"\n'
                        '[mcp_servers.edsys-media-cleanup]\ncommand = "' + str(launcher) + '"\n'
                        'enabled = true\nstartup_timeout_sec = 20\ntool_timeout_sec = 900\n'
                        'enabled_tools = ["media_find", "media_plan", "media_execute", "media_status"]\n'
                        '[mcp_servers.edsys-media-cleanup.env]\nPYTHONUNBUFFERED = "1"\n')
            config.write_text(original)
            expected = copy.deepcopy(tomllib.loads(original))
            expected["mcp_servers"]["edsys-media-cleanup"]["enabled_tools"] = ["media_delete", "media_status"]
            with mock.patch.object(register.Path, "home", return_value=root), contextlib.redirect_stdout(io.StringIO()):
                register.main()
                first = config.read_text()
                register.main()
            self.assertEqual(tomllib.loads(first), expected)
            self.assertIn("# preserved comment", first)
            self.assertEqual(first, config.read_text())
            self.assertEqual(1, len(list((root / ".local/state/edsys-media-cleanup/registration-backups").glob("*.toml"))))
