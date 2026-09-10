from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'Manage-WorkLaptopAccess.ps1'
spec = importlib.util.spec_from_file_location('bundle', ROOT / 'prepare_bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = self.root / 'testkey'
        subprocess.run(['ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-f', str(self.key)], check=True)
        self.public = ' '.join(Path(str(self.key) + '.pub').read_text().split()[:2])
        self.manifest = dict(schemaVersion=1, computer='THOMPSON-LC086', user='thompson\\jclark',
                             hubAddress='100.100.10.1', laptopAddress='100.100.10.2', publicKey=self.public)

    def ps(self, code, check=True, script=SCRIPT):
        # Load function definitions only. Never execute the Windows installer on
        # the test host, and do not mock a passing live deployment.
        harness = r'''
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
$e=$null; $t=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile('__SCRIPT__',[ref]$t,[ref]$e)
if ($e.Count) { throw ($e | Out-String) }
foreach ($node in $ast.EndBlock.Statements) {
    if ($node -is [System.Management.Automation.Language.FunctionDefinitionAst]) {
        . ([scriptblock]::Create($node.Extent.Text))
    }
}
'''.replace('__SCRIPT__', str(script).replace("'", "''"))
        path = self.root / 'test.ps1'
        path.write_text(harness + '\n' + code)
        result = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(path)], text=True, capture_output=True)
        if check:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def write_manifest(self, changes=None):
        data = dict(self.manifest)
        data.update(changes or {})
        p = self.root / 'access.json'
        p.write_text(json.dumps(data))
        return p

    def test_both_scripts_parse(self):
        for script in ROOT.glob('*.ps1'):
            self.ps(f"$e=$null; $t=$null; [System.Management.Automation.Language.Parser]::ParseFile('{script}',[ref]$t,[ref]$e)|Out-Null; if ($e.Count) {{ throw ($e|Out-String) }}")

    def test_parameter_binding_does_not_require_script_root(self):
        self.ps(r'''
$paramOnly = [scriptblock]::Create($ast.ParamBlock.Extent.Text + "`n 'parameters-bound'")
if ((& $paramOnly -Action Plan) -cne 'parameters-bound') { throw 'Parameter binding failed' }
if ((& $paramOnly -Action Install -ManifestPath 'explicit.json' -EmployerApproved) -cne 'parameters-bound') { throw 'Explicit parameter binding failed' }
''')

    def test_error_receipt_captures_preflight_failure_and_nonzero_status(self):
        result = self.root / 'failure.json'
        self.ps(r'''
$code = Invoke-AccessWithReceipt -Operation { throw 'synthetic prerequisite failure' } -ResultPath '__RESULT__' -Id ('a'*32) -RequestedAction Plan
if ($code -ne 1) { throw 'Wrong failure exit code' }
'''.replace('__RESULT__', str(result)), script=ROOT / 'Invoke-WorkLaptopAccess.ps1')
        data = json.loads(result.read_text())
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['action'], 'Plan')
        self.assertEqual(data['error']['message'], 'synthetic prerequisite failure')
        self.assertEqual(data['runId'], 'a' * 32)
        self.assertEqual(data['exitCode'], 1)

    def test_success_receipt_and_stale_result_refusal(self):
        result = self.root / 'success.json'
        self.ps(r'''
$code = Invoke-AccessWithReceipt -Operation { } -ResultPath '__RESULT__' -Id ('b'*32) -RequestedAction Plan
if ($code -ne 0) { throw 'Wrong success exit code' }
'''.replace('__RESULT__', str(result)), script=ROOT / 'Invoke-WorkLaptopAccess.ps1')
        old = result.read_bytes()
        data = json.loads(old)
        self.assertEqual(data['status'], 'succeeded')
        self.assertIsNone(data['error'])
        retried = self.ps(r'''
Invoke-AccessWithReceipt -Operation { throw 'different failure' } -ResultPath '__RESULT__' -Id ('c'*32) -RequestedAction Plan
'''.replace('__RESULT__', str(result)), script=ROOT / 'Invoke-WorkLaptopAccess.ps1', check=False)
        self.assertNotEqual(retried.returncode, 0)
        self.assertEqual(result.read_bytes(), old)

    def test_valid_manifest_roundtrips(self):
        p = self.write_manifest()
        out = self.ps(f"Read-AccessManifest '{p}' | ConvertTo-Json -Compress").stdout
        self.assertEqual(json.loads(out), self.manifest)

    def test_manifest_rejects_wrong_endpoint_and_injection(self):
        cases = [dict(computer='NIMO'), dict(user='administrator'), dict(hubAddress='0.0.0.0'),
                 dict(laptopAddress='100.100.10.1'), dict(hubAddress='100.100.10.1\nPort 2222'),
                 dict(hubAddress='100.100.010.1'), dict(publicKey='restrict ' + self.public),
                 dict(publicKey=self.public + '\n' + self.public), dict(publicKey='ssh-ed25519 AAAA'),
                 dict(publicKey=self.public + ' comment')]
        for case in cases:
            with self.subTest(case=list(case)):
                p = self.write_manifest(case)
                self.assertNotEqual(self.ps(f"Read-AccessManifest '{p}'", check=False).returncode, 0)

    def test_firewall_complement_excludes_only_hub(self):
        for address, expected in [
            ('100.100.10.1', ['0.0.0.0-100.100.10.0', '100.100.10.2-255.255.255.255']),
            ('100.64.0.0', ['0.0.0.0-100.63.255.255', '100.64.0.1-255.255.255.255']),
            ('100.127.255.255', ['0.0.0.0-100.127.255.254', '100.128.0.0-255.255.255.255'])]:
            out = self.ps(f"@(Get-OtherIPv4Ranges '{address}') | ConvertTo-Json -Compress").stdout
            self.assertEqual(json.loads(out), expected)

    def test_rendered_config_passes_real_openssh_parser(self):
        sshd = shutil.which('sshd') or '/usr/sbin/sshd'
        if not Path(sshd).exists():
            self.skipTest('OpenSSH server parser unavailable')
        shutil.copyfile(self.key, self.root / 'ssh_host_ed25519_key')
        (self.root / 'ssh_host_ed25519_key').chmod(0o600)
        p = self.write_manifest()
        out = self.ps(f"$m=Read-AccessManifest '{p}'; Get-ServerConfig $m '{self.root}'").stdout
        config = self.root / 'sshd_config'
        config.write_text(out)
        proc = subprocess.run([sshd, '-T', '-f', str(config)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        for line in ['passwordauthentication no', 'authenticationmethods publickey',
                     'allowtcpforwarding no', 'allowagentforwarding no',
                     'allowusers thompson\\jclark', 'listenaddress 100.100.10.2:22']:
            self.assertIn(line, proc.stdout.splitlines())
        self.assertNotIn('Match ', out)

    def test_failed_stop_keeps_containment(self):
        result = self.ps(r'''
$script:trace = [Collections.Generic.List[string]]::new()
function Get-Service { [pscustomobject]@{Status='Running'} }
function Set-Service { $script:trace.Add('disable') }
function Stop-Service { $script:trace.Add('stop'); throw 'simulated stop failure' }
function Get-NetFirewallRule { throw 'Firewall must not be opened after failed stop' }
try { Close-ManagedAccess; throw 'Expected failed stop' } catch {
    if ($_.Exception.Message -ne 'simulated stop failure') { throw }
}
$script:trace | ConvertTo-Json -Compress
''')
        self.assertEqual(json.loads(result.stdout), ['disable', 'stop'])

    def test_revoke_closes_service_before_firewall_and_key(self):
        result = self.ps(r'''
$script:trace = [Collections.Generic.List[string]]::new()
$script:allowRule='allow'; $script:denyRule='deny'; $script:containRule='contain'; $script:keyPath='key'
$script:status='Running'
function Get-Service { [pscustomobject]@{Status=$script:status} }
function Set-Service { $script:trace.Add('disable') }
function Stop-Service { $script:trace.Add('stop'); $script:status='Stopped' }
function Get-NetFirewallRule { param($Name); [pscustomobject]@{Name=$Name} }
function Remove-NetFirewallRule { process { $script:trace.Add('remove-'+$_.Name) } }
function Disable-NetFirewallRule { process { $script:trace.Add('disable-'+$_.Name) } }
function Test-Path { $true }
function Write-PrivateText { $script:trace.Add('revoke-key') }
Close-ManagedAccess
$script:trace | ConvertTo-Json -Compress
''')
        self.assertEqual(json.loads(result.stdout), ['disable', 'stop', 'remove-allow', 'remove-deny',
                                                    'revoke-key', 'disable-OpenSSH-Server-In-TCP', 'remove-contain'])

    def test_non_windows_execution_cannot_mutate(self):
        p = subprocess.run(['pwsh', '-NoProfile', '-NonInteractive', '-File', str(SCRIPT),
                            '-Action', 'Install', '-EmployerApproved'], capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn('THOMPSON-LC086 only', p.stderr)

    def test_pin_refuses_changed_host_key(self):
        (self.root / 'windows').mkdir()
        shutil.copyfile(self.write_manifest(), self.root / 'windows' / 'access.json')
        shutil.copyfile(Path(str(self.key) + '.pub'), self.root / 'host-key.pub')
        (self.root / 'known_hosts').write_text('')
        bundle.pin(self.root)
        self.assertEqual((self.root / 'known_hosts').read_text(), '100.100.10.2 ' + self.public + '\n')
        bundle.pin(self.root)
        (self.root / 'known_hosts').write_text('100.100.10.2 changed-key\n')
        with self.assertRaises(ValueError):
            bundle.pin(self.root)

    def test_config_drift_prevents_automatic_revocation(self):
        config = self.root / 'sshd_config'
        key = self.root / 'authorized_keys'
        config.write_text('approved config')
        key.write_text('approved key')
        code = r'''
$script:configPath='__CONFIG__'; $script:keyPath='__KEY__'; $script:sshd='approved.exe'
$script:receipt = @{configHash=(Get-FileHash $script:configPath).Hash; keyHash=(Get-FileHash $script:keyPath).Hash}
function Get-CimInstance { [pscustomobject]@{PathName='approved.exe';StartName='LocalSystem'} }
Assert-ManagedFilesUnchanged
[IO.File]::WriteAllText($script:keyPath, 'subsequent IT change')
try { Assert-ManagedFilesUnchanged; throw 'Drift was accepted' } catch {
    if ($_.Exception.Message -notlike '*drifted*') { throw }
}
'''.replace('__CONFIG__', str(config)).replace('__KEY__', str(key))
        self.ps(code)

    def test_invalid_bundle_target_does_not_create_identity(self):
        for target in [self.root / 'bundle', Path('/home/jeremy/.codex/operator-checkpoints/bad name')]:
            with self.assertRaises(ValueError):
                bundle.prepare(target, self.root / 'newkey', '100.100.10.1', '100.100.10.2')
            self.assertFalse((self.root / 'newkey').exists())

    def test_ssh_user_quote_preserves_domain_backslash(self):
        config = self.root / 'client.conf'
        config.write_text('Host test\n  HostName 100.100.10.2\n  User ' + bundle.quote('thompson\\jclark') + '\n')
        out = subprocess.run(['ssh', '-G', '-F', str(config), 'test'], capture_output=True, text=True, check=True).stdout
        self.assertIn('user thompson\\jclark', out.splitlines())


if __name__ == '__main__':
    unittest.main()
