#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidateSet('Plan', 'Install', 'Verify', 'Revoke')]
    [string]$Action = 'Plan',
    [string]$ManifestPath,
    [switch]$EmployerApproved
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# Windows PowerShell can bind defaults before the script-root automatic
# variable is available. Resolve file-relative paths only in the script body.
$installerPath = $PSCommandPath
if ([string]::IsNullOrWhiteSpace($installerPath)) { $installerPath = $MyInvocation.MyCommand.Path }
if ([string]::IsNullOrWhiteSpace($installerPath)) { throw 'Run the saved installer with -File; its source path could not be resolved.' }
$scriptDirectory = Split-Path -Parent $installerPath
if ([string]::IsNullOrWhiteSpace($ManifestPath)) { $ManifestPath = Join-Path $scriptDirectory 'access.json' }

function Assert-TailnetIPv4 {
    param([string]$Value)
    $ip = $null
    if ($Value -notmatch '^\d{1,3}(\.\d{1,3}){3}$' -or
        -not [Net.IPAddress]::TryParse($Value, [ref]$ip) -or
        $ip.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork -or
        $ip.ToString() -cne $Value) { throw 'Expected one canonical Tailnet IPv4 address.' }
    $bytes = $ip.GetAddressBytes()
    if ($bytes[0] -ne 100 -or $bytes[1] -lt 64 -or $bytes[1] -gt 127) {
        throw 'Only addresses in the Tailscale IPv4 range are accepted.'
    }
}

function Read-AccessManifest {
    param([string]$Path)
    if ((Get-Item -LiteralPath $Path).Length -gt 16384) { throw 'Oversized manifest.' }
    $m = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    if ($m.schemaVersion -ne 1 -or $m.computer -cne 'THOMPSON-LC086' -or
        $m.user -cne 'thompson\jclark') { throw 'Manifest endpoint does not match this installer.' }
    Assert-TailnetIPv4 $m.hubAddress
    Assert-TailnetIPv4 $m.laptopAddress
    if ($m.hubAddress -eq $m.laptopAddress) { throw 'Hub and laptop must be distinct peers.' }
    # One bare public key, no authorized_keys options or injected directives.
    if ($m.publicKey -cnotmatch '^ssh-ed25519 ([A-Za-z0-9+/]+={0,2})$') {
        throw 'Expected one bare Ed25519 public key, without options or comments.'
    }
    $blob = [Convert]::FromBase64String($Matches[1])
    if ($blob.Length -ne 51 -or
        [BitConverter]::ToString($blob[0..3]) -ne '00-00-00-0B' -or
        [Text.Encoding]::ASCII.GetString($blob, 4, 11) -cne 'ssh-ed25519' -or
        [BitConverter]::ToString($blob[15..18]) -ne '00-00-00-20') {
        throw 'Malformed Ed25519 key.'
    }
    return $m
}

function Get-OtherIPv4Ranges {
    param([string]$Allowed)
    Assert-TailnetIPv4 $Allowed
    $b = [Net.IPAddress]::Parse($Allowed).GetAddressBytes()
    [uint64]$n = [uint64]$b[0] * 16777216 + [uint64]$b[1] * 65536 + [uint64]$b[2] * 256 + $b[3]
    function Convert-NumberToIP([uint64]$Value) {
        return '{0}.{1}.{2}.{3}' -f (($Value -shr 24) -band 255), (($Value -shr 16) -band 255), (($Value -shr 8) -band 255), ($Value -band 255)
    }
    return @(('0.0.0.0-' + (Convert-NumberToIP ($n - 1))), ((Convert-NumberToIP ($n + 1)) + '-255.255.255.255'))
}

function Get-ServerConfig {
    param($Manifest, [string]$SshDirectory)
    $dir = $SshDirectory.Replace('\', '/')
    if ($dir -match '["\r\n]') { throw 'Unsafe SSH directory.' }
    return @"
# Managed by EdSys Manage-WorkLaptopAccess.ps1. Fresh installation only.
Port 22
AddressFamily inet
ListenAddress $($Manifest.laptopAddress)
HostKey "$dir/ssh_host_ed25519_key"
AuthorizedKeysFile "$dir/administrators_authorized_keys"
AllowUsers $($Manifest.user)
PubkeyAuthentication yes
PasswordAuthentication no
AuthenticationMethods publickey
PermitEmptyPasswords no
AllowAgentForwarding no
AllowTcpForwarding no
GatewayPorts no
LoginGraceTime 30
MaxAuthTries 3
LogLevel VERBOSE
SyslogFacility AUTH
Subsystem sftp internal-sftp
"@
}

function Invoke-Native {
    param([string]$File, [string[]]$Arguments)
    $output = & $File @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) { throw "Native command failed: $File (exit $LASTEXITCODE): $($output | Out-String)" }
    return $output
}

