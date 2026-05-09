<#
.SYNOPSIS
  Read-only EdSys live inventory collector.

.DESCRIPTION
  Creates a timestamped local audit folder outside Git and gathers non-secret
  network, port, HTTP title, and optional SSH metadata for known EdSys hosts.

  This script is intentionally conservative:
  - No service restarts.
  - No package installs.
  - No public IP scans.
  - No environment dumps.
  - No .env, private key, cookie, token, or log collection.

  Raw output should remain under C:\EdSys-Codex\_local-audits and must be
  reviewed/sanitized before any summary is committed.
#>

[CmdletBinding()]
param(
    [string]$AuditRoot = "C:\EdSys-Codex\_local-audits",
    [switch]$SkipSsh
)

$ErrorActionPreference = "Continue"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$auditId = "edsys-$timestamp"
$auditPath = Join-Path $AuditRoot $auditId

$folders = @(
    "host-checks",
    "network",
    "docker",
    "proxmox",
    "pihole",
    "switch",
    "sanitized-review"
)

foreach ($folder in $folders) {
    New-Item -ItemType Directory -Force -Path (Join-Path $auditPath $folder) | Out-Null
}

@"
# Raw EdSys Audit Output

Audit ID: $auditId
Created: $(Get-Date -Format o)

This folder contains raw local evidence. Do not commit this folder.
Review and sanitize before copying any summaries into Git.
"@ | Set-Content -Path (Join-Path $auditPath "RAW_README.md") -Encoding UTF8

Set-Content -Path (Join-Path $AuditRoot "LATEST_AUDIT_PATH.txt") -Value $auditPath -Encoding UTF8

$hosts = @(
    @{ Name = "edcore"; Ip = "192.168.50.1"; Ports = @(22, 53, 80, 443, 8443) },
    @{ Name = "pihole-primary"; Ip = "192.168.50.5"; Ports = @(22, 53, 80, 443) },
    @{ Name = "pihole-secondary"; Ip = "192.168.50.6"; Ports = @(22, 53, 80, 443) },
    @{ Name = "edsys-voice"; Ip = "192.168.50.7"; Ports = @(22) },
    @{ Name = "voice-node1"; Ip = "192.168.50.12"; Ports = @(22) },
    @{ Name = "9950x"; Ip = "192.168.50.50"; Ports = @(22, 3000, 3001, 3002, 3005, 32400, 5000, 5500, 5678, 6333, 7997, 8096, 8181, 9000, 9443, 10200, 10300, 10400, 11434, 19999) },
    @{ Name = "pve-node0"; Ip = "192.168.50.51"; Ports = @(22, 8006) },
    @{ Name = "pve-node1"; Ip = "192.168.50.52"; Ports = @(22, 8006) },
    @{ Name = "pve-node2"; Ip = "192.168.50.53"; Ports = @(22, 8006) },
    @{ Name = "master-bedroom-htpc"; Ip = "192.168.50.54"; Ports = @(22) },
    @{ Name = "edcorelan"; Ip = "192.168.50.55"; Ports = @(22) },
    @{ Name = "home-assistant"; Ip = "192.168.50.75"; Ports = @(8123) },
    @{ Name = "node1-services"; Ip = "192.168.50.76"; Ports = @(3001) },
    @{ Name = "family-services"; Ip = "192.168.50.78"; Ports = @(22, 2283, 8010, 8085) },
    @{ Name = "arr-server"; Ip = "192.168.50.201"; Ports = @(22, 5055, 6767, 7878, 8080, 8085, 8686, 8989, 9696) },
    @{ Name = "basecamp-tailscale"; Ip = "100.120.155.81"; Ports = @(22, 8080, 8088) }
)

