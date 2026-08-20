#!/usr/bin/env python3
"""Static and parser checks for the Windows Codex controller scripts."""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "Audit-WorkLaptopCodex.ps1"
CONFIGURE = ROOT / "Configure-WorkLaptopCodex.ps1"
RESTART = ROOT / "Restart-WorkLaptopCodex.ps1"


class WindowsCodexScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.audit = AUDIT.read_text(encoding="utf-8")
        cls.configure = CONFIGURE.read_text(encoding="utf-8")
        cls.restart = RESTART.read_text(encoding="utf-8")

    def assert_powershell_parses(self, script: Path) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh is unavailable")
        escaped = str(script).replace("'", "''")
        command = r"""
$tokens=$null
$errors=$null
[System.Management.Automation.Language.Parser]::ParseFile(
  '__SCRIPT__', [ref]$tokens, [ref]$errors
) | Out-Null
if ($errors.Count) {
  $errors | ForEach-Object { Write-Error $_.Message }
  exit 1
}
""".replace("__SCRIPT__", escaped)
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_scripts_parse(self) -> None:
        self.assert_powershell_parses(AUDIT)
        self.assert_powershell_parses(CONFIGURE)
        self.assert_powershell_parses(RESTART)

    def test_toml_editor_accepts_blank_and_empty_input(self) -> None:
        pwsh = shutil.which("pwsh")
        if not pwsh:
            self.skipTest("pwsh is unavailable")
        escaped = str(CONFIGURE).replace("'", "''")
        command = r"""
$tokens=$null
$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
  '__SCRIPT__', [ref]$tokens, [ref]$errors
)
if ($errors.Count) { exit 1 }
$functionAst=$ast.Find({
  param($node)
  $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
  $node.Name -eq 'Set-TomlScalar'
}, $true)
. ([scriptblock]::Create($functionAst.Extent.Text))
$lines=New-Object 'Collections.Generic.List[string]'
$lines.Add('')
Set-TomlScalar -Lines $lines -Section '' -Key 'model' -Literal '"gpt-5.6-sol"'
if ($lines -notcontains 'model = "gpt-5.6-sol"') { exit 2 }
$empty=New-Object 'Collections.Generic.List[string]'
Set-TomlScalar -Lines $empty -Section 'features' -Key 'apps' -Literal 'true'
if (($empty -join "`n") -ne "[features]`napps = true") { exit 3 }
""".replace("__SCRIPT__", escaped)
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_endpoint_and_outbound_only_boundary(self) -> None:
        for text in (self.audit, self.configure, self.restart):
            self.assertIn("THOMPSON-LC086", text)
            self.assertIn("THOMPSON\\jclark", text)
        for text in (self.audit, self.configure):
            self.assertIn("BatchMode=yes", text)
            self.assertIn("PasswordAuthentication=no", text)
        for forbidden in (
            "Enable-PSRemoting",
            "New-NetFirewallRule",
            "Set-NetFirewallRule",
            "Add-WindowsCapability",
            "sshd.exe",
        ):
            self.assertNotIn(forbidden, self.configure)

    def test_restart_is_unified_bounded_and_exact(self) -> None:
        for expected in (
            "Get-AppxPackage -Name 'OpenAI.Codex'",
            "Get-StartApps",
            "'ChatGPT Classic'",
            "StopTimeoutSeconds",
            "StartTimeoutSeconds",
            "codex-cli $TargetCodexVersion",
            "ChatGPT Classic remains stopped",
        ):
            self.assertIn(expected, self.restart)
        self.assertNotIn("Restart-Computer", self.restart)

    def test_audit_stays_sanitized_and_read_only(self) -> None:
        for expected in (
            "no credential values",
            "Get-SafeCodexConfig",
            "openAiApiKeyPresent",
            "No application, package, Codex setting",
        ):
            self.assertIn(expected, self.audit)
        for forbidden in (
            "Get-Content -LiteralPath (Join-Path $codexHome 'auth.json')",
            "Get-Credential",
            "Get-Clipboard",
            "Get-ChildItem -Recurse $env:USERPROFILE",
        ):
            self.assertNotIn(forbidden, self.audit)

    def test_apply_uses_exact_official_installer_and_safe_default(self) -> None:
        for expected in (
            "https://chatgpt.com/codex/install.ps1",
            "https://releases.openai.com/codex",
            "Test-ArchiveDigest",
            "Get-PackageArchiveDigest",
            "TargetCodexVersion = '0.148.0'",
            "sandbox_mode', '\"workspace-write\"'",
            "approval_policy', '\"on-request\"'",
            "sandbox', '\"elevated\"'",
            "model', '\"gpt-5.6-sol\"'",
            "web_search', '\"live\"'",
            "mcp_servers.openaiDeveloperDocs",
        ):
            self.assertIn(expected, self.configure)
        self.assertNotIn("[Environment]::SetEnvironmentVariable('OPENAI_API_KEY'", self.configure)

    def test_apply_has_private_backup_rollback_and_bounded_packages(self) -> None:
        for expected in (
            "Protect-PrivateDirectory",
            "Test-ExpectedAcl",
            "AreAccessRulesProtected",
            "System32\\icacls.exe",
            "'/inheritance:r'",
            "user-path.txt",
            "config.toml",
            "Copy-Item -LiteralPath",
            "[Environment]::SetEnvironmentVariable('Path', $userPathBefore, 'User')",
        ):
            self.assertIn(expected, self.configure)
        for package_id in (
            "GitHub.cli",
            "Microsoft.PowerShell",
            "BurntSushi.ripgrep.MSVC",
            "jqlang.jq",
            "astral-sh.uv",
            "Microsoft.VisualStudioCode",
        ):
            self.assertIn(package_id, self.configure)
        self.assertNotIn("winget upgrade --all", self.configure)

    def test_named_profiles_are_explicit(self) -> None:
        for profile in (
            "fast-iteration.config.toml",
            "safe-docs.config.toml",
            "read-only.config.toml",
            "docs-edit.config.toml",
            "deep-orchestrator.config.toml",
            "max-power.config.toml",
        ):
            self.assertIn(profile, self.configure)
        self.assertIn('model = "gpt-5.6-luna"', self.configure)
        self.assertIn('model = "gpt-5.6-terra"', self.configure)
        self.assertIn('model = "gpt-5.6-sol"', self.configure)
        self.assertIn('model_reasoning_effort = "ultra"', self.configure)
        self.assertIn('model_reasoning_effort = "max"', self.configure)
        self.assertIn('sandbox_mode = "danger-full-access"', self.configure)
        self.assertNotIn('approval_policy = "never"', self.configure)


if __name__ == "__main__":
    unittest.main()
