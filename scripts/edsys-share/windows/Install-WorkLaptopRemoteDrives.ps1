[CmdletBinding()]
param(
    [switch]$SkipCredentialPrompts,
    [ValidateRange(30, 900)]
    [int]$VerificationTimeoutSeconds = 180
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

$expectedComputer = 'THOMPSON-LC086'
$expectedIdentity = 'THOMPSON\jclark'
$currentIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if ($env:COMPUTERNAME -ine $expectedComputer -or $currentIdentity -ine $expectedIdentity) {
    throw "This installer is restricted to $expectedIdentity on $expectedComputer; current endpoint is $currentIdentity on $env:COMPUTERNAME."
}

$principal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this installer normally, not with Run as administrator, so the mappings enter the existing Explorer session.'
}

$maps = @(
    [pscustomobject]@{ Letter = 'F:'; RemotePath = '\\basecamp\03_Files'; Label = 'Basecamp Files' },
    [pscustomobject]@{ Letter = 'I:'; RemotePath = '\\9950x.taile832fe.ts.net\Foothills-Inbox\ask-foothills-intake'; Label = 'Ask Foothills Intake' },
    [pscustomobject]@{ Letter = 'K:'; RemotePath = '\\basecamp\Kindle-Drop'; Label = 'Kindle Drop' },
    [pscustomobject]@{ Letter = 'Q:'; RemotePath = '\\9950x.taile832fe.ts.net\EdSys-Share'; Label = 'EdSys Share' },
    [pscustomobject]@{ Letter = 'R:'; RemotePath = '\\9950x.taile832fe.ts.net\Foothills-Project'; Label = 'Foothills Project' },
    [pscustomobject]@{ Letter = 'S:'; RemotePath = '\\basecamp\Foothills_ASI'; Label = 'Foothills ASI' },
    [pscustomobject]@{ Letter = 'T:'; RemotePath = '\\basecamp\07_Transfer'; Label = 'Basecamp Transfer' },
    [pscustomobject]@{ Letter = 'U:'; RemotePath = '\\basecamp\Foothills_Unit_Selections_Intake'; Label = 'Unit Selections Intake' }
)

foreach ($map in $maps) {
    $logical = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($map.Letter)'" -ErrorAction SilentlyContinue
    if ($null -eq $logical) {
        continue
    }
    if (-not $logical.ProviderName -or $logical.ProviderName -ine $map.RemotePath) {
        throw "$($map.Letter) is already assigned to a different resource: $($logical.ProviderName)"
    }
}

function Test-SavedCredential {
    param([Parameter(Mandatory = $true)][string]$Target)

    $output = & "$env:SystemRoot\System32\cmdkey.exe" "/list:$Target" 2>&1
    return ($LASTEXITCODE -eq 0 -and (($output | Out-String) -match [regex]::Escape($Target)))
}

function Ensure-SavedCredential {
    param(
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$UserName,
        [Parameter(Mandatory = $true)][string]$Purpose
    )

    if (Test-SavedCredential -Target $Target) {
        Write-Host "Credential Manager already contains the $Purpose credential for $Target."
        return
    }
    if ($SkipCredentialPrompts) {
        throw "Credential Manager does not contain the required $Purpose credential for $Target."
    }

    Write-Host "Windows will now request the $Purpose password for $UserName."
    Write-Host 'The password is entered through cmdkey and is not stored in this script or its status report.'
    & "$env:SystemRoot\System32\cmdkey.exe" "/add:$Target" "/user:$UserName" /pass
    if ($LASTEXITCODE -ne 0 -or -not (Test-SavedCredential -Target $Target)) {
        throw "Credential Manager did not save the required $Purpose credential for $Target."
    }
}

Ensure-SavedCredential -Target '9950x.taile832fe.ts.net' `
    -UserName '9950x\edsys-share-dell' -Purpose '9950x file-share'
Ensure-SavedCredential -Target 'basecamp' `
    -UserName 'BASECAMP\FoothillsShares' -Purpose 'Basecamp file-share'