$httpTargets = @(
    @{ Name = "UniFi Network"; Url = "https://192.168.50.1:8443" },
    @{ Name = "Homepage"; Url = "http://192.168.50.50:3005" },
    @{ Name = "Open WebUI"; Url = "http://192.168.50.50:3000" },
    @{ Name = "AnythingLLM"; Url = "http://192.168.50.50:3002" },
    @{ Name = "Plex"; Url = "http://192.168.50.50:32400/web" },
    @{ Name = "Frigate"; Url = "http://192.168.50.50:5000" },
    @{ Name = "n8n"; Url = "http://192.168.50.50:5678" },
    @{ Name = "Qdrant"; Url = "http://192.168.50.50:6333" },
    @{ Name = "Infinity"; Url = "http://192.168.50.50:7997" },
    @{ Name = "Jellyfin"; Url = "http://192.168.50.50:8096" },
    @{ Name = "Tautulli"; Url = "http://192.168.50.50:8181" },
    @{ Name = "Portainer"; Url = "http://192.168.50.50:9000" },
    @{ Name = "Audiobookshelf"; Url = "http://192.168.50.50:13378" },
    @{ Name = "Penpot"; Url = "http://192.168.50.50:9002" },
    @{ Name = "EdSys Command Portal"; Url = "http://192.168.50.50:3010" },
    @{ Name = "Netdata"; Url = "http://192.168.50.50:19999" },
    @{ Name = "Home Assistant"; Url = "http://192.168.50.75:8123" },
    @{ Name = "qBittorrent"; Url = "http://192.168.50.201:8080" },
    @{ Name = "Radarr"; Url = "http://192.168.50.201:7878" },
    @{ Name = "Sonarr"; Url = "http://192.168.50.201:8989" },
    @{ Name = "Lidarr"; Url = "http://192.168.50.201:8686" },
    @{ Name = "Bazarr"; Url = "http://192.168.50.201:6767" },
    @{ Name = "Prowlarr"; Url = "http://192.168.50.201:9696" },
    @{ Name = "Overseerr"; Url = "http://192.168.50.201:5055" },
    @{ Name = "SABnzbd"; Url = "http://192.168.50.201:8085" },
    @{ Name = "Family Services"; Url = "http://192.168.50.78:8085" },
    @{ Name = "Immich"; Url = "http://192.168.50.78:2283" },
    @{ Name = "Paperless"; Url = "http://192.168.50.78:8010" },
    @{ Name = "Foothills Task List"; Url = "http://100.120.155.81:8080" },
    @{ Name = "Foothills Portal"; Url = "http://100.120.155.81:8088" }
)

function Test-PortFast {
    param(
        [string]$ComputerName,
        [int]$Port,
        [int]$TimeoutMs = 1200
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($ComputerName, $Port, $null, $null)
        $success = $async.AsyncWaitHandle.WaitOne($TimeoutMs, $false)
        if (-not $success) { return $false }
        $client.EndConnect($async)
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

"Collecting Windows network baseline..."
Get-NetNeighbor -AddressFamily IPv4 | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "network\windows-get-netneighbor-ipv4.csv")
arp -a | Set-Content -Path (Join-Path $auditPath "network\windows-arp-a_REVIEW_BEFORE_COMMIT.txt") -Encoding UTF8
Get-NetRoute -AddressFamily IPv4 | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "network\windows-get-netroute-ipv4.csv")
Get-DnsClientServerAddress | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "network\windows-dns-client-server-address.csv")
ipconfig /all | Set-Content -Path (Join-Path $auditPath "network\windows-ipconfig-all_REVIEW_BEFORE_COMMIT.txt") -Encoding UTF8
route print | Set-Content -Path (Join-Path $auditPath "network\windows-route-print_REVIEW_BEFORE_COMMIT.txt") -Encoding UTF8

"Checking known hosts..."
$hostRows = foreach ($hostEntry in $hosts) {
    $ping = Test-Connection -ComputerName $hostEntry.Ip -Count 1 -Quiet -ErrorAction SilentlyContinue
    [pscustomobject]@{
        Name = $hostEntry.Name
        Ip = $hostEntry.Ip
        Ping = $ping
        CheckedAt = (Get-Date -Format o)
    }
}
$hostRows | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "host-checks\known-host-ping.csv")

"Checking known service ports..."
$portRows = foreach ($hostEntry in $hosts) {
    foreach ($port in $hostEntry.Ports) {
        [pscustomobject]@{
            Hostname = $hostEntry.Name
            Ip = $hostEntry.Ip
            Port = $port
            Reachable = (Test-PortFast -ComputerName $hostEntry.Ip -Port $port)
            CheckedAt = (Get-Date -Format o)
        }
    }
}
$portRows | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "host-checks\known-service-port-checks.csv")

