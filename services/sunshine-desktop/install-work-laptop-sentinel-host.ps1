#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$HostAddress,
    [string]$BundleDirectory,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:COMPUTERNAME -ine 'THOMPSON-LC086' -or -not [Environment]::Is64BitProcess) {
    throw 'Run in elevated 64-bit PowerShell on the approved work laptop.'
}
$ip = $null
if (-not [Net.IPAddress]::TryParse($HostAddress, [ref]$ip)) { throw 'Invalid host address.' }
$bytes = $ip.GetAddressBytes()
if ($bytes.Length -ne 4 -or $bytes[0] -ne 100 -or $bytes[1] -lt 64 -or $bytes[1] -gt 127 -or
    $ip.ToString() -cne $HostAddress) { throw 'Expected one canonical Tailscale IPv4 address.' }
$address = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $HostAddress
if ($address.InterfaceAlias -notmatch 'Tailscale') { throw 'Host address is not on Tailscale.' }
$providers = @(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName FirewallProduct)
if ($providers.Count -ne 1 -or $providers[0].displayName -cne 'Sentinel Firewall') {
    throw 'Expected the existing Sentinel Firewall provider.'
}
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Net;
using System.Runtime.InteropServices;
public static class EdSysSunshineSetup {
    [DllImport("wscapi.dll", ExactSpelling=true)]
    public static extern int WscGetSecurityProviderHealth(uint providers, out int health);
    public static string Request(string uri, string body, string auth) {
        var request = (HttpWebRequest)WebRequest.Create(uri);
        request.Proxy = null;
        request.Timeout = 5000;
        // Requests are exclusively to this computer: loopback during bootstrap,
        // then its verified Tailscale address. Sunshine creates a self-signed cert.
        request.ServerCertificateValidationCallback = delegate { return true; };
        if (!String.IsNullOrEmpty(auth)) request.Headers[HttpRequestHeader.Authorization] = "Basic " + auth;
        // Windows PowerShell converts $null to an empty System.String here.
        if (!String.IsNullOrEmpty(body)) {
            request.Method = "POST";
            request.ContentType = "application/json";
            using (var writer = new StreamWriter(request.GetRequestStream())) writer.Write(body);
        }
        using (var response = request.GetResponse())
        using (var reader = new StreamReader(response.GetResponseStream())) return reader.ReadToEnd();
    }
}
'@
[int]$health = -1
if ([EdSysSunshineSetup]::WscGetSecurityProviderHealth(1, [ref]$health) -ne 0 -or $health -ne 0) {
    throw 'Windows Security Center does not report healthy firewall protection.'
}
$root = Join-Path $env:ProgramFiles 'Sunshine'
$exe = Join-Path $root 'sunshine.exe'
if ((Test-Path $root) -or (Get-Service SunshineService,SunshineSvc -ErrorAction SilentlyContinue)) {
    throw 'Existing Sunshine found; inspect instead of repeating installation.'
}
if (-not $BundleDirectory) { $BundleDirectory = $PSScriptRoot }
$msi = Join-Path $BundleDirectory 'Sunshine-Windows-AMD64-installer.msi'
if ((Get-FileHash $msi -Algorithm SHA256).Hash -ine '1d7fed8beecd5889dc7ff14cf9f42d6d38f37c3066c13c6c2a5f4e91847e0ccf') {
    throw 'Official MSI SHA-256 mismatch.'
}
$signature = Get-AuthenticodeSignature $msi
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '^CN=David Lane, O=David Lane,') {
    throw 'Expected the verified official release publisher signature.'
}
# The reviewed migration action preserves config/sunshine.conf. Refuse a
# package containing that filename: it could replace our loopback bootstrap.
$installer = New-Object -ComObject WindowsInstaller.Installer
$database = $installer.OpenDatabase($msi, 0)
$view = $database.OpenView('SELECT `FileName` FROM `File`')
$view.Execute()
while ($record = $view.Fetch()) {
    if ($record.StringData(1) -match '(^|\|)sunshine\.conf$') { throw 'MSI contains a configuration file; review bootstrap containment.' }
}
$view.Close()
Write-Host 'Preflight passed: verified release, healthy Sentinel, fresh host, exact Tailscale interface.'
if ($Plan) { return }
$run = Join-Path $env:ProgramData ('EdSys\Sunshine-Setup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $run | Out-Null
icacls.exe $run /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect setup reports.' }
$report = [ordered]@{status='incomplete'; version='2026.914.233613'; firewallMode='SentinelManaged';
    rebootRequired=$false; credentialsCreated=$false; tailnetOnly=$false; pairing='pending'; liveStream='pending'}
$conf = Join-Path $root 'config\sunshine.conf'
function Write-Configuration([string]$BindAddress, [string]$Origin) {
    @"
sunshine_name = Work Laptop - Site Office
address_family = ipv4
bind_address = $BindAddress
upnp = disabled
origin_web_ui_allowed = $Origin
csrf_allowed_origins = https://localhost,https://127.0.0.1,https://$HostAddress
lan_encryption_mode = 2
wan_encryption_mode = 2
controller = disabled
dd_configuration_option = disabled
install_steam_audio_drivers = disabled
"@ | Set-Content -LiteralPath $conf -Encoding ASCII
}
function Wait-Listeners([string]$BindAddress) {
    for ($i=0; $i -lt 45; $i++) {
        $processes = @(Get-Process sunshine -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
        $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
            Where-Object { $_.OwningProcess -in $processes })
        if (@($listeners | Where-Object LocalAddress -ne $BindAddress).Count) { throw 'Unexpected Sunshine listener address.' }
        if (@(47984,47989,47990,48010 | Where-Object { $_ -notin $listeners.LocalPort }).Count -eq 0) { return }
        Start-Sleep -Seconds 1
    }
    throw 'Sunshine did not become ready; inspect the private logs.'
}
try {
    New-Item -ItemType Directory (Split-Path $conf) -Force | Out-Null
    Write-Configuration '127.0.0.1' 'pc'
    $bootstrapHash = (Get-FileHash $conf).Hash
    $install = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart /L*v `"$run\install.log`"" -PassThru
    if (-not $install.WaitForExit(300000)) { throw 'MSI is still running with loopback configuration. Inspect before retrying.' }
    $install.Refresh()
    if ($install.ExitCode -notin @(0,3010)) { throw "MSI exit $($install.ExitCode); see protected log." }
    $report.rebootRequired = ($install.ExitCode -eq 3010)
    if ((Get-FileHash $conf).Hash -cne $bootstrapHash) { throw 'MSI altered bootstrap configuration.' }
    icacls.exe (Split-Path $conf) /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not protect Sunshine configuration.' }
    Wait-Listeners '127.0.0.1'
    # Disable only the installed application's broad Windows allow rules.
    # Sentinel and all profile settings remain unchanged.
    Get-NetFirewallApplicationFilter -Program $exe | Get-NetFirewallRule |
        Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } | Disable-NetFirewallRule
    $random = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($random) } finally { $rng.Dispose() }
    $credential = @{username='jeremy';password=[Convert]::ToBase64String($random)}
    $credential | ConvertTo-Json | Set-Content (Join-Path $run 'web-admin.json') -Encoding UTF8
    $body = @{newUsername=$credential.username;newPassword=$credential.password;confirmNewPassword=$credential.password} | ConvertTo-Json -Compress
    $response = [EdSysSunshineSetup]::Request('https://127.0.0.1:47990/api/password', $body, $null) | ConvertFrom-Json
    if ($response.status -ne $true) { throw 'Local admin account creation did not succeed.' }
    $auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($credential.username+':'+$credential.password))
    $null = [EdSysSunshineSetup]::Request('https://127.0.0.1:47990/api/config', $null, $auth) | ConvertFrom-Json
    $report.credentialsCreated = $true
    Stop-Service SunshineService
    (Get-Service SunshineService).WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    Write-Configuration $HostAddress 'wan'
    # "wan" is Sunshine's source classification for Tailnet addresses, not a
    # public listener. The exact bind above constrains every streaming socket.
    Set-Service SunshineService -StartupType Automatic
    sc.exe config SunshineService depend= Tailscale | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure Tailscale startup dependency.' }
    sc.exe failure SunshineService reset= 86400 actions= restart/15000/restart/30000/restart/60000 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not configure service recovery.' }
    Start-Service SunshineService
    Wait-Listeners $HostAddress
    $null = [EdSysSunshineSetup]::Request("https://${HostAddress}:47990/api/config", $null, $auth) | ConvertFrom-Json
    $apps = Get-Content (Join-Path $root 'config\apps.json') -Raw | ConvertFrom-Json
    if ('Desktop' -notin @($apps.apps.name)) { throw 'Desktop application missing.' }
    $report.tailnetOnly = $true
    $report.status = 'installed-pairing-pending'
    Write-Host 'Sunshine installed with authenticated Tailscale management and encrypted streaming. Pairing remains pending.'
} catch {
    Stop-Service SunshineService -ErrorAction SilentlyContinue
    Set-Service SunshineService -StartupType Disabled -ErrorAction SilentlyContinue
    throw
} finally {
    $report | ConvertTo-Json | Set-Content (Join-Path $run 'result.json') -Encoding UTF8
    Write-Host "Private setup report: $run\result.json"
}
