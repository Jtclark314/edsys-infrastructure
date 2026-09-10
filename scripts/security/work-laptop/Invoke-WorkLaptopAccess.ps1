#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Install')][string]$Action = 'Plan',
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-f0-9]{32}$')][string]$RunId,
    [switch]$EmployerApproved
)
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

function Get-AccessFailureDetail {
    param([Management.Automation.ErrorRecord]$Record)
    # Do not include invocation text, native command output collections,
    # configuration, key material, or a transcript in the returned receipt.
    $message = [string]$Record.Exception.Message
    $message = $message -replace '(ssh-ed25519|ssh-rsa)\s+[A-Za-z0-9+/=]+', '$1 [redacted]'
    if ($message.Length -gt 2000) { $message = $message.Substring(0, 2000) }
    return [ordered]@{
        message = $message
        exceptionType = $Record.Exception.GetType().FullName
        errorId = [string]$Record.FullyQualifiedErrorId
        script = [IO.Path]::GetFileName([string]$Record.InvocationInfo.ScriptName)
        line = $Record.InvocationInfo.ScriptLineNumber
    }
}

function Invoke-AccessWithReceipt {
    param([scriptblock]$Operation, [string]$ResultPath, [string]$Id, [string]$RequestedAction)
    $result = [ordered]@{
        schemaVersion = 1; runId = $Id; action = $RequestedAction
        status = 'failed'; exitCode = 1; error = $null
        completedAt = ''; installationStatus = 'not-observed'
    }
    try {
        & $Operation | Out-Host
        $result.status = 'succeeded'
        $result.exitCode = 0
    } catch {
        $result.error = Get-AccessFailureDetail $_
    }
    $result.completedAt = (Get-Date).ToUniversalTime().ToString('o')
    # Report only the installer's own phase, if it exists. This never retries
    # a failed install or reads private keys/configuration into the result.
    try {
        $state = Join-Path $env:ProgramData 'EdSys-WorkLaptopAccess\receipt.json'
        if (Test-Path -LiteralPath $state) {
            $receipt = Get-Content -LiteralPath $state -Raw | ConvertFrom-Json
            if ($receipt.owner -eq 'EdSys-WorkLaptopAccess-v1') {
                $result.installationStatus = [string]$receipt.status
            }
        } else { $result.installationStatus = 'no-installation-receipt' }
    } catch { $result.installationStatus = 'receipt-unreadable' }
    # Exclusive creation prevents stale diagnostics or replacing an existing
    # file. The outer launcher chooses a new random ID for every invocation.
    $stream = [IO.File]::Open($ResultPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    try {
        $bytes = (New-Object Text.UTF8Encoding($false)).GetBytes(($result | ConvertTo-Json -Depth 5))
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
    return [int]$result.exitCode
}

$installer = Join-Path $PSScriptRoot 'Manage-WorkLaptopAccess.ps1'
$manifest = Join-Path $PSScriptRoot 'access.json'
$resultPath = Join-Path $PSScriptRoot ("result-$RunId.json")
$code = Invoke-AccessWithReceipt -Id $RunId -RequestedAction $Action -ResultPath $resultPath -Operation {
    & $installer -Action $Action -ManifestPath $manifest -EmployerApproved:$EmployerApproved
}
exit $code
