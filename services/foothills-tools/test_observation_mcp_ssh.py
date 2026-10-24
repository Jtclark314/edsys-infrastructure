"""Transport boundary checks; no connection or production records are used."""
from __future__ import annotations

import base64
import unittest
from unittest.mock import patch

import observation_mcp_ssh as launcher


class TransportTests(unittest.TestCase):
    def test_ssh_is_noninteractive_and_uses_existing_pinned_host(self):
        args = launcher.command()
        self.assertEqual(args[0], '/usr/bin/ssh')
        self.assertIn('-T', args)
        self.assertEqual(args[-2], 'basecamp')
        for option in (
            'BatchMode=yes', 'StrictHostKeyChecking=yes',
            'ClearAllForwardings=yes', 'ForwardAgent=no',
            'ForwardX11=no', 'PermitLocalCommand=no', 'ConnectionAttempts=1',
        ):
            self.assertIn(option, args)
        self.assertFalse(any(a in args for a in ('-L', '-R', '-D', '-A', '-t')))

    def test_remote_bootstrap_uses_current_production_release_and_private_env(self):
        encoded = launcher.command()[-1].rsplit(' ', 1)[1]
        script = base64.b64decode(encoded).decode('utf-16le')
        self.assertEqual(script, launcher.REMOTE_SCRIPT)
        self.assertIn('config\\active-release.txt', script)
        self.assertIn('config\\observation-tracker.env', script)
        self.assertIn("'^[A-Za-z0-9][A-Za-z0-9._-]*$'", script)
        self.assertIn("'.venv\\Scripts\\python.exe'", script)
        self.assertIn("'integrations\\observation_mcp.py'", script)
        self.assertIn('& $python -u $entry', script)
        self.assertNotIn('Invoke-Expression', script)
        self.assertNotIn('Copy-Item', script)

    def test_optional_grant_is_a_fixed_private_file_not_a_value(self):
        script = launcher.REMOTE_SCRIPT
        self.assertIn("config\\codex-jeremy-read-grant.txt", script)
        self.assertIn("$env:OBSERVATION_MCP_GRANT_FILE = $grantFile", script)
        self.assertIn("Test-Path -LiteralPath $grantFile -PathType Leaf", script)
        self.assertNotIn("Get-Content -LiteralPath $grantFile", script)
        self.assertNotIn("Write-Output", script)

    def test_argument_injection_is_rejected_before_ssh(self):
        with patch.object(launcher.sys, 'argv', ['launcher', '--host=other']), \
                patch.object(launcher.os, 'execv') as execute:
            self.assertEqual(launcher.main(), 2)
            execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