function Set-AdminOnlyAcl {
    param([string]$Path, [switch]$Directory)
    $admins = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    if ($Directory) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
        $inherit = [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit'
    } else {
        $acl = New-Object Security.AccessControl.FileSecurity
        $inherit = [Security.AccessControl.InheritanceFlags]::None
    }
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($admins)
    foreach ($sid in @($admins, $system)) {
        $rule = New-Object Security.AccessControl.FileSystemAccessRule(
            $sid, 'FullControl', $inherit, 'None', 'Allow')
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Assert-AdminOnlyAcl {
    param([string]$Path)
    $acl = Get-Acl -LiteralPath $Path
    if (-not $acl.AreAccessRulesProtected -or
        $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-32-544', 'S-1-5-18')) {
        throw "Unprotected owner/ACL: $Path"
    }
    $rules = @($acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier]))
    if ($rules.Count -ne 2) { throw "Unexpected ACL entries: $Path" }
    foreach ($sid in @('S-1-5-32-544', 'S-1-5-18')) {
        $match = @($rules | Where-Object { $_.IdentityReference.Value -eq $sid -and
            $_.AccessControlType -eq 'Allow' -and $_.FileSystemRights -eq 'FullControl' })
        if ($match.Count -ne 1) { throw "Missing administrator/system access: $Path" }
    }
}

function Write-PrivateText {
    param([string]$Path, [string]$Text)
    [IO.File]::WriteAllText($Path, $Text, (New-Object Text.UTF8Encoding($false)))
    Set-AdminOnlyAcl $Path
}

