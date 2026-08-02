[CmdletBinding()]
param(
    [ValidatePattern('^[A-Z]:$')]
    [string]$LocalPath = 'Q:',
    [string]$RemotePath = '\\9950x.taile832fe.ts.net\EdSys-Share',
    [string]$ServerName = '9950x.taile832fe.ts.net',
    [string]$Label = '',
    [ValidateRange(30, 1800)]
    [int]$WaitSeconds = 600
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if ([string]::IsNullOrWhiteSpace($Label)) {
    $Label = if ($LocalPath -eq 'Q:') { 'EdSys Share' } else { 'Remote Drive' }
}

$stateDirectory = Join-Path $env:LOCALAPPDATA 'EdSys'
$stateFile = switch ($LocalPath) {
    'Q:' { 'EdSys-Share-Q-status.json' }
    'R:' { 'Foothills-Project-R-status.json' }
    default { 'Remote-Drive-{0}-status.json' -f $LocalPath.TrimEnd(':') }
}
$statePath = Join-Path $stateDirectory $stateFile
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

function Get-PersistentProfile {
    $registryPath = "HKCU:\Network\$($LocalPath.TrimEnd(':'))"
    if (-not (Test-Path -LiteralPath $registryPath)) {
        return $null
    }
    return Get-ItemProperty -LiteralPath $registryPath
}

function Write-DriveState {
    param([string]$Status, [string]$Detail)

    $profile = Get-PersistentProfile
    $mapping = Get-SmbMapping -LocalPath $LocalPath -ErrorAction SilentlyContinue
    [pscustomobject]@{
        status = $Status
        detail = $Detail
        time = (Get-Date).ToString('o')
        localPath = $LocalPath
        remotePath = $RemotePath
        label = $Label
        reachable = (Test-Path -LiteralPath ($LocalPath + '\'))
        persistentProfile = ($null -ne $profile -and $profile.RemotePath -ieq $RemotePath)
        mappingStatus = if ($null -ne $mapping) { [string]$mapping.Status } else { $null }
        user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
        sessionId = (Get-Process -Id $PID).SessionId
    } | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $statePath -Encoding UTF8
}

function Test-SmbEndpoint {
    $client = New-Object Net.Sockets.TcpClient
    try {
        $connect = $client.BeginConnect($ServerName, 445, $null, $null)
        if (-not $connect.AsyncWaitHandle.WaitOne(3000, $false)) {
            return $false
        }
        $client.EndConnect($connect)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Set-ExplorerLabel {
    $mountRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2'
    $mountName = '##' + $RemotePath.TrimStart('\').Replace('\', '#')
    $mountKey = Join-Path $mountRoot $mountName
    New-Item -Path $mountKey -Force | Out-Null
    New-ItemProperty -Path $mountKey -Name '_LabelFromReg' -PropertyType String -Value $Label -Force | Out-Null
}

try {
    Write-DriveState 'running' 'Waiting for the SMB endpoint.'
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while (-not (Test-SmbEndpoint)) {
        if ((Get-Date) -ge $deadline) {
            throw "SMB endpoint did not become ready within $WaitSeconds seconds."
        }
        Start-Sleep -Seconds 5
    }

    $registryPath = "HKCU:\Network\$($LocalPath.TrimEnd(':'))"
    $logicalDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$LocalPath'" -ErrorAction SilentlyContinue
    if ($null -ne $logicalDrive -and $logicalDrive.ProviderName -and $logicalDrive.ProviderName -ine $RemotePath) {
        throw "$LocalPath is already assigned to a different resource: $($logicalDrive.ProviderName)"
    }

    $mapping = Get-SmbMapping -LocalPath $LocalPath -ErrorAction SilentlyContinue
    if ($null -ne $mapping) {
        try {
            Remove-SmbMapping -LocalPath $LocalPath -Force -UpdateProfile -ErrorAction Stop
        }
        catch {
            & "$env:SystemRoot\System32\cmd.exe" /d /c "net use $LocalPath /delete /y >nul 2>&1" | Out-Null
        }
    }
    & "$env:SystemRoot\System32\cmd.exe" /d /c "net use $LocalPath /delete /y >nul 2>&1" | Out-Null
    Remove-Item -LiteralPath $registryPath -Recurse -Force -ErrorAction SilentlyContinue

    $lastError = $null
    do {
        $netOutput = & "$env:SystemRoot\System32\cmd.exe" /d /c "net use $LocalPath $RemotePath /persistent:yes 2>&1"
        $netExit = $LASTEXITCODE
        $profile = Get-PersistentProfile
        $mapping = Get-SmbMapping -LocalPath $LocalPath -ErrorAction SilentlyContinue
        if (
            $netExit -eq 0 -and
            $null -ne $profile -and
            $profile.RemotePath -ieq $RemotePath -and
            $null -ne $mapping -and
            $mapping.RemotePath -ieq $RemotePath -and
            (Test-Path -LiteralPath ($LocalPath + '\'))
        ) {
            $lastError = $null
            break
        }

        $safeOutput = (($netOutput | Out-String).Trim() -replace '(?i)password\s*[:=].*', '[redacted]')
        $lastError = if ($safeOutput) {
            "net use exit ${netExit}: $safeOutput"
        }
        else {
            "net use exit $netExit without a reachable persistent profile."
        }

        if ((Get-Date) -ge $deadline) {
            throw $lastError
        }
        Start-Sleep -Seconds 5
    } while ($true)

    Set-ExplorerLabel
    Write-DriveState 'ok' 'Explorer-visible persistent mapping and File Explorer label are ready; the server requires SMB encryption.'
}
catch {
    Write-DriveState 'error' $_.Exception.Message
    exit 1
}
