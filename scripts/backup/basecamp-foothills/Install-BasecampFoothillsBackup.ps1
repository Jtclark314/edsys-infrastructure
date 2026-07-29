[CmdletBinding()]
param(
    [string]$SourceRoot = $PSScriptRoot,
    [string]$InstallRoot = "C:\EdSys\FoothillsOffsiteBackup",
    [string]$TaskName = "Foothills Offsite Backup",
    [string]$DailyAt = "01:30"
)

$ErrorActionPreference = "Stop"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)) {
    throw "Run this installer from an elevated PowerShell session."
}

New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $SourceRoot "basecamp_foothills_stage.py") `
    -Destination (Join-Path $InstallRoot "basecamp_foothills_stage.py") -Force
Copy-Item -LiteralPath (Join-Path $SourceRoot "Backup-BasecampFoothills.ps1") `
    -Destination (Join-Path $InstallRoot "Backup-BasecampFoothills.ps1") -Force

$Action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
        (Join-Path $InstallRoot "Backup-BasecampFoothills.ps1") + '"') `
    -WorkingDirectory $InstallRoot
$Trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$Settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $Action `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description "Create a verified, private recovery stage for every Basecamp Foothills app and critical host service configuration." `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
$Deadline = (Get-Date).AddHours(2)
do {
    Start-Sleep -Seconds 2
    $Task = Get-ScheduledTask -TaskName $TaskName
} while ($Task.State -eq "Running" -and (Get-Date) -lt $Deadline)

$Info = Get-ScheduledTaskInfo -TaskName $TaskName
if ($Task.State -eq "Running") {
    throw "Initial Basecamp backup did not finish within two hours."
}
if ($Info.LastTaskResult -ne 0) {
    throw "Initial Basecamp backup failed with task result $($Info.LastTaskResult)."
}

$Manifest = "C:\Foothills\OffsiteBackup\current\manifest.json"
if (-not (Test-Path -LiteralPath $Manifest)) {
    throw "Initial backup completed without publishing a manifest."
}

Write-Host "Installed and verified scheduled task: $TaskName"
Write-Host "Current manifest: $Manifest"