function Assert-NoReparsePath {
    param([string]$Path)
    $item = [IO.Path]::GetFullPath($Path)
    while ($item) {
        if (Test-Path -LiteralPath $item) {
            if ((Get-Item -LiteralPath $item -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Refusing a redirected installation path: $item"
            }
        }
        $item = [IO.Path]::GetDirectoryName($item)
    }
}

function Save-Receipt {
    param([string]$Status)
    $script:receipt.status = $Status
    $script:receipt.updatedAt = (Get-Date).ToUniversalTime().ToString('o')
    Write-PrivateText $script:receiptPath ($script:receipt | ConvertTo-Json -Depth 8)
}

function Assert-ManagedFilesUnchanged {
    if ((Get-FileHash $script:configPath -Algorithm SHA256).Hash -cne $script:receipt.configHash -or
        (Get-FileHash $script:keyPath -Algorithm SHA256).Hash -cne $script:receipt.keyHash) {
        throw 'Managed SSH configuration/key drifted; no automatic rewrite is permitted.'
    }
    $service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    if ($service.PathName.Trim('"') -ine $script:sshd -or $service.StartName -ne 'LocalSystem') {
        throw 'SSHD service ownership changed; local operator review is required.'
    }
}

function Close-ManagedAccess {
    # Called only after this installer owns the fresh SSH service. Preserve a
    # blocking rule if stopping the service fails; never restore an open rule.
    $svc = Get-Service sshd -ErrorAction SilentlyContinue
    if ($svc) {
        Set-Service sshd -StartupType Disabled
        if ($svc.Status -ne 'Stopped') { Stop-Service sshd -Force -ErrorAction Stop }
        if ((Get-Service sshd).Status -ne 'Stopped') { throw 'SSHD did not stop; containment rule retained.' }
    }
    foreach ($name in @($script:allowRule, $script:denyRule)) {
        Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    if (Test-Path -LiteralPath $script:keyPath) { Write-PrivateText $script:keyPath '# Access revoked by EdSys.' }
    Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    Get-NetFirewallRule -Name $script:containRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
}

function Test-ManagedAccess {
    param($Manifest)
    foreach ($profile in @(Get-NetFirewallProfile -PolicyStore ActiveStore)) {
        if ($profile.Enabled -ne 'True' -or $profile.DefaultInboundAction -ne 'Block' -or
            $profile.AllowLocalFirewallRules -eq 'False') { throw 'Effective firewall policy no longer matches the installation requirements.' }
    }
    foreach ($path in @($script:stateRoot, $script:sshRoot, $script:configPath, $script:keyPath,
            $script:hostKey, $script:receiptPath)) { Assert-AdminOnlyAcl $path }
    Assert-ManagedFilesUnchanged
    Invoke-Native $script:sshd @('-t', '-f', $script:configPath) | Out-Null
    $effective = Invoke-Native $script:sshd @('-T', '-f', $script:configPath)
    foreach ($line in @('passwordauthentication no', 'pubkeyauthentication yes',
            'authenticationmethods publickey', 'allowtcpforwarding no', 'allowagentforwarding no',
            "allowusers $($Manifest.user)", "listenaddress $($Manifest.laptopAddress):22")) {
        if ($effective -notcontains $line) { throw "Missing effective SSH restriction: $line" }
    }
    $svc = Get-CimInstance Win32_Service -Filter "Name='sshd'"
    if ($svc.State -ne 'Running' -or $svc.StartMode -ne 'Auto' -or $svc.StartName -ne 'LocalSystem') {
        throw 'SSHD service is not running automatically as LocalSystem.'
    }
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction Stop)
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne $Manifest.laptopAddress -or
        $listeners[0].OwningProcess -ne $svc.ProcessId) { throw 'Unexpected SSH listener or owner.' }
    $rule = Get-NetFirewallRule -PolicyStore ActiveStore -Name $script:allowRule
    $addresses = $rule | Get-NetFirewallAddressFilter
    $ports = $rule | Get-NetFirewallPortFilter
    if ($rule.Enabled -ne 'True' -or $rule.Action -ne 'Allow' -or $rule.Direction -ne 'Inbound' -or
        @($addresses.RemoteAddress).Count -ne 1 -or $addresses.RemoteAddress -ne $Manifest.hubAddress -or
        @($addresses.LocalAddress).Count -ne 1 -or $addresses.LocalAddress -ne $Manifest.laptopAddress -or
        $ports.LocalPort -ne '22' -or $ports.Protocol -ne 'TCP') { throw 'Effective allow rule is not exact.' }
    $deny = Get-NetFirewallRule -PolicyStore ActiveStore -Name $script:denyRule
    $denyAddresses = $deny | Get-NetFirewallAddressFilter
    $denyPorts = $deny | Get-NetFirewallPortFilter
    $expectedRanges = @(Get-OtherIPv4Ranges $Manifest.hubAddress)
    if ($deny.Enabled -ne 'True' -or $deny.Action -ne 'Block' -or $deny.Direction -ne 'Inbound' -or
        $denyAddresses.LocalAddress -ne $Manifest.laptopAddress -or
        @(Compare-Object @($denyAddresses.RemoteAddress) $expectedRanges).Count -ne 0 -or
        $denyPorts.LocalPort -ne '22' -or $denyPorts.Protocol -ne 'TCP') { throw 'Effective exclusion rule is not exact.' }
    $serviceConfig = Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Services\sshd'
    if ($serviceConfig.DependOnService -notcontains 'Tailscale' -or
        $serviceConfig.DelayedAutoStart -ne 1 -or -not $serviceConfig.FailureActions -or
        $serviceConfig.FailureActionsOnNonCrashFailures -ne 1) { throw 'Boot/recovery settings are incomplete.' }
    Write-Host 'PASS: local configuration, ACLs, effective firewall, service, and listener.'
    Write-Host 'Hub login, file transfer, negative access checks, and reboot persistence still require acceptance.'
    Write-Host ('Host key: ' + ((Invoke-Native $script:keygen @('-lf', "$script:hostKey.pub")) -join ' '))
}

if ($env:OS -ne 'Windows_NT' -or $env:COMPUTERNAME -ine 'THOMPSON-LC086') {
    throw 'Run on Windows laptop THOMPSON-LC086 only.'
}
if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell.' }
if ($Action -in @('Install', 'Revoke') -and $env:SSH_CONNECTION) {
    throw 'Install/Revoke must run in a local elevated PowerShell window so closing SSH cannot interrupt recovery.'
}
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.Name -ine 'THOMPSON\jclark') { throw 'Run as THOMPSON\jclark; another administrator identity is not accepted.' }
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open PowerShell with Run as administrator. No privilege bypass or new administrator account is provided.'
}

$stateRoot = Join-Path $env:ProgramData 'EdSys-WorkLaptopAccess'
$sshRoot = Join-Path $env:ProgramData 'ssh'
$receiptPath = Join-Path $stateRoot 'receipt.json'
$configPath = Join-Path $sshRoot 'sshd_config'
$keyPath = Join-Path $sshRoot 'administrators_authorized_keys'
$hostKey = Join-Path $sshRoot 'ssh_host_ed25519_key'
$sshd = Join-Path $env:SystemRoot 'System32\OpenSSH\sshd.exe'
$keygen = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh-keygen.exe'
$allowRule = 'EdSys-WorkLaptop-SSH-9950x'
$denyRule = 'EdSys-WorkLaptop-SSH-DenyOthers'
$containRule = 'EdSys-WorkLaptop-SSH-InstallContainment'
foreach ($path in @($stateRoot, $sshRoot)) { Assert-NoReparsePath $path }

