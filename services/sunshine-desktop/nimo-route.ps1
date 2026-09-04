[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = 'C:\ProgramData\EdSys\Sunshine'
$config = Get-Content -Raw (Join-Path $root 'client.json') | ConvertFrom-Json
$target = [System.Net.IPAddress]::Parse($config.HubLanAddress)
if ($target.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
    -not $target.ToString().StartsWith('192.168.50.')) { throw 'Invalid hub LAN address' }
$prefix = "$target/32"
$statePath = Join-Path $root 'route-state.json'
$old = if (Test-Path $statePath) { Get-Content -Raw $statePath | ConvertFrom-Json } else { $null }

function Remove-OwnedRoute($state) {
    if ($null -eq $state -or $state.Prefix -ne $prefix) { return }
    Get-NetRoute -PolicyStore ActiveStore -DestinationPrefix $prefix -InterfaceIndex $state.InterfaceIndex -ErrorAction SilentlyContinue |
        Where-Object { $_.NextHop -eq '0.0.0.0' -and $_.RouteMetric -eq 1 } |
        Remove-NetRoute -Confirm:$false -ErrorAction Stop
}

$candidates = @(Get-NetConnectionProfile | Where-Object { $_.Name -eq 'EdSys' } | ForEach-Object {
    $idx = $_.InterfaceIndex
    $adapter = Get-NetAdapter -InterfaceIndex $idx -ErrorAction SilentlyContinue
    if ($null -eq $adapter -or $adapter.Status -ne 'Up') { return }
    Get-NetIPAddress -InterfaceIndex $idx -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.AddressState -eq 'Preferred' -and $_.IPAddress -in $config.AllowedLanAddresses } |
        ForEach-Object {
            $metric = (Get-NetIPInterface -InterfaceIndex $idx -AddressFamily IPv4).InterfaceMetric
            [pscustomobject]@{ InterfaceIndex=$idx; InterfaceAlias=$adapter.Name; Address=$_.IPAddress; Metric=$metric }
        }
})
$selected = $candidates | Sort-Object Metric,InterfaceIndex | Select-Object -First 1
if ($null -eq $selected) {
    Remove-OwnedRoute $old
    Remove-Item -LiteralPath $statePath -ErrorAction SilentlyContinue
    Write-Output 'No qualified EdSys LAN interface; Tailscale remains available'
    exit 0
}
if ($null -ne $old -and $old.InterfaceIndex -ne $selected.InterfaceIndex) { Remove-OwnedRoute $old }
$existing = @(Get-NetRoute -PolicyStore ActiveStore -DestinationPrefix $prefix -ErrorAction SilentlyContinue)
$correct = @($existing | Where-Object { $_.InterfaceIndex -eq $selected.InterfaceIndex -and $_.NextHop -eq '0.0.0.0' -and $_.RouteMetric -eq 1 })
if ($existing.Count -gt 0 -and $correct.Count -ne $existing.Count) { throw 'An unmanaged hub /32 route conflicts; do not overwrite it' }
if ($correct.Count -eq 0) {
    New-NetRoute -PolicyStore ActiveStore -DestinationPrefix $prefix -InterfaceIndex $selected.InterfaceIndex -NextHop '0.0.0.0' -RouteMetric 1 | Out-Null
}
[pscustomobject]@{ Prefix=$prefix; InterfaceIndex=$selected.InterfaceIndex } | ConvertTo-Json | Set-Content -Encoding UTF8 $statePath
$route = @(Find-NetRoute -RemoteIPAddress $target.ToString())
if (-not ($route | Where-Object { $_.InterfaceIndex -eq $selected.InterfaceIndex -and $_.IPAddress -eq $selected.Address })) {
    throw 'Direct LAN source/interface acceptance failed'
}
Write-Output "Direct LAN route accepted through $($selected.InterfaceAlias)"
