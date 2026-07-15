[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z]:$')]
    [string]$LocalPath = 'R:',
    [string]$TaskName = 'Foothills Inbox R Reconnect',
    [int]$LogonDelaySeconds = 25
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$source = Join-Path $PSScriptRoot 'Reconnect-EdSysShare.ps1'
if (-not (Test-Path -LiteralPath $source)) {
    throw "Reconnect script is missing: $source"
}

$remotePath = '\\9950x.taile832fe.ts.net\Foothills-Inbox'
$installDirectory = Join-Path $env:LOCALAPPDATA 'EdSys'
$installedScript = Join-Path $installDirectory 'Reconnect-EdSysShare.ps1'
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $installedScript -Force

$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$actionArguments = (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass ' +
    '-File "' + $installedScript + '" ' +
    '-LocalPath "' + $LocalPath + '" ' +
    '-RemotePath "' + $remotePath + '"'
)
$action = New-ScheduledTaskAction -Execute $powerShell -Argument $actionArguments
$trigger = New-ScheduledTaskTrigger -AtLogOn -User (
    [Security.Principal.WindowsIdentity]::GetCurrent().Name
)
$trigger.Delay = 'PT{0}S' -f $LogonDelaySeconds
$principal = New-ScheduledTaskPrincipal -UserId (
    [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description (
        "Waits for Tailscale/SMB readiness and maps Foothills Inbox as $LocalPath."
    ) -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started scheduled task: $TaskName"
