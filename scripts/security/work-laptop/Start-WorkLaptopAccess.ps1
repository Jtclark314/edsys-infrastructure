#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Install')][string]$Action = 'Plan',
    [switch]$EmployerApproved
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ine 'THOMPSON-LC086' -or
    [Security.Principal.WindowsIdentity]::GetCurrent().Name -ine 'THOMPSON\jclark') {
    throw 'Run locally on THOMPSON-LC086 as THOMPSON\jclark.'
}
if ($Action -eq 'Install' -and -not $EmployerApproved) { throw 'Employer/IT approval for persistent inbound administration is required; attest with -EmployerApproved.' }
$installer = Join-Path $PSScriptRoot 'Manage-WorkLaptopAccess.ps1'
$runner = Join-Path $PSScriptRoot 'Invoke-WorkLaptopAccess.ps1'
$manifest = Join-Path $PSScriptRoot 'access.json'
$bundle = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bundle.json') -Raw | ConvertFrom-Json
if ($bundle.schemaVersion -ne 1 -or $bundle.hubUploadDirectory -notmatch '^/home/jeremy/\.codex/operator-checkpoints/[a-zA-Z0-9/_-]+$') {
    throw 'Invalid private hub return directory.'
}
foreach ($name in @('Manage-WorkLaptopAccess.ps1', 'Invoke-WorkLaptopAccess.ps1', 'access.json')) {
    $expected = $bundle.hashes.PSObject.Properties[$name].Value
    if ((Get-FileHash (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash -ine $expected) {
        throw "Bundle checksum mismatch: $name"
    }
}
if ($runner -match '["\r\n]' -or $manifest -match '["\r\n]') { throw 'Unsafe bundle path.' }
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$runId = [Guid]::NewGuid().ToString('N')
$resultPath = Join-Path $PSScriptRoot ("result-$runId.json")
$arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -Action {1} -RunId {2}' -f $runner, $Action, $runId
if ($EmployerApproved) { $arguments += ' -EmployerApproved' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Reuse an already elevated console; do not hide its child in another UAC window.
    $nativeArguments = @('-NoLogo', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $runner,
        '-Action', $Action, '-RunId', $runId)
    # Windows PowerShell -File does not support explicit boolean values for
    # switch parameters. Pass the flag only when set.
    if ($EmployerApproved) { $nativeArguments += '-EmployerApproved' }
    & $ps @nativeArguments
    $exitCode = $LASTEXITCODE
} else {
    Write-Host 'Windows will ask for administrator approval. Existing corporate policy remains authoritative.'
    $process = Start-Process -FilePath $ps -ArgumentList $arguments -Verb RunAs -Wait -PassThru
    $exitCode = $process.ExitCode
}
if (-not (Test-Path -LiteralPath $resultPath)) { throw "The elevated runner returned no diagnostic (exit $exitCode). Run Invoke-WorkLaptopAccess.ps1 locally in an elevated console to inspect a startup/policy error." }
$result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
if ($result.runId -cne $runId -or $result.action -cne $Action -or $result.exitCode -ne $exitCode) {
    throw 'The elevated diagnostic does not match this invocation.'
}
& scp.exe -q -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 `
    $resultPath ('9950x:' + $bundle.hubUploadDirectory + "/result-$runId.json")
if ($LASTEXITCODE -ne 0) { Write-Warning "Diagnostic upload failed; local report: $resultPath" }
if ($exitCode -ne 0) {
    throw ("{0} failed: {1} ({2}:{3}). Installation status: {4}. Diagnostic: {5}" -f
        $Action, $result.error.message, $result.error.script, $result.error.line, $result.installationStatus, $resultPath)
}
if ($Action -eq 'Plan') { Write-Host 'Read-only preflight completed; diagnostic returned to 9950x when reachable.'; return }
$hostKey = Join-Path $PSScriptRoot 'host-key.pub'
if (-not (Test-Path -LiteralPath $hostKey)) { throw 'Public host key export is absent; hub acceptance cannot proceed.' }
# The ordinary user sends only a public host key over the pre-existing trusted
# outbound connection. No private SSH key or Windows credential is uploaded.
& scp.exe -q -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 `
    $hostKey ('9950x:' + $bundle.hubUploadDirectory + '/host-key.pub')
if ($LASTEXITCODE -ne 0) { throw 'Installation passed locally, but public host-key return failed. Keep the local host-key.pub for operator verification.' }
Write-Host 'Local setup passed and public host key returned to 9950x. Tell Codex: setup completed.'