"Checking HTTP titles..."
$httpRows = foreach ($target in $httpTargets) {
    try {
        $response = Invoke-WebRequest -Uri $target.Url -UseBasicParsing -TimeoutSec 6 -SkipCertificateCheck -ErrorAction Stop
        $title = ""
        if ($response.Content -match "<title[^>]*>(.*?)</title>") {
            $title = ($Matches[1] -replace "\s+", " ").Trim()
        }
        [pscustomobject]@{
            Name = $target.Name
            Url = $target.Url
            Reachable = $true
            StatusCode = [int]$response.StatusCode
            Title = $title
            CheckedAt = (Get-Date -Format o)
        }
    }
    catch {
        [pscustomobject]@{
            Name = $target.Name
            Url = $target.Url
            Reachable = $false
            StatusCode = $null
            Title = $_.Exception.Message
            CheckedAt = (Get-Date -Format o)
        }
    }
}
$httpRows | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "host-checks\http-status-title-probes.csv")

if (-not $SkipSsh) {
    "Checking non-interactive SSH access..."
    $sshTargets = @(
        @{ Name = "edcore"; Target = "edcore" },
        @{ Name = "9950x"; Target = "9950x" },
        @{ Name = "pve-node0"; Target = "pve-node0" },
        @{ Name = "pve-node1"; Target = "pve-node1" },
        @{ Name = "pve-node2"; Target = "pve-node2" },
        @{ Name = "arr-server"; Target = "arr-server" },
        @{ Name = "pihole-primary"; Target = "jeremy@192.168.50.5" },
        @{ Name = "pihole-secondary"; Target = "jeremy@192.168.50.6" },
        @{ Name = "family-services"; Target = "jeremy@192.168.50.78" },
        @{ Name = "basecamp"; Target = "basecamp" }
    )

    $sshRows = foreach ($sshTarget in $sshTargets) {
        $outFile = Join-Path $auditPath ("host-checks\ssh-test-{0}.txt" -f $sshTarget.Name)
        & ssh -o BatchMode=yes -o ConnectTimeout=5 $sshTarget.Target "hostname" *> $outFile
        [pscustomobject]@{
            Name = $sshTarget.Name
            Target = $sshTarget.Target
            SshAccess = ($LASTEXITCODE -eq 0)
            ExitCode = $LASTEXITCODE
            OutputFile = $outFile
            CheckedAt = (Get-Date -Format o)
        }
    }
    $sshRows | Export-Csv -NoTypeInformation -Path (Join-Path $auditPath "host-checks\ssh-access.csv")

    $linuxCommand = @'
hostnamectl
uname -a
ip -br addr
ip -br link
ip route
ip neigh
df -h
lsblk
free -h
uptime
systemctl --failed
ss -tulpn
findmnt
mount | grep -E "nfs|cifs|mergerfs|fuse|/mnt|media|ai-store" || true
docker ps --format '{{json .}}' 2>/dev/null || true
docker compose ls 2>/dev/null || true
'@

    foreach ($targetName in @("edcore", "9950x")) {
        $row = $sshRows | Where-Object { $_.Name -eq $targetName -and $_.SshAccess }
        if ($row) {
            & ssh -o BatchMode=yes -o ConnectTimeout=5 $row.Target $linuxCommand *> (Join-Path $auditPath ("host-checks\{0}-linux-baseline.txt" -f $targetName))
        }
    }
}

@"
# Sanitized Summary Starter

Audit ID: $auditId
Raw folder: $auditPath

Review raw files, redact sensitive values, and generate sanitized summaries before committing.

Responding hosts:
$($hostRows | Where-Object Ping | ForEach-Object { "- $($_.Name) $($_.Ip)" } | Out-String)

Reachable ports:
$($portRows | Where-Object Reachable | ForEach-Object { "- $($_.Hostname) $($_.Ip):$($_.Port)" } | Out-String)
"@ | Set-Content -Path (Join-Path $auditPath "SANITIZED_SUMMARY.md") -Encoding UTF8

Write-Host "Audit folder: $auditPath"
