param(
    [string]$SourceRoot = $PSScriptRoot,
    [string]$InstallRoot = "C:\EdSys\KindleDrop",
    [string]$StateRoot = "C:\ProgramData\EdSys\KindleDrop",
    [string]$TailnetIPv4 = "100.120.155.81",
    [string]$MonitorIPv4 = "100.87.137.47",
    [string]$SiteLanCIDR = "192.168.1.0/24",
    [string[]]$ApprovedTailnetClients = @("100.87.137.47")
)

$ErrorActionPreference = "Stop"
$Identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$PrincipalCheck = [Security.Principal.WindowsPrincipal]::new($Identity)
if (-not $PrincipalCheck.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer from an elevated PowerShell session."
}

foreach ($Required in @(
    "kindle_drop.py",
    "commission_gmail.py",
    "tailnet_deny_ranges.py",
    "config.example.json",
    "requirements.txt",
    "Start-KindleDrop.ps1",
    "Health-KindleDrop.ps1",
    "Backup-KindleDrop.ps1",
    "New-KindleDropClient.ps1"
)) {
    if (-not (Test-Path (Join-Path $SourceRoot $Required))) {
        throw "Required deployment file is missing: $Required"
    }
}

$Python = (Get-Command py.exe -ErrorAction Stop).Source
$PythonVersion = & $Python -3.12 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($PythonVersion.Trim() -ne "3.12") {
    throw "Python 3.12 is required."
}

$ReleaseId = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$ReleaseRoot = Join-Path $InstallRoot "releases\$ReleaseId"
$ShareRoot = Join-Path $InstallRoot "share"
$OperationsRoot = Join-Path $InstallRoot "operations"
$BackupsRoot = Join-Path $InstallRoot "backups"
$SettingsPath = Join-Path $InstallRoot "settings.json"
$PrivateRoot = Join-Path $StateRoot "private"
$ConfigTemplate = Get-Content (Join-Path $SourceRoot "config.example.json") -Raw |
    ConvertFrom-Json

New-Item -ItemType Directory -Force -Path `
    $ReleaseRoot, $ShareRoot, $OperationsRoot, $BackupsRoot, `
    (Join-Path $StateRoot "processing"), `
    (Join-Path $StateRoot "state"), `
    (Join-Path $StateRoot "logs"), `
    $PrivateRoot | Out-Null

Copy-Item -Force (Join-Path $SourceRoot "kindle_drop.py") $ReleaseRoot
Copy-Item -Force (Join-Path $SourceRoot "commission_gmail.py") $ReleaseRoot
Copy-Item -Force (Join-Path $SourceRoot "tailnet_deny_ranges.py") $ReleaseRoot
Copy-Item -Force (Join-Path $SourceRoot "requirements.txt") $ReleaseRoot
Copy-Item -Recurse -Force (Join-Path $SourceRoot "tests") $ReleaseRoot
Copy-Item -Force (Join-Path $SourceRoot "Start-KindleDrop.ps1") $OperationsRoot
Copy-Item -Force (Join-Path $SourceRoot "Health-KindleDrop.ps1") $OperationsRoot
Copy-Item -Force (Join-Path $SourceRoot "Backup-KindleDrop.ps1") $OperationsRoot
Copy-Item -Force (Join-Path $SourceRoot "New-KindleDropClient.ps1") $OperationsRoot

& $Python -3.12 -m venv (Join-Path $ReleaseRoot ".venv")
$ReleasePython = Join-Path $ReleaseRoot ".venv\Scripts\python.exe"
& $ReleasePython -m pip install --disable-pip-version-check `
    -r (Join-Path $ReleaseRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Pinned Python dependency installation failed."
}
Push-Location $ReleaseRoot
try {
    & $ReleasePython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw "Kindle Drop tests failed."
    }
} finally {
    Pop-Location
}

$ConfigTemplate.share_root = $ShareRoot
$ConfigTemplate.state_root = $StateRoot
$ConfigTemplate.private_blob_path = Join-Path $PrivateRoot "gmail-private.dpapi"
$ConfigTemplate.listen_ip = $TailnetIPv4
$ConfigTemplate.listen_port = 8094
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[IO.File]::WriteAllText(
    $SettingsPath,
    ($ConfigTemplate | ConvertTo-Json -Depth 10),
    $Utf8NoBom
)
$ReleaseRoot | Set-Content -Encoding ascii (Join-Path $InstallRoot "current.txt")

foreach ($Folder in @(
    "00-Drop-PDF-Here",
    "01-Resend",
    "10-Needs-Web-Upload",
    "20-Failed",
    "30-Returned-Annotated",
    "90-Receipts"
)) {
    New-Item -ItemType Directory -Force -Path (Join-Path $ShareRoot $Folder) |
        Out-Null
}

@"
BASECAMP KINDLE DROP
====================

This is a private Tailnet share. Placing a PDF in 00-Drop-PDF-Here explicitly
authorizes this service to submit the document to Amazon Send to Kindle.

Normal use:
  1. Copy a PDF into 00-Drop-PDF-Here.
  2. Wait for a JSON status record in 90-Receipts.
  3. "submitted" means Gmail accepted the message; it does not prove Kindle
     delivery. Check the Kindle library.