$installers = @(
    'Install-AskFoothillsIntakeReconnect.ps1',
    'Install-EdSysShareReconnect.ps1',
    'Install-FoothillsProjectReconnect.ps1',
    'Install-BasecampSharesReconnect.ps1'
)
foreach ($installer in $installers) {
    $path = Join-Path $PSScriptRoot $installer
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required installer is missing: $path"
    }
    & $path | ForEach-Object { Write-Host $_ }
}

$taskNames = @(
    'Ask Foothills Intake I Reconnect',
    'EdSys Share Q Reconnect',
    'Foothills Project R Reconnect',
    'Basecamp Shares Reconnect'
)
$deadline = (Get-Date).AddSeconds($VerificationTimeoutSeconds)
foreach ($taskName in $taskNames) {
    do {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
        if ($task.State -ne 'Running') {
            break
        }
        if ((Get-Date) -ge $deadline) {
            throw "Timed out waiting for scheduled task: $taskName"
        }
        Start-Sleep -Seconds 1
    } while ($true)

    $info = Get-ScheduledTaskInfo -TaskName $taskName
    if ($info.LastTaskResult -ne 0) {
        throw "Scheduled task failed: $taskName (result $($info.LastTaskResult))"
    }
}

do {
    $shell = New-Object -ComObject Shell.Application
    $thisPc = $shell.NameSpace(17)
    $visiblePaths = @(
        $thisPc.Items() |
            Where-Object { $_.Path -match '^[A-Z]:\\$' } |
            ForEach-Object { [string]$_.Path }
    )
    $missing = @($maps | Where-Object { $visiblePaths -notcontains ($_.Letter + '\') })
    if ($missing.Count -eq 0) {
        break
    }
    if ((Get-Date) -ge $deadline) {
        throw ('The mappings completed but This PC is still missing: ' + (($missing | ForEach-Object { $_.Letter }) -join ', '))
    }
    Start-Sleep -Seconds 2
} while ($true)

$results = foreach ($map in $maps) {
    $profilePath = "HKCU:\Network\$($map.Letter.TrimEnd(':'))"
    $profile = if (Test-Path -LiteralPath $profilePath) {
        Get-ItemProperty -LiteralPath $profilePath
    }
    else {
        $null
    }
    [pscustomobject]@{
        letter = $map.Letter
        label = $map.Label
        remotePath = $map.RemotePath
        visibleInThisPC = ($visiblePaths -contains ($map.Letter + '\'))
        reachable = (Test-Path -LiteralPath ($map.Letter + '\'))
        persistentProfile = ($null -ne $profile -and $profile.RemotePath -ieq $map.RemotePath)
        topLevelItemCount = if (Test-Path -LiteralPath ($map.Letter + '\')) {
            @(Get-ChildItem -LiteralPath ($map.Letter + '\') -Force -ErrorAction Stop).Count
        }
        else {
            $null
        }
    }
}

$stateRoot = Join-Path $env:LOCALAPPDATA 'EdSys'
$statusPath = Join-Path $stateRoot 'Work-Laptop-Remote-Drives-status.json'
New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
$status = [pscustomobject]@{
    status = if (@($results | Where-Object { -not $_.visibleInThisPC -or -not $_.reachable -or -not $_.persistentProfile }).Count -eq 0) { 'ok' } else { 'error' }
    time = (Get-Date).ToString('o')
    computer = $env:COMPUTERNAME
    user = $currentIdentity
    sessionId = (Get-Process -Id $PID).SessionId
    mappings = $results
}
$status | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding UTF8

if ($status.status -ne 'ok') {
    throw "One or more mappings failed final This PC/profile/reachability acceptance. Review $statusPath"
}

Start-Process explorer.exe -ArgumentList 'R:\'
Write-Host ''
Write-Host 'PASS: F:, I:, K:, Q:, R:, S:, T:, and U: are visible in This PC, reachable, and persistent.'
Write-Host "Verification report: $statusPath"