if ($Action -in @('Verify', 'Revoke') -or (Test-Path -LiteralPath $receiptPath)) {
    Assert-AdminOnlyAcl $stateRoot
    Assert-AdminOnlyAcl $receiptPath
    $receipt = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    if ($receipt.computer -cne 'THOMPSON-LC086' -or $receipt.owner -cne 'EdSys-WorkLaptopAccess-v1') {
        throw 'Unrecognized installation receipt.'
    }
    if ($Action -eq 'Revoke') {
        if ($receipt.status -in @('local-verified', 'configured')) { Assert-ManagedFilesUnchanged }
        Close-ManagedAccess
        Save-Receipt 'revoked'
        Write-Host 'REVOKED: SSH stopped/disabled, hub authorization removed, managed access rules removed.'
        Write-Host 'Windows capability, protected host keys, configuration, and receipt are retained for recovery.'
        return
    }
    if ($Action -eq 'Plan') { $receipt | Select-Object computer, status, updatedAt; return }
    if ($receipt.status -ne 'local-verified') { throw 'Prior installation is incomplete/revoked. Review recovery before reinstalling.' }
    $manifest = Read-AccessManifest (Join-Path $stateRoot 'access.json')
    Test-ManagedAccess $manifest
    return
}

$manifest = Read-AccessManifest $ManifestPath
$tailscale = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
$tail = (Invoke-Native $tailscale @('status', '--json')) -join "`n" | ConvertFrom-Json
if ($tail.BackendState -ne 'Running' -or $tail.Self.TailscaleIPs -notcontains $manifest.laptopAddress) {
    throw 'Tailscale is not connected at the expected laptop address.'
}
$hub = @($tail.Peer.PSObject.Properties.Value | Where-Object {
    $_.HostName -ieq '9950x' -and $_.TailscaleIPs -contains $manifest.hubAddress -and $_.Online
})
if ($hub.Count -ne 1) { throw 'The expected 9950x peer is not online in this Tailnet.' }
$tailService = Get-CimInstance Win32_Service -Filter "Name='Tailscale'"
if ($tailService.State -ne 'Running' -or $tailService.StartMode -ne 'Auto') { throw 'Tailscale must already run automatically.' }
foreach ($profile in @(Get-NetFirewallProfile -PolicyStore ActiveStore)) {
    if ($profile.Enabled -ne 'True' -or $profile.DefaultInboundAction -ne 'Block' -or
        $profile.AllowLocalFirewallRules -eq 'False') {
        throw 'Existing firewall policy does not permit this restricted local installation; refer to employer IT.'
    }
}
$capability = Get-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
if ($capability.State -ne 'NotPresent' -or (Get-Service sshd -ErrorAction SilentlyContinue) -or
    (Test-Path -LiteralPath $stateRoot) -or
    ((Test-Path -LiteralPath $sshRoot) -and @(Get-ChildItem $sshRoot -Force).Count -gt 0) -or
    (Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue)) {
    throw 'Existing SSH installation or recovery state detected. It will not be overwritten.'
}
foreach ($name in @($allowRule, $denyRule, $containRule)) {
    if (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue) { throw "Existing reserved firewall rule: $name" }
}
if (@(Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue).Count) {
    throw 'TCP 22 is already in use.'
}
$plan = [ordered]@{
    computer = $manifest.computer; user = $manifest.user
    transport = 'Windows OpenSSH over existing Tailscale, TCP 22'
    source = $manifest.hubAddress; bind = $manifest.laptopAddress
    authentication = 'Dedicated Ed25519 key only; existing administrator privileges'
    persistence = 'Automatic delayed service; Tailscale dependency; restart recovery'
    recovery = 'Revoke closes access; installed Windows capability and protected recovery files retained'
    graphicalDesktop = $false; employerPolicyChanges = $false
}
$plan | ConvertTo-Json
if ($Action -eq 'Plan') { return }
if (-not $EmployerApproved) { throw 'Install requires -EmployerApproved to attest employer/IT approval for persistent inbound administration.' }