Use 01-Resend only to intentionally override duplicate-hash suppression.
PDFs over 18 MiB move unchanged to 10-Needs-Web-Upload for manual use of
Amazon Send to Kindle Web. Invalid files move to 20-Failed.
Scribe exports captured from the dedicated mailbox remain in
30-Returned-Annotated until you file or delete them.

Windows:
  \\basecamp.taile832fe.ts.net\Kindle-Drop
  Map to K: if K: is available.

iPhone/iPad Files:
  Connect to Server: smb://basecamp.taile832fe.ts.net/Kindle-Drop

Use the separately issued credential for this device. Do not enable offline
caching. The share is not available through the public Internet.
"@ | ForEach-Object {
    [IO.File]::WriteAllText((Join-Path $ShareRoot "README.txt"), $_, $Utf8NoBom)
}

if (-not (Get-LocalGroup -Name "KindleDropClients" -ErrorAction SilentlyContinue)) {
    New-LocalGroup -Name "KindleDropClients" `
        -Description "SMB-only Kindle Drop device identities" |
        Out-Null
}

icacls.exe $ShareRoot /inheritance:r /grant:r `
    "SYSTEM:(OI)(CI)(F)" `
    "BUILTIN\Administrators:(OI)(CI)(F)" `
    "NT AUTHORITY\LOCAL SERVICE:(OI)(CI)(F)" `
    "$env:COMPUTERNAME\KindleDropClients:(OI)(CI)(M)" | Out-Null
icacls.exe $StateRoot /inheritance:r /grant:r `
    "SYSTEM:(OI)(CI)(F)" `
    "BUILTIN\Administrators:(OI)(CI)(F)" `
    "NT AUTHORITY\LOCAL SERVICE:(OI)(CI)(M)" | Out-Null
icacls.exe $BackupsRoot /inheritance:r /grant:r `
    "SYSTEM:(OI)(CI)(F)" `
    "BUILTIN\Administrators:(OI)(CI)(F)" `
    "NT AUTHORITY\LOCAL SERVICE:(OI)(CI)(M)" | Out-Null

$ExistingShare = Get-SmbShare -Name "Kindle-Drop" -ErrorAction SilentlyContinue
if ($ExistingShare -and $ExistingShare.Path -ne $ShareRoot) {
    throw "Existing Kindle-Drop share points to an unexpected path: $($ExistingShare.Path)"
}
if (-not $ExistingShare) {
    New-SmbShare -Name "Kindle-Drop" -Path $ShareRoot `
        -FullAccess "BUILTIN\Administrators" `
        -ChangeAccess "$env:COMPUTERNAME\KindleDropClients" `
        -EncryptData $true -CachingMode None -FolderEnumerationMode AccessBased `
        -Description "Private Tailnet PDF intake for Amazon Send to Kindle" |
        Out-Null
} else {
    Set-SmbShare -Name "Kindle-Drop" -EncryptData $true -CachingMode None `
        -FolderEnumerationMode AccessBased -Force
}

Set-SmbServerConfiguration -EnableSMB1Protocol $false `
    -RequireSecuritySignature $true -RejectUnencryptedAccess $true -Force |
    Out-Null

# Windows classifies Tailscale peers as LocalSubnet. Preserve the established
# site-LAN behavior while preventing the built-in rule from bypassing the
# explicit Tailnet allowlist created below.
# Scope both Windows TCP/445 rule groups to the established site LAN.
foreach ($DisplayName in @(
    "File and Printer Sharing (SMB-In)",
    "File and Printer Sharing (Restrictive) (SMB-In)"
)) {
    Get-NetFirewallRule -DisplayName $DisplayName -ErrorAction Stop |
        Where-Object { $_.Enabled -eq "True" } |
        Get-NetFirewallAddressFilter |
        Set-NetFirewallAddressFilter -RemoteAddress $SiteLanCIDR | Out-Null
}

$StartScript = Join-Path $OperationsRoot "Start-KindleDrop.ps1"
$HealthScript = Join-Path $OperationsRoot "Health-KindleDrop.ps1"
$BackupScript = Join-Path $OperationsRoot "Backup-KindleDrop.ps1"

# Quiesce only the previously managed dispatcher immediately before task
# replacement. Dependency installation and tests happen before this point.
if (Get-ScheduledTask -TaskName "EdSys Kindle Drop Health" -ErrorAction SilentlyContinue) {
    Disable-ScheduledTask -TaskName "EdSys Kindle Drop Health" | Out-Null
    Stop-ScheduledTask -TaskName "EdSys Kindle Drop Health" -ErrorAction SilentlyContinue
}
if (Get-ScheduledTask -TaskName "EdSys Kindle Drop" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "EdSys Kindle Drop" -ErrorAction SilentlyContinue
    $StopDeadline = (Get-Date).AddSeconds(30)
    do {
        Start-Sleep -Seconds 1
        $OldListener = Get-NetTCPConnection -State Listen -LocalAddress $TailnetIPv4 `
            -LocalPort 8094 -ErrorAction SilentlyContinue
    } while ($OldListener -and (Get-Date) -lt $StopDeadline)
    if ($OldListener) {
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
            Where-Object {
                $_.CommandLine -like ("*" + $InstallRoot + "*kindle_drop.py*")
            } |
            ForEach-Object {
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }
    }
}

$DispatcherAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$StartScript`"" `
    -WorkingDirectory $OperationsRoot
$DispatcherTrigger = New-ScheduledTaskTrigger -AtStartup
$DispatcherTrigger.Delay = "PT1M"
$DispatcherPrincipal = New-ScheduledTaskPrincipal `
    -UserId "NT AUTHORITY\LOCAL SERVICE" -LogonType ServiceAccount -RunLevel Limited
$DispatcherSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -StartWhenAvailable `
    -RestartCount 8 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName "EdSys Kindle Drop" `
    -Action $DispatcherAction -Trigger $DispatcherTrigger `
    -Principal $DispatcherPrincipal -Settings $DispatcherSettings `
    -Description "Validates and submits intentional Kindle Drop PDFs; captures authenticated Scribe returns." `
    -Force | Out-Null

$HealthAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$HealthScript`"" `
    -WorkingDirectory $OperationsRoot
$HealthTrigger = New-ScheduledTaskTrigger -Once `
    -At ((Get-Date).AddMinutes(1)) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)
$SystemPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$HealthSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 3) -StartWhenAvailable
Register-ScheduledTask -TaskName "EdSys Kindle Drop Health" `
    -Action $HealthAction -Trigger $HealthTrigger `
    -Principal $SystemPrincipal -Settings $HealthSettings `
    -Description "Repairs only the expected Kindle Drop dispatcher when its listener is absent." `
    -Force | Out-Null

$BackupAction = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$BackupScript`"" `
    -WorkingDirectory $OperationsRoot
$BackupTrigger = New-ScheduledTaskTrigger -Daily -At "02:20"
$BackupSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -StartWhenAvailable
Register-ScheduledTask -TaskName "EdSys Kindle Drop Backup" `
    -Action $BackupAction -Trigger $BackupTrigger `
    -Principal $SystemPrincipal -Settings $BackupSettings `
    -Description "Creates an online SQLite snapshot with receipts and retained annotated returns." `
    -Force | Out-Null

Remove-NetFirewallRule -DisplayName "EdSys Kindle Drop Health Tailnet" `
    -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName "EdSys Kindle Drop Health Tailnet" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8094 `
    -LocalAddress $TailnetIPv4 -RemoteAddress $MonitorIPv4 `
    -InterfaceAlias "Tailscale" -Profile Any | Out-Null

Remove-NetFirewallRule -DisplayName "EdSys Kindle Drop SMB Tailnet" `
    -ErrorAction SilentlyContinue
if ($ApprovedTailnetClients.Count -gt 0) {
    New-NetFirewallRule -DisplayName "EdSys Kindle Drop SMB Tailnet" `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 445 `
        -LocalAddress $TailnetIPv4 -RemoteAddress $ApprovedTailnetClients `
        -InterfaceAlias "Tailscale" -Profile Any | Out-Null
}

Remove-NetFirewallRule -DisplayName "EdSys Kindle Drop SMB Tailnet Deny" `
    -ErrorAction SilentlyContinue
$DenyRangeScript = Join-Path $ReleaseRoot "tailnet_deny_ranges.py"
$DeniedTailnetRanges = @(& $ReleasePython $DenyRangeScript $ApprovedTailnetClients)
if ($LASTEXITCODE -ne 0 -or $DeniedTailnetRanges.Count -eq 0) {
    throw "Failed to calculate the Tailnet SMB deny ranges."
}
New-NetFirewallRule -DisplayName "EdSys Kindle Drop SMB Tailnet Deny" `
    -Direction Inbound -Action Block -Protocol TCP -LocalPort 445 `
    -LocalAddress $TailnetIPv4 -RemoteAddress $DeniedTailnetRanges `
    -InterfaceAlias "Tailscale" -Profile Any | Out-Null

Start-ScheduledTask -TaskName "EdSys Kindle Drop"
$Deadline = (Get-Date).AddSeconds(90)
do {
    Start-Sleep -Seconds 3
    $Listener = Get-NetTCPConnection -State Listen -LocalAddress $TailnetIPv4 `
        -LocalPort 8094 -ErrorAction SilentlyContinue
} while (-not $Listener -and (Get-Date) -lt $Deadline)
if (-not $Listener) {
    throw "Kindle Drop did not open its exact Tailnet health listener."
}

Start-ScheduledTask -TaskName "EdSys Kindle Drop Health"
Start-ScheduledTask -TaskName "EdSys Kindle Drop Backup"

Write-Host "Installed Kindle Drop release $ReleaseId."
Write-Host "Share: \\basecamp.taile832fe.ts.net\Kindle-Drop"
Write-Host "Health: http://$TailnetIPv4`:8094/healthz"
Write-Host "The service intentionally reports commissioning/503 until the dedicated"
Write-Host "Gmail, Kindle destination, and proven Amazon sender/host allowlists are stored."
