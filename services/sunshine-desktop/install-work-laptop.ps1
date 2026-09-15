#Requires -Version 5.1
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$HubTailnetAddress,
    [string]$HubHost = '9950x',
    [Parameter(Mandatory=$true)][string]$HubBundleDirectory,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:COMPUTERNAME -ine 'THOMPSON-LC086' -or
    [Security.Principal.WindowsIdentity]::GetCurrent().Name -ine 'THOMPSON\jclark') {
    throw 'Run locally as THOMPSON\jclark on THOMPSON-LC086.'
}
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run in ordinary PowerShell, not as administrator.'
}
if ($HubHost -notmatch '^[a-zA-Z0-9][a-zA-Z0-9.-]*$' -or
    $HubBundleDirectory -notmatch '^/home/jeremy/\.codex/operator-checkpoints/[a-zA-Z0-9_-]+$') {
    throw 'Invalid private hub connection parameters.'
}
$ip = [Net.IPAddress]::Parse($HubTailnetAddress).GetAddressBytes()
if ($ip.Length -ne 4 -or $ip[0] -ne 100 -or $ip[1] -lt 64 -or $ip[1] -gt 127) {
    throw 'Expected an exact Tailscale IPv4 address.'
}
function Test-Port([int]$Port) {
    $c = New-Object Net.Sockets.TcpClient
    try { return ($c.ConnectAsync($HubTailnetAddress,$Port).Wait(4000) -and $c.Connected) }
    catch { return $false } finally { $c.Dispose() }
}
$root = Join-Path $env:LOCALAPPDATA 'EdSys\Moonlight-6.1.0'
$candidates = @((Join-Path $env:ProgramFiles 'Moonlight Game Streaming\Moonlight.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Moonlight Game Streaming\Moonlight.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Moonlight Game Streaming\Moonlight.exe'),
    (Join-Path $root 'Moonlight.exe'))
$command = Get-Command Moonlight.exe -ErrorAction SilentlyContinue
if ($command) { $candidates += $command.Source }
$existing = @($candidates | Select-Object -Unique | Where-Object { Test-Path -LiteralPath $_ })
$report = [ordered]@{ computer=$env:COMPUTERNAME; action='preflight';
    moonlightFound=($existing.Count -gt 0); paired=$false; shortcuts=$false;
    streamPorts=@{}; adminBlocked=$false; visualAcceptance='to be confirmed' }