# Nothing above this line mutates the laptop. A temporary block covers the
# Windows capability installer, which may create a broad default firewall rule.
$ownsInstallation = $false
try {
    New-NetFirewallRule -Name $containRule -DisplayName 'EdSys SSH installation containment' `
        -Direction Inbound -Action Block -Protocol TCP -LocalPort 22 -Profile Any | Out-Null
    New-Item -ItemType Directory -Path $stateRoot | Out-Null
    Set-AdminOnlyAcl $stateRoot -Directory
    $receipt = [ordered]@{ schemaVersion = 1; owner = 'EdSys-WorkLaptopAccess-v1'; computer = $manifest.computer;
        status = 'installing'; updatedAt = ''; configHash = ''; keyHash = '' }
    Save-Receipt 'installing'
    Copy-Item -LiteralPath $installerPath -Destination (Join-Path $stateRoot 'Manage-WorkLaptopAccess.ps1')
    Set-AdminOnlyAcl (Join-Path $stateRoot 'Manage-WorkLaptopAccess.ps1')
    Write-PrivateText (Join-Path $stateRoot 'access.json') ($manifest | ConvertTo-Json)
    $ownsInstallation = $true
    $result = Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0'
    Set-Service sshd -StartupType Disabled
    Stop-Service sshd -ErrorAction Stop
    Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    if ($result.RestartNeeded) { throw 'Windows requests a restart before continuing; access remains disabled.' }
    New-Item -ItemType Directory -Path $sshRoot -Force | Out-Null
    Set-AdminOnlyAcl $sshRoot -Directory
    foreach ($item in @(Get-ChildItem $sshRoot -Force)) {
        Copy-Item -LiteralPath $item.FullName -Destination $stateRoot -Recurse
    }
    Write-PrivateText $configPath (Get-ServerConfig $manifest $sshRoot)
    Write-PrivateText $keyPath ('from="{0}",no-agent-forwarding,no-port-forwarding,no-X11-forwarding {1} edsys-9950x-work-laptop' -f $manifest.hubAddress, $manifest.publicKey)
    if (-not (Test-Path -LiteralPath $hostKey)) {
        # -A uses the standard system paths and needs no passphrase argument.
        Invoke-Native $keygen @('-A') | Out-Null
    }
    foreach ($file in @(Get-ChildItem $sshRoot -File)) { Set-AdminOnlyAcl $file.FullName }
    Invoke-Native $sshd @('-t', '-f', $configPath) | Out-Null
    $receipt.configHash = (Get-FileHash $configPath -Algorithm SHA256).Hash
    $receipt.keyHash = (Get-FileHash $keyPath -Algorithm SHA256).Hash
    Save-Receipt 'configured'
    New-NetFirewallRule -Name $allowRule -DisplayName 'EdSys work laptop SSH from 9950x' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -Profile Any `
        -LocalAddress $manifest.laptopAddress -RemoteAddress $manifest.hubAddress | Out-Null
    New-NetFirewallRule -Name $denyRule -DisplayName 'EdSys work laptop SSH exclude other peers' `
        -Direction Inbound -Action Block -Protocol TCP -LocalPort 22 -Profile Any `
        -LocalAddress $manifest.laptopAddress -RemoteAddress (Get-OtherIPv4Ranges $manifest.hubAddress) | Out-Null
    $sc = Join-Path $env:SystemRoot 'System32\sc.exe'
    $dependencies = @((Get-Service sshd).ServicesDependedOn | ForEach-Object { $_.Name }) + @('Tailscale')
    Invoke-Native $sc @('config', 'sshd', 'depend=', (($dependencies | Select-Object -Unique) -join '/'), 'start=', 'delayed-auto') | Out-Null
    Invoke-Native $sc @('failure', 'sshd', 'reset=', '86400', 'actions=', 'restart/15000/restart/30000/restart/60000') | Out-Null
    Invoke-Native $sc @('failureflag', 'sshd', '1') | Out-Null
    Start-Service sshd
    (Get-Service sshd).WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
    Test-ManagedAccess $manifest
    Remove-NetFirewallRule -Name $containRule
    Save-Receipt 'local-verified'
    # Export only the public host key for pinning through the existing outbound SSH path.
    [IO.File]::WriteAllText((Join-Path $scriptDirectory 'host-key.pub'),
        [IO.File]::ReadAllText("$hostKey.pub"), (New-Object Text.UTF8Encoding($false)))
    Write-Host 'INSTALLED. Remote acceptance and reboot verification are still pending.'
    Write-Host "To revoke locally: powershell.exe -NoProfile -File `"$stateRoot\Manage-WorkLaptopAccess.ps1`" -Action Revoke"
} catch {
    $failure = $_
    if ($ownsInstallation) {
        try { Close-ManagedAccess; Save-Receipt 'failed-access-closed' }
        catch { Write-Warning 'Automatic access closure was incomplete. Keep the containment rule and ask the operator to inspect locally.' }
    } else {
        Get-NetFirewallRule -Name $containRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    }
    throw $failure
}
