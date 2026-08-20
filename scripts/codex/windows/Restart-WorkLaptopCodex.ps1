[CmdletBinding()]
param(
    [ValidatePattern('^[0-9]+\.[0-9]+\.[0-9]+$')]
    [string]$TargetCodexVersion = '0.148.0',
    [ValidateRange(5, 60)]
    [int]$StopTimeoutSeconds = 20,
    [ValidateRange(10, 120)]
    [int]$StartTimeoutSeconds = 45
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$expectedComputer = 'THOMPSON-LC086'
$expectedIdentity = 'THOMPSON\jclark'
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($env:COMPUTERNAME -ine $expectedComputer -or $currentIdentity -ine $expectedIdentity) {
    throw "This restart is restricted to $expectedIdentity on $expectedComputer; current endpoint is $currentIdentity on $env:COMPUTERNAME."
}

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this restart from the ordinary desktop session, not from an elevated PowerShell window.'
}

$package = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction Stop | Select-Object -First 1
if ($null -eq $package -or $package.Status -ne 'Ok') {
    throw 'The unified ChatGPT/Codex Appx package is missing or unhealthy.'
}
$app = Get-StartApps |
    Where-Object { $_.AppID -like ($package.PackageFamilyName + '!*') } |
    Select-Object -First 1
if ($null -eq $app) {
    throw 'The unified ChatGPT/Codex Start-menu entry was not found.'
}

$processNames = @('ChatGPT', 'ChatGPT Classic', 'codex')
$stopDeadline = (Get-Date).AddSeconds($StopTimeoutSeconds)
do {
    $running = @(
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $processNames -contains $_.ProcessName }
    )
    if ($running.Count -eq 0) { break }
    $running | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
} while ((Get-Date) -lt $stopDeadline)

$survivors = @(
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $processNames -contains $_.ProcessName }
)
if ($survivors.Count -gt 0) {
    throw 'One or more ChatGPT/Codex processes did not stop within the bounded wait.'
}

$launchStarted = Get-Date
Start-Process -FilePath 'explorer.exe' -ArgumentList ('shell:AppsFolder\' + $app.AppID)
$startDeadline = $launchStarted.AddSeconds($StartTimeoutSeconds)
do {
    Start-Sleep -Seconds 2
    $freshChatGpt = @(Get-Process -Name 'ChatGPT' -ErrorAction SilentlyContinue)
} while ($freshChatGpt.Count -eq 0 -and (Get-Date) -lt $startDeadline)
if ($freshChatGpt.Count -eq 0) {
    throw 'Unified ChatGPT/Codex did not relaunch within the bounded wait.'
}

$standaloneBinary = Join-Path $env:USERPROFILE '.codex\packages\standalone\current\bin\codex.exe'
if (-not (Test-Path -LiteralPath $standaloneBinary -PathType Leaf)) {
    throw "The expected standalone Codex binary is missing: $standaloneBinary"
}
$standaloneVersion = (& $standaloneBinary --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $standaloneVersion -ne "codex-cli $TargetCodexVersion") {
    throw "The standalone Codex version check failed after restart: $standaloneVersion"
}

$classicProcesses = @(Get-Process -Name 'ChatGPT Classic' -ErrorAction SilentlyContinue)
if ($classicProcesses.Count -gt 0) {
    throw 'ChatGPT Classic relaunched unexpectedly; unified-app acceptance is not clean.'
}

Write-Host $standaloneVersion
Write-Host "PASS: Unified ChatGPT/Codex restarted with $($freshChatGpt.Count) fresh process(es); ChatGPT Classic remains stopped."
