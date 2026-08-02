$ProgressPreference = 'SilentlyContinue'
$ErrorActionPreference = 'Continue'
$ServerName = 'basecamp'
$WaitSeconds = 600
$maps = @(
    [pscustomobject]@{ Letter = 'F:'; Share = '03_Files'; Label = 'Basecamp Files' },
    [pscustomobject]@{ Letter = 'K:'; Share = 'Kindle-Drop'; Label = 'Kindle Drop' },
    [pscustomobject]@{ Letter = 'S:'; Share = 'Foothills_ASI'; Label = 'Foothills ASI' },
    [pscustomobject]@{ Letter = 'T:'; Share = '07_Transfer'; Label = 'Basecamp Transfer' },
    [pscustomobject]@{ Letter = 'U:'; Share = 'Foothills_Unit_Selections_Intake'; Label = 'Unit Selections Intake' }
)

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
    param([string]$RemotePath, [string]$Label)

    $mountRoot = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2'
    $mountName = '##' + $RemotePath.TrimStart('\').Replace('\', '#')
    $mountKey = Join-Path $mountRoot $mountName
    New-Item -Path $mountKey -Force | Out-Null
    New-ItemProperty -Path $mountKey -Name '_LabelFromReg' -PropertyType String -Value $Label -Force | Out-Null
}

$deadline = (Get-Date).AddSeconds($WaitSeconds)
while (-not (Test-SmbEndpoint)) {
    if ((Get-Date) -ge $deadline) {
        break
    }
    Start-Sleep -Seconds 5
}

$endpointReady = Test-SmbEndpoint
$results = foreach ($m in $maps) {
    $remote = '\\' + $ServerName + '\' + $m.Share
    $registryPath = "HKCU:\Network\$($m.Letter.TrimEnd(':'))"
    $attempts = 0
    $exitCode = $null
    $reachable = $false
    $itemCount = $null
    $errorText = if ($endpointReady) { $null } else { 'SMB endpoint did not become ready before the reconnect deadline.' }

    if ($endpointReady) {
        do {
            $attempts++
            $profile = if (Test-Path -LiteralPath $registryPath) { Get-ItemProperty -LiteralPath $registryPath } else { $null }
            $logical = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($m.Letter)'" -ErrorAction SilentlyContinue
            if (
                $null -ne $profile -and
                $profile.RemotePath -ieq $remote -and
                $null -ne $logical -and
                $logical.ProviderName -ieq $remote -and
                (Test-Path -LiteralPath ($m.Letter + '\'))
            ) {
                $exitCode = 0
                $reachable = $true
                break
            }

            if ($null -ne $logical -and $logical.ProviderName -and $logical.ProviderName -ine $remote) {
                $errorText = "$($m.Letter) is assigned to a different resource: $($logical.ProviderName)"
                $exitCode = 85
                break
            }

            & "$env:SystemRoot\System32\net.exe" use $m.Letter /delete /y 2>&1 | Out-Null
            $netOutput = & "$env:SystemRoot\System32\net.exe" use $m.Letter $remote /persistent:yes 2>&1
            $exitCode = $LASTEXITCODE

            if ($exitCode -eq 0 -and (Test-Path -LiteralPath ($m.Letter + '\'))) {
                $profile = if (Test-Path -LiteralPath $registryPath) { Get-ItemProperty -LiteralPath $registryPath } else { $null }
                if ($null -ne $profile -and $profile.RemotePath -ieq $remote) {
                    $reachable = $true
                    $errorText = $null
                    break
                }
                $errorText = 'The drive connected but its persistent profile was not created.'
            }
            else {
                $safeOutput = (($netOutput | Out-String).Trim() -replace '(?i)password\s*[:=].*', '[redacted]')
                $errorText = if ($safeOutput) { "net use exit ${exitCode}: $safeOutput" } else { "net use exit $exitCode" }
            }

            if ((Get-Date) -ge $deadline) {
                break
            }
            Start-Sleep -Seconds 5
        } while ($true)
    }

    if ($reachable) {
        try {
            Set-ExplorerLabel -RemotePath $remote -Label $m.Label
            $itemCount = @(Get-ChildItem -LiteralPath ($m.Letter + '\') -Force -ErrorAction Stop).Count
        }
        catch {
            $reachable = $false
            $errorText = $_.Exception.Message
        }
    }

    [pscustomobject]@{
        Letter = $m.Letter
        Share = $m.Share
        RemotePath = $remote
        Label = $m.Label
        Attempts = $attempts
        ExitCode = $exitCode
        Reachable = $reachable
        PersistentProfile = (Test-Path -LiteralPath $registryPath)
        TopLevelItemCount = $itemCount
        Error = $errorText
    }
}

$status = [pscustomobject]@{
    RunId = [guid]::NewGuid().ToString()
    Timestamp = (Get-Date).ToString('o')
    Computer = $env:COMPUTERNAME
    User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    SessionId = (Get-Process -Id $PID).SessionId
    Server = $ServerName
    EndpointReady = $endpointReady
    Mappings = $results
    AllPassed = (@($results | Where-Object { -not $_.Reachable -or -not $_.PersistentProfile -or $_.ExitCode -ne 0 }).Count -eq 0)
}
$statusPath = Join-Path $env:LOCALAPPDATA 'EdSys\BasecampSharesReconnect-status.json'
$status | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $statusPath -Encoding UTF8
if (-not $status.AllPassed) {
    exit 1
}
