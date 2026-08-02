[CmdletBinding()]
param(
    [string]$TaskName = 'Basecamp Shares Reconnect',
    [int]$LogonDelaySeconds = 30
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$source = Join-Path $PSScriptRoot 'Reconnect-BasecampShares.ps1'
if (-not (Test-Path -LiteralPath $source)) {
    throw "Reconnect script is missing: $source"
}

$installDirectory = Join-Path $env:LOCALAPPDATA 'EdSys'
$installedScript = Join-Path $installDirectory 'Reconnect-BasecampShares.ps1'
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $installedScript -Force

$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action = New-ScheduledTaskAction -Execute $powerShell -Argument (
    '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass ' +
    '-File "' + $installedScript + '"'
)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User (
    [Security.Principal.WindowsIdentity]::GetCurrent().Name
)
$trigger.Delay = 'PT{0}S' -f $LogonDelaySeconds
$principal = New-ScheduledTaskPrincipal -UserId (
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description (
        'Waits for Basecamp SMB readiness and restores the five persistent Nimo file drives with bounded retries.'
    ) -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started scheduled task: $TaskName"
