[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z]:$')]
    [string]$LocalPath = 'R:',
    [string]$TaskName = 'Foothills Project R Reconnect',
    [int]$LogonDelaySeconds = 25
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$source = Join-Path $PSScriptRoot 'Reconnect-EdSysShare.ps1'
if (-not (Test-Path -LiteralPath $source)) {
    throw "Reconnect script is missing: $source"
}

$remotePath = '\\9950x.taile832fe.ts.net\Foothills-Project'
$installDirectory = Join-Path $env:LOCALAPPDATA 'EdSys'
$installedScript = Join-Path $installDirectory 'Reconnect-EdSysShare.ps1'
New-Item -ItemType Directory -Path $installDirectory -Force | Out-Null
Copy-Item -LiteralPath $source -Destination $installedScript -Force

$powerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$actionArguments = (
    '-NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass ' +
    '-File "' + $installedScript + '" ' +
    '-LocalPath "' + $LocalPath + '" ' +
    '-RemotePath "' + $remotePath + '" ' +
    '-Label "Foothills Project"'
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
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 20) `
    -Hidden

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description (
        "Waits for Tailscale/SMB readiness and restores the persistent full Foothills project tree as $LocalPath."
    ) -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started scheduled task: $TaskName"
