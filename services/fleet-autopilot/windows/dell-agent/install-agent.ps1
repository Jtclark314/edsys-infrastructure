[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundleRoot,
    [Parameter(Mandatory = $true)][ValidatePattern('^[0-9a-fA-F]{64}$')][string]$TrustedSignerSha256,
    [string]$InstallRoot = (Join-Path $env:ProgramData 'EdSys\FleetAgent'),
    [switch]$AllowMutations,
    [switch]$RunAsSystem
)

$ErrorActionPreference = 'Stop'
$manifestPath = Join-Path $BundleRoot 'bundle-manifest.json'
if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw 'Signed bundle manifest is missing.' }
$allowedSigners = Join-Path $BundleRoot 'allowed_signers'
$signaturePath = Join-Path $BundleRoot 'bundle-manifest.json.sig'
if (-not (Test-Path -LiteralPath $allowedSigners -PathType Leaf) -or -not (Test-Path -LiteralPath $signaturePath -PathType Leaf)) { throw 'Bundle signature material is missing.' }
$actualSignerHash = (Get-FileHash -LiteralPath $allowedSigners -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualSignerHash -cne $TrustedSignerSha256.ToLowerInvariant()) { throw 'Bundle signer trust pin does not match.' }
# cmd.exe redirects the manifest file byte-for-byte. PowerShell 5.1's text
# pipeline changes signed bytes; Windows OpenSSH can also hang on a managed pipe.
$sshKeygen = (Get-Command ssh-keygen.exe -ErrorAction Stop).Source
foreach ($value in @($sshKeygen, $allowedSigners, $signaturePath, $manifestPath)) {
    if ($value -match '["%&|<>^!\r\n]') { throw 'Bundle paths contain unsupported shell characters.' }
}
$startInfo = New-Object Diagnostics.ProcessStartInfo
$startInfo.FileName = Join-Path $env:SystemRoot 'System32\cmd.exe'
$startInfo.Arguments = '/d /s /c ""' + $sshKeygen + '" -Y verify -f "' + $allowedSigners + '" -I edsys-fleet-release -n file -s "' + $signaturePath + '" < "' + $manifestPath + '""'
$startInfo.UseShellExecute = $false
$startInfo.CreateNoWindow = $true
$startInfo.RedirectStandardOutput = $true
$startInfo.RedirectStandardError = $true
$verifier = [Diagnostics.Process]::Start($startInfo)
try {
    if (-not $verifier.WaitForExit(30000)) {
        & (Join-Path $env:SystemRoot 'System32\taskkill.exe') /PID $verifier.Id /T /F 2>&1 | Out-Null
        throw 'Bundle signature verification timed out.'
    }
    if ($verifier.ExitCode -ne 0) { throw 'Bundle Ed25519 signature verification failed.' }
}
finally { $verifier.Dispose() }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
foreach ($file in @($manifest.files)) {
    $path = Join-Path $BundleRoot ([string]$file.path)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Bundle file is missing: $($file.path)" }
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -cne [string]$file.sha256) { throw "Bundle hash mismatch: $($file.path)" }
}

New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().User
$system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetAccessRuleProtection($true, $false)
$inheritance = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
$none = [Security.AccessControl.PropagationFlags]::None
$allow = [Security.AccessControl.AccessControlType]::Allow
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', $inheritance, $none, $allow)))
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($system, 'FullControl', $inheritance, $none, $allow)))
Set-Acl -LiteralPath $InstallRoot -AclObject $acl

Copy-Item -Path (Join-Path $BundleRoot '*') -Destination $InstallRoot -Recurse -Force
$configPath = Join-Path $InstallRoot 'config.json'
$config = Get-Content -LiteralPath (Join-Path $InstallRoot 'config.example.json') -Raw | ConvertFrom-Json
$config.state_root = $InstallRoot
$config.bundle_root = $InstallRoot
$config.trusted_signer_sha256 = $TrustedSignerSha256.ToLowerInvariant()
$config.allow_mutations = [bool]$AllowMutations
# Windows PowerShell 5.1 Set-Content UTF8 adds a BOM that Go's JSON parser rejects.
[IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($false)))

$executable = Join-Path $InstallRoot 'edsys-fleet-agent.exe'
# Create the identity once, before the long-running task can race its creation.
$identityInfo = New-Object Diagnostics.ProcessStartInfo
$identityInfo.FileName = $executable
$identityInfo.Arguments = '--config "' + $configPath + '" --print-enrollment'
$identityInfo.UseShellExecute = $false
$identityInfo.CreateNoWindow = $true
$identityInfo.RedirectStandardOutput = $true
$identityInfo.RedirectStandardError = $true
$identityProcess = [Diagnostics.Process]::Start($identityInfo)
try {
    if (-not $identityProcess.WaitForExit(30000)) {
        $identityProcess.Kill()
        throw 'Agent identity initialization timed out.'
    }
    if ($identityProcess.ExitCode -ne 0) { throw 'Agent identity initialization failed.' }
    $enrollment = $identityProcess.StandardOutput.ReadToEnd()
    $null = $enrollment | ConvertFrom-Json
}
finally { $identityProcess.Dispose() }
$arguments = "--config `"$configPath`""
$action = New-ScheduledTaskAction -Execute $executable -Argument $arguments
$trigger = New-ScheduledTaskTrigger -AtLogOn
$triggerBoot = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RestartCount 12 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
if ($RunAsSystem) {
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
}
else {
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Highest
}
Register-ScheduledTask -TaskName 'EdSys-Fleet-Outbound-Agent' -Action $action -Trigger @($trigger,$triggerBoot) -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName 'EdSys-Fleet-Outbound-Agent'
$enrollment
