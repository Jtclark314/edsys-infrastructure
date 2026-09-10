"""Behavioral checks run isolated functions, never the laptop installer body."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UpdaterTests(unittest.TestCase):
    def test_isolated_behaviors(self):
        pwsh = shutil.which('pwsh')
        if not pwsh:
            self.skipTest('PowerShell unavailable')
        result = subprocess.run(
            [pwsh, '-NoProfile', '-NonInteractive', '-File',
             str(ROOT / 'tests/Test-WorkLaptopUpdater.ps1'),
             '-SourcePath', str(ROOT / 'Update-WorkLaptopCodex.ps1')],
            capture_output=True, text=True, timeout=90,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('UPDATER_TESTS_OK', result.stdout)

    def test_scope_contract(self):
        source = (ROOT / 'Update-WorkLaptopCodex.ps1').read_text()
        for forbidden in ('Enable-PSRemoting', 'New-NetFirewallRule', 'Restart-Computer',
                          'Remove-AppxPackage', 'winget upgrade --all',
                          'Set-ExecutionPolicy', 'Set-TomlScalar'):
            self.assertNotIn(forbidden, source)
        self.assertNotIn('$PSScriptRoot', source)
        self.assertIn('THOMPSON-LC086', source)
        self.assertIn('THOMPSON\\jclark', source)
        self.assertIn('NoOffer', source)
        self.assertIn('Restore-PreviousCodex.ps1', source)


if __name__ == '__main__':
    unittest.main()
