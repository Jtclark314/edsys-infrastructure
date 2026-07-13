[CmdletBinding()]
param(
    [string]$LocalPath = 'Q:',
    [string]$RemotePath = '\\9950x.taile832fe.ts.net\EdSys-Share',
    [string]$ServerName = '9950x.taile832fe.ts.net',
    [int]$WaitSeconds = 300
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'

$stateDirectory = Join-Path $env:LOCALAPPDATA 'EdSys'
$statePath = Join-Path $stateDirectory 'EdSys-Share-Q-status.json'
New-Item -ItemType Directory -Path $stateDirectory -Force | Out-Null

function Write-EdSysShareState {
    param([string]$Status, [string]$Detail)

    [pscustomobject]@{
        status = $Status
        detail = $Detail
        time = (Get-Date).ToString('o')
        localPath = $LocalPath
        remotePath = $RemotePath
    } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
}

function Test-SmbEndpoint {
    $client = New-Object System.Net.Sockets.TcpClient
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

try {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while (-not (Test-SmbEndpoint)) {
        if ((Get-Date) -ge $deadline) {
            throw "SMB endpoint did not become ready within $WaitSeconds seconds."
        }
        Start-Sleep -Seconds 5
    }

    $logicalDrive = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$LocalPath'" `
        -ErrorAction SilentlyContinue
    if ($null -ne $logicalDrive -and
        $logicalDrive.ProviderName -ne $RemotePath) {
        throw "$LocalPath is already assigned to a different resource."
    }

    $registryPath = "HKCU:\Network\$($LocalPath.TrimEnd(':'))"
    $persistentProfile = Test-Path -LiteralPath $registryPath
    $mapping = Get-SmbMapping -LocalPath $LocalPath -ErrorAction SilentlyContinue
    if ($null -ne $mapping -and
        $mapping.RemotePath -eq $RemotePath -and
        -not $persistentProfile -and
        (Test-Path "$LocalPath\")) {
        Write-EdSysShareState 'ok' 'Existing mapping is accessible.'
        exit 0
    }

    if ($null -ne $mapping) {
        try {
            Remove-SmbMapping -LocalPath $LocalPath -Force -UpdateProfile `
                -ErrorAction Stop
        }
        catch {
            & cmd.exe /c "net use $LocalPath /delete /y" 2>$null | Out-Null
        }
    }

    Remove-Item -LiteralPath $registryPath -Recurse -Force `
        -ErrorAction SilentlyContinue

    New-SmbMapping -LocalPath $LocalPath -RemotePath $RemotePath `
        -Persistent $false -RequireIntegrity $true -RequirePrivacy $true | Out-Null

    if (-not (Test-Path "$LocalPath\")) {
        throw 'The SMB mapping was created but is not accessible.'
    }

    Write-EdSysShareState 'ok' 'Mapping created after the SMB endpoint became ready.'
}
catch {
    Write-EdSysShareState 'error' $_.Exception.Message
    exit 1
}