foreach ($port in @(47984,47989,48010)) { $report.streamPorts["$port"] = Test-Port $port }
$report.adminBlocked = -not (Test-Port 47990)
Write-Host ('Existing Moonlight installations found: ' + $existing.Count)
Write-Host 'Profile: 1080p60, 15 Mbps, borderless desktop, HEVC, hardware decoding, Tailscale.'
if ($Plan) { $report | ConvertTo-Json -Depth 4; return }
$sshOptions = @('-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=10')
$hubName = & ssh.exe @sshOptions $HubHost hostname
if ($LASTEXITCODE -ne 0 -or ($hubName -join '').Trim() -ne '9950x') {
    throw 'The existing trusted SSH connection to 9950x must work before setup.'
}
if ($report.streamPorts.Values -contains $false) { throw 'A required streaming port is unreachable.' }
if (-not $report.adminBlocked) { throw 'Unexpected remote Sunshine admin access; stop for review.' }
$run = Join-Path $env:LOCALAPPDATA ('EdSys-Private\moonlight-setup\' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run -Force | Out-Null
$result = Join-Path $run 'result.json'
try {
    if ($existing.Count -gt 0) {
        $exe = $existing[0]
    } else {
        $zip = Join-Path $PSScriptRoot 'MoonlightPortable-x64-6.1.0.zip'
        if ((Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash -ine '95f4d0853a31c7fced4b6d233ddf55ee41720963f2e2620a9cb49a21d112aed1') {
            throw 'Official portable archive checksum mismatch.'
        }
        if (Test-Path -LiteralPath $root) { throw 'Portable target exists without Moonlight.exe; inspect before retry.' }
        $staging = Join-Path $run 'staging'
        Expand-Archive -LiteralPath $zip -DestinationPath $staging
        $stagedExe = Join-Path $staging 'Moonlight.exe'
        $sig = Get-AuthenticodeSignature -LiteralPath $stagedExe
        if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch '(CN=Cameron Gutman,|Moonlight Game Streaming Project)') {
            throw 'Official Moonlight publisher signature validation failed.'
        }
        New-Item -ItemType Directory -Path (Split-Path $root) -Force | Out-Null
        Move-Item -LiteralPath $staging -Destination $root
        $exe = Join-Path $root 'Moonlight.exe'
        $report.action = 'portable-installed'
    }
    $sig = Get-AuthenticodeSignature -LiteralPath $exe
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch '(CN=Cameron Gutman,|Moonlight Game Streaming Project)') {
        throw 'Existing Moonlight publisher signature validation failed.'
    }
    $report.version = (Get-Item -LiteralPath $exe).VersionInfo.FileVersion
    $report.signature = [string]$sig.Status
    if (Get-Process Moonlight -ErrorAction SilentlyContinue) {
        throw 'Close Moonlight on this laptop, then rerun setup so pairing uses the correct process.'
    }
    # Probe only the app listing first so a previously paired client is retained.
    $appList = Join-Path $run 'apps.txt'
    $appErrors = Join-Path $run 'apps-error.txt'
    $list = Start-Process -FilePath $exe -ArgumentList "list $HubTailnetAddress" -PassThru -RedirectStandardOutput $appList -RedirectStandardError $appErrors
    if (-not $list.WaitForExit(15000)) {
        Stop-Process -Id $list.Id -ErrorAction SilentlyContinue
    } else {
        $list.Refresh()
        $report.paired = ($list.ExitCode -eq 0 -and (Get-Content $appList -Raw) -match 'Desktop')
    }
    if (-not $report.paired) {
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        $bytes = New-Object byte[] 4
        $rng.GetBytes($bytes); $rng.Dispose()
        $pin = ([BitConverter]::ToUInt32($bytes,0) % 10000).ToString('D4')
        $pair = Start-Process -FilePath $exe -ArgumentList "pair $HubTailnetAddress --pin $pin" -PassThru
        Start-Sleep -Seconds 2
        $pin | & ssh.exe @sshOptions $HubHost "python3 $HubBundleDirectory/pair-local.py"
        $pairAccepted = ($LASTEXITCODE -eq 0)
        $pin = $null
        if (-not $pair.WaitForExit(30000)) { throw 'Pairing did not finish; close its window and retry.' }
        $pair.Refresh()
        if (-not $pairAccepted -or $pair.ExitCode -ne 0) { throw 'Pairing did not complete.' }
        $list = Start-Process -FilePath $exe -ArgumentList "list $HubTailnetAddress" -PassThru -RedirectStandardOutput $appList -RedirectStandardError $appErrors
        if (-not $list.WaitForExit(15000)) { throw 'Paired application verification timed out.' }
        $list.Refresh()
        if ($list.ExitCode -ne 0 -or (Get-Content $appList -Raw) -notmatch 'Desktop') { throw 'Desktop app was not verified after pairing.' }
        $report.paired = $true
    }
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shell = New-Object -ComObject WScript.Shell
    foreach ($name in @('9950x Desktop.lnk','9950x Desktop (Tailscale).lnk')) {
        $path = Join-Path $desktop $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $run ($name + '.before')) }
        $shortcut = $shell.CreateShortcut($path)
        $shortcut.TargetPath = $exe
        $shortcut.Arguments = "--display-mode borderless --resolution 1920x1080 --fps 60 --bitrate 15000 --video-codec HEVC --video-decoder hardware --capture-system-keys always --absolute-mouse --quit-after stream $HubTailnetAddress Desktop"
        $shortcut.WorkingDirectory = Split-Path $exe
        $shortcut.IconLocation = "$exe,0"
        $shortcut.Description = '9950x physical desktop over Tailscale; temporary 1080p display with restoration'
        $shortcut.Save()
        $verified = $shell.CreateShortcut($path)
        if ($verified.TargetPath -ne $exe -or $verified.Arguments -notlike "*stream $HubTailnetAddress Desktop") { throw 'Shortcut verification failed.' }
    }
    $report.shortcuts = $true
    $report.action = 'setup-verified'
    Write-Host 'Setup verified. Open 9950x Desktop to connect. Quit the stream with Ctrl+Alt+Shift+Q.'
} catch {
    $report.action = 'incomplete'
    # Detailed errors remain in the local console; no private process state is uploaded.
    throw
} finally {
    $report | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $result -Encoding UTF8
    & scp.exe -q @sshOptions $result ($HubHost + ':' + $HubBundleDirectory + '/result-' + (Split-Path $run -Leaf) + '.json')
    if ($LASTEXITCODE -ne 0) { Write-Warning "Result upload failed; report is at $result" }
}
