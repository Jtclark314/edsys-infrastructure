[CmdletBinding()]
param(
    [string]$InstallRoot = "C:\EdSys\FoothillsOffsiteBackup",
    [string]$OutputRoot = "C:\Foothills\OffsiteBackup"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-JsonFile {
    param(
        [Parameter(Mandatory = $true)]$Value,
        [Parameter(Mandatory = $true)][string]$Path,
        [int]$Depth = 8
    )
    $Value | ConvertTo-Json -Depth $Depth | Set-Content -LiteralPath $Path -Encoding UTF8
}

function Invoke-ApplicationBackup {
    param(
        [Parameter(Mandatory = $true)][string]$Script,
        [string[]]$Arguments = @()
    )
    if (-not (Test-Path -LiteralPath $Script)) {
        throw "Required application backup script is missing: $Script"
    }
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Application backup script failed ($LASTEXITCODE): $Script"
    }
}

$StageScript = Join-Path $InstallRoot "basecamp_foothills_stage.py"
if (-not (Test-Path -LiteralPath $StageScript)) {
    throw "Basecamp staging helper is missing: $StageScript"
}

New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null
# Restrict the root before any private recovery material is staged.
& icacls.exe $OutputRoot /inheritance:r | Out-Null
& icacls.exe $OutputRoot /grant:r `
    "SYSTEM:(OI)(CI)(F)" `
    "BUILTIN\Administrators:(OI)(CI)(F)" `
    "$env:COMPUTERNAME\jeremy:(OI)(CI)(RX)" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to apply the private backup ACL."
}
$RecoveryRoot = Join-Path $env:ProgramData ("EdSys\FoothillsOffsiteBackup\recovery-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $RecoveryRoot -Force | Out-Null

try {
    Invoke-ApplicationBackup `
        -Script "C:\Foothills\UnitSelections\operations\Backup-UnitSelections.ps1" `
        -Arguments @("-InstallRoot", "C:\Foothills\UnitSelections")
    Invoke-ApplicationBackup `
        -Script "C:\EdSys\KindleDrop\operations\Backup-KindleDrop.ps1"

    $TaskRoot = Join-Path $RecoveryRoot "scheduled-tasks"
    New-Item -ItemType Directory -Path $TaskRoot -Force | Out-Null
    $Tasks = Get-ScheduledTask | Where-Object {
        $_.TaskName -match "(?i)(Foothills|Kindle Drop|Speakr)"
    }
    foreach ($Task in $Tasks) {
        $SafeName = ($Task.TaskPath.Trim("\") + "-" + $Task.TaskName) -replace "[^A-Za-z0-9._-]", "_"
        Export-ScheduledTask -TaskName $Task.TaskName -TaskPath $Task.TaskPath |
            Set-Content -LiteralPath (Join-Path $TaskRoot ($SafeName + ".xml")) -Encoding UTF8
    }

    $Services = Get-CimInstance Win32_Service | Where-Object {
        $_.Name -match "(?i)(Agent|cloudflared|sshd|Tailscale)" -or
        $_.DisplayName -match "(?i)(Agent DVR|Cloudflare|OpenSSH|Tailscale)"
    } | Select-Object Name, DisplayName, State, StartMode, StartName, PathName
    Write-JsonFile -Value @($Services) -Path (Join-Path $RecoveryRoot "services.json")

    $FirewallRules = Get-NetFirewallRule | Where-Object {
        $_.DisplayName -match "(?i)(Foothills|Agent|Speakr|Cloudflare|Tailscale|OpenSSH)"
    }
    Write-JsonFile `
        -Value @($FirewallRules | Select-Object Name, DisplayName, Enabled, Direction, Action, Profile, Service) `
        -Path (Join-Path $RecoveryRoot "firewall-rules.json")
    Write-JsonFile `
        -Value @($FirewallRules | Get-NetFirewallPortFilter | Select-Object InstanceID, Protocol, LocalPort, RemotePort) `
        -Path (Join-Path $RecoveryRoot "firewall-ports.json")
    Write-JsonFile `
        -Value @($FirewallRules | Get-NetFirewallAddressFilter | Select-Object InstanceID, LocalAddress, RemoteAddress) `
        -Path (Join-Path $RecoveryRoot "firewall-addresses.json")

    $Shares = Get-SmbShare | Where-Object {
        $_.Name -match "(?i)(Foothills|Unit)"
    }
    Write-JsonFile `
        -Value @($Shares | Select-Object Name, Path, Description, EncryptData, FolderEnumerationMode) `
        -Path (Join-Path $RecoveryRoot "smb-shares.json")
    $ShareAccess = foreach ($Share in $Shares) {
        Get-SmbShareAccess -Name $Share.Name | Select-Object @{
            Name = "ShareName"
            Expression = { $Share.Name }
        }, AccountName, AccessControlType, AccessRight
    }
    Write-JsonFile -Value @($ShareAccess) -Path (Join-Path $RecoveryRoot "smb-share-access.json")

    $Computer = Get-CimInstance Win32_ComputerSystem |
        Select-Object Name, Manufacturer, Model, Domain, PartOfDomain
    $OperatingSystem = Get-CimInstance Win32_OperatingSystem |
        Select-Object Caption, Version, BuildNumber, OSArchitecture, LastBootUpTime
    Write-JsonFile `
        -Value ([ordered]@{
            computer = $Computer
            operating_system = $OperatingSystem
            captured_at = (Get-Date).ToUniversalTime().ToString("o")
        }) `
        -Path (Join-Path $RecoveryRoot "host.json")

    & py -3 $StageScript --output-root $OutputRoot --recovery-source $RecoveryRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Basecamp recovery staging failed with exit code $LASTEXITCODE."
    }

    # Reapply recursively because the stage may hold passwords, tunnel tokens,
    # and private host keys copied from trees with their own ACLs.
    & icacls.exe $OutputRoot /inheritance:r | Out-Null
    & icacls.exe $OutputRoot /grant:r `
        "SYSTEM:(OI)(CI)(F)" `
        "BUILTIN\Administrators:(OI)(CI)(F)" `
        "$env:COMPUTERNAME\jeremy:(OI)(CI)(RX)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to apply the private backup ACL."
    }
}
finally {
    if (Test-Path -LiteralPath $RecoveryRoot) {
        Remove-Item -LiteralPath $RecoveryRoot -Recurse -Force
    }
}
