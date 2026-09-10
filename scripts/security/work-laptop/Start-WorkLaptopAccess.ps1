#Requires -Version 5.1
[CmdletBinding()]
param([switch]$EmployerApproved)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
if ($env:COMPUTERNAME -ine 'THOMPSON-LC086' -or
    [Security.Principal.WindowsIdentity]::GetCurrent().Name -ine 'THOMPSON\jclark') {
    throw 'Run locally on THOMPSON-LC086 as THOMPSON\jclark.'
}
if (-not $EmployerApproved) { throw 'Employer/IT approval for persistent inbound administration is required; attest with -EmployerApproved.' }
$installer = Join-Path $PSScriptRoot 'Manage-WorkLaptopAccess.ps1'
$manifest = Join-Path $PSScriptRoot 'access.json'
$bundle = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'bundle.json') -Raw | ConvertFrom-Json
if ($bundle.schemaVersion -ne 1 -or $bundle.hubUploadDirectory -notmatch '^/home/jeremy/\.codex/operator-checkpoints/[a-zA-Z0-9/_-]+$') {
    throw 'Invalid private hub return directory.'
}
foreach ($name in @('Manage-WorkLaptopAccess.ps1', 'access.json')) {
    $expected = $bundle.hashes.PSObject.Properties[$name].Value
    if ((Get-FileHash (Join-Path $PSScriptRoot $name) -Algorithm SHA256).Hash -ine $expected) {
        throw "Bundle checksum mismatch: $name"
    }
}
if ($installer -match '["\r\n]' -or $manifest -match '["\r\n]') { throw 'Unsafe bundle path.' }
$ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "{0}" -Action Install -ManifestPath "{1}" -EmployerApproved' -f $installer, $manifest
Write-Host 'Windows will ask for administrator approval. Existing corporate policy remains authoritative.'
$process = Start-Process -FilePath $ps -ArgumentList $arguments -Verb RunAs -Wait -PassThru
if ($process.ExitCode -ne 0) {
    throw "Installer failed (exit $($process.ExitCode)). To see its diagnostic, run the same installer in an elevated PowerShell window."
}
$hostKey = Join-Path $PSScriptRoot 'host-key.pub'
if (-not (Test-Path -LiteralPath $hostKey)) { throw 'Public host key export is absent; hub acceptance cannot proceed.' }
# The ordinary user sends only a public host key over the pre-existing trusted
# outbound connection. No private SSH key or Windows credential is uploaded.
& scp.exe -q -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=10 `
    $hostKey ('9950x:' + $bundle.hubUploadDirectory + '/host-key.pub')
if ($LASTEXITCODE -ne 0) { throw 'Installation passed locally, but public host-key return failed. Keep the local host-key.pub for operator verification.' }
Write-Host 'Local setup passed and public host key returned to 9950x. Tell Codex: setup completed.'
