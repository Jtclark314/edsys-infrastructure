#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$HostAddress,
    [Parameter(Mandatory=$true)][string]$ClientAddress,
    [string]$BundleDirectory,
    [switch]$Plan
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
if ($env:COMPUTERNAME -ine 'THOMPSON-LC086') { throw 'Run only on THOMPSON-LC086.' }
if (-not [Environment]::Is64BitProcess) { throw 'Use 64-bit Windows PowerShell.' }
function Test-TailnetAddress([string]$Address) {
    $bytes = [Net.IPAddress]::Parse($Address).GetAddressBytes()
    if ($bytes.Length -ne 4 -or $bytes[0] -ne 100 -or $bytes[1] -lt 64 -or $bytes[1] -gt 127) {
        throw 'Expected exact Tailscale IPv4 addresses.'
    }
}
Test-TailnetAddress $HostAddress
Test-TailnetAddress $ClientAddress
if ($HostAddress -eq $ClientAddress) { throw 'Host and client must differ.' }
$address = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $HostAddress
if ($address.InterfaceAlias -notmatch 'Tailscale') { throw 'Host address is not on Tailscale.' }
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
if ($profiles.Count -ne 3 -or @($profiles | Where-Object { -not $_.Enabled }).Count) {
    throw 'Windows Firewall must be enabled on all profiles.'
}
$root = Join-Path $env:ProgramFiles 'Sunshine'
$exe = Join-Path $root 'sunshine.exe'
$group = 'EdSys Work Laptop Sunshine'
if ((Test-Path $root) -or (Get-Service SunshineService -ErrorAction SilentlyContinue) -or
    (Get-NetFirewallRule -Group $group -ErrorAction SilentlyContinue)) {
    throw 'Existing Sunshine or prior setup found. Preserve it and inspect before retrying.'
}
if (-not $BundleDirectory) { $BundleDirectory = $PSScriptRoot }
if (-not $BundleDirectory) { throw 'Specify BundleDirectory when invoking as a scriptblock.' }
$msi = Join-Path $BundleDirectory 'Sunshine-Windows-AMD64-installer.msi'
if ((Get-FileHash $msi -Algorithm SHA256).Hash -ine '1d7fed8beecd5889dc7ff14cf9f42d6d38f37c3066c13c6c2a5f4e91847e0ccf') {
    throw 'Official MSI SHA-256 mismatch.'
}
$signature = Get-AuthenticodeSignature $msi
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '^CN=David Lane, O=David Lane,') {
    throw 'Expected the verified official release publisher signature.'
}
Write-Host 'Install Sunshine 2026.914.233613; allow only Nimo over Tailscale; local web management.'
if ($Plan) { return }
$run = Join-Path $env:ProgramData ('EdSys\Sunshine-Setup-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $run | Out-Null
& icacls.exe $run /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not protect setup reports.' }
$report = [ordered]@{ action='incomplete'; service='to be confirmed'; localPorts=$false;
    pairing='to be confirmed'; liveStream='to be confirmed'; rebootRequired=$false }
$guard = 'EdSys-Sunshine-Setup-Block'
try {
    # Block before MSI custom actions can start the service or add broad allow rules.
    New-NetFirewallRule -Name $guard -DisplayName $guard -Group $group -Direction Inbound -Action Block -Profile Any -Program $exe | Out-Null
    if (-not (Get-NetFirewallRule -PolicyStore ActiveStore -Name $guard)) { throw 'Setup guard is not effective.' }
    $install = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /qn /norestart /L*v `"$run\install.log`"" -PassThru
    if (-not $install.WaitForExit(300000)) { throw 'MSI still running; setup guard retained. Do not rerun until inspected.' }
    $install.Refresh()
    if ($install.ExitCode -notin @(0,3010)) { throw "MSI exit $($install.ExitCode); see protected installation log." }
    $report.rebootRequired = ($install.ExitCode -eq 3010)
    Stop-Service SunshineService -ErrorAction Stop
    # This release signs its MSI, not the enclosed executable. Trust the pinned,
    # signature-verified package; do not require a nonexistent EXE signature.
    if (-not (Test-Path -LiteralPath $exe)) { throw 'Installed executable missing.' }
    $conf = Join-Path $root 'config\sunshine.conf'
    New-Item -ItemType Directory -Path (Split-Path $conf) -Force | Out-Null
    if (Test-Path $conf) { Copy-Item $conf (Join-Path $run 'sunshine.conf.before') }
    @'
sunshine_name = Work Laptop - Site Office
address_family = ipv4
upnp = disabled
origin_web_ui_allowed = pc
lan_encryption_mode = 2
wan_encryption_mode = 2
controller = disabled
dd_configuration_option = disabled
install_steam_audio_drivers = disabled
'@ | Set-Content -LiteralPath $conf -Encoding ASCII
    # The new install may add unrestricted application rules. Disable only its rules.
    Get-NetFirewallApplicationFilter -Program $exe | Get-NetFirewallRule |
        Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Allow' } |
        Disable-NetFirewallRule
    # Explicit deny of every other peer also protects against unrelated broad port rules.
    $b = [Net.IPAddress]::Parse($ClientAddress).GetAddressBytes()
    $n = ([uint64]$b[0] * 16777216) + ([uint64]$b[1] * 65536) + ([uint64]$b[2] * 256) + $b[3]
    function Convert-IPv4([uint64]$Value) {
        return ('{0}.{1}.{2}.{3}' -f (($Value -shr 24) -band 255), (($Value -shr 16) -band 255), (($Value -shr 8) -band 255), ($Value -band 255))
    }
    $denied = @(('0.0.0.0-' + (Convert-IPv4 ($n-1))), ((Convert-IPv4 ($n+1)) + '-255.255.255.255'), '::/0')
    New-NetFirewallRule -Name 'EdSys-Sunshine-Deny-Other-Peers' -DisplayName 'Sunshine deny other peers' -Group $group -Direction Inbound -Action Block -Profile Any -Program $exe -RemoteAddress $denied | Out-Null
    New-NetFirewallRule -Name 'EdSys-Sunshine-Deny-Remote-Admin' -DisplayName 'Sunshine deny remote administration' -Group $group -Direction Inbound -Action Block -Profile Any -Program $exe -Protocol TCP -LocalPort 47990 | Out-Null
    New-NetFirewallRule -Name 'EdSys-Sunshine-Nimo-TCP' -DisplayName 'Sunshine Nimo TCP' -Group $group -Direction Inbound -Action Allow -Profile Any -Program $exe -LocalAddress $HostAddress -RemoteAddress $ClientAddress -InterfaceAlias $address.InterfaceAlias -Protocol TCP -LocalPort 47984,47989,48010 | Out-Null
    New-NetFirewallRule -Name 'EdSys-Sunshine-Nimo-UDP' -DisplayName 'Sunshine Nimo UDP' -Group $group -Direction Inbound -Action Allow -Profile Any -Program $exe -LocalAddress $HostAddress -RemoteAddress $ClientAddress -InterfaceAlias $address.InterfaceAlias -Protocol UDP -LocalPort 47998,47999,48000,48002,48010 | Out-Null
    $effective = @(Get-NetFirewallRule -PolicyStore ActiveStore -Group $group | Where-Object { $_.Enabled -eq 'True' })
    if ($effective.Count -ne 5) { throw 'Expected firewall rules are not effective; corporate policy may prohibit local rules.' }
    Set-Service SunshineService -StartupType Automatic
    Start-Service SunshineService
    $ready = $false
    for ($i=0; $i -lt 30; $i++) {
        $ports = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort)
        if (@(47984,47989,47990,48010 | Where-Object { $_ -notin $ports }).Count -eq 0) { $ready=$true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready -or (Get-Service SunshineService).Status -ne 'Running') { throw 'Sunshine startup failed; setup guard retained.' }
    $apps = Get-Content (Join-Path $root 'config\apps.json') -Raw | ConvertFrom-Json
    if ('Desktop' -notin @($apps.apps.name)) { throw 'Desktop application missing.' }
    Remove-NetFirewallRule -Name $guard
    $report.action = 'installed-pairing-pending'
    $report.service = 'Running, Automatic'
    $report.localPorts = $true
    Write-Host 'Sunshine installed. Open https://localhost:47990 on this laptop and create its local admin account.'
    Write-Host 'Keep this laptop plugged in, awake, and its lid open. Power policy was not changed.'
    Write-Host 'Nimo pairing and a live desktop stream still need verification.'
} catch {
    Stop-Service SunshineService -ErrorAction SilentlyContinue
    Set-Service SunshineService -StartupType Disabled -ErrorAction SilentlyContinue
    throw
} finally {
    $report | ConvertTo-Json | Set-Content (Join-Path $run 'result.json') -Encoding UTF8
    Write-Host "Local setup report: $run\result.json"
}
