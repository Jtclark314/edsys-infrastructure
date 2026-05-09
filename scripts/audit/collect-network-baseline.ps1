[CmdletBinding()]
param(
    [string]$OutputRoot = "C:\EdSys-Codex\_local-audits",
    [string]$AuditId = ("edsys-" + (Get-Date -Format "yyyyMMdd-HHmmss")),
    [int]$TimeoutMs = 750
)

$ErrorActionPreference = "Continue"

function New-AuditDirectory {
    param([string]$Root, [string]$Id)
    $path = Join-Path $Root $Id
    New-Item -ItemType Directory -Force -Path $path | Out-Null
    return $path
}

function Test-TcpPort {
    param(
        [string]$Address,
        [int]$Port,
        [int]$TimeoutMilliseconds
    )

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($Address, $Port, $null, $null)
        $connected = $async.AsyncWaitHandle.WaitOne($TimeoutMilliseconds, $false)
        if ($connected) {
            $client.EndConnect($async)
            return $true
        }
        return $false
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

$auditPath = New-AuditDirectory -Root $OutputRoot -Id $AuditId

$hosts = @(
    [pscustomobject]@{ Name = "edcore"; IP = "192.168.50.1"; Role = "router-dhcp-dns" },
    [pscustomobject]@{ Name = "pihole-primary"; IP = "192.168.50.5"; Role = "dns-filtering" },
    [pscustomobject]@{ Name = "pihole-secondary"; IP = "192.168.50.6"; Role = "dns-filtering-planned" },
    [pscustomobject]@{ Name = "9950x"; IP = "192.168.50.50"; Role = "primary-service-host" },
    [pscustomobject]@{ Name = "pve-node0"; IP = "192.168.50.51"; Role = "proxmox" },
    [pscustomobject]@{ Name = "pve-node1"; IP = "192.168.50.52"; Role = "proxmox" },
    [pscustomobject]@{ Name = "pve-node2"; IP = "192.168.50.53"; Role = "proxmox" },
    [pscustomobject]@{ Name = "master-bedroom-htpc"; IP = "192.168.50.54"; Role = "endpoint" },
    [pscustomobject]@{ Name = "home-assistant"; IP = "192.168.50.75"; Role = "home-automation" },
    [pscustomobject]@{ Name = "family-services"; IP = "192.168.50.78"; Role = "family-services" },
    [pscustomobject]@{ Name = "arr-server"; IP = "192.168.50.201"; Role = "media-automation" }
)

$commonPortChecks = foreach ($hostItem in $hosts) {
    foreach ($portItem in @(22, 80, 443)) {
        [pscustomobject]@{ Host = $hostItem.Name; IP = $hostItem.IP; Port = $portItem; Service = "common-$portItem" }
    }
}

$specificPortChecks = @(
    [pscustomobject]@{ Host = "edcore"; IP = "192.168.50.1"; Port = 53; Service = "DNS" },
    [pscustomobject]@{ Host = "pihole-primary"; IP = "192.168.50.5"; Port = 53; Service = "DNS" },
    [pscustomobject]@{ Host = "pihole-secondary"; IP = "192.168.50.6"; Port = 53; Service = "DNS" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 3000; Service = "Open WebUI" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 3001; Service = "Uptime Kuma" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 3002; Service = "AnythingLLM" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 3005; Service = "Homepage" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 32400; Service = "Plex" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 5000; Service = "Frigate" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 5500; Service = "Frigate alt" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 5678; Service = "n8n" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 6333; Service = "Qdrant" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 7997; Service = "Infinity embeddings" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 8096; Service = "Jellyfin" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 8181; Service = "Tautulli" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 9000; Service = "Portainer" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 9443; Service = "Portainer HTTPS" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 10200; Service = "Wyoming Piper" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 10300; Service = "Wyoming Whisper" },
    [pscustomobject]@{ Host = "9950x"; IP = "192.168.50.50"; Port = 11434; Service = "Ollama" },
    [pscustomobject]@{ Host = "home-assistant"; IP = "192.168.50.75"; Port = 8123; Service = "Home Assistant" },
    [pscustomobject]@{ Host = "family-services"; IP = "192.168.50.78"; Port = 8085; Service = "Nextcloud/family-services likely" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 8080; Service = "qBittorrent" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 8085; Service = "SABnzbd" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 7878; Service = "Radarr" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 8989; Service = "Sonarr" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 8686; Service = "Lidarr" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 6767; Service = "Bazarr" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 9696; Service = "Prowlarr" },
    [pscustomobject]@{ Host = "arr-server"; IP = "192.168.50.201"; Port = 5055; Service = "Overseerr" }
)

$metadata = [pscustomobject]@{
    audit_id = $AuditId
    started_at = (Get-Date).ToString("s")
    output_path = $auditPath
    safety = "read-only ping and TCP checks; raw local evidence outside Git"
}
$metadata | ConvertTo-Json | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "metadata.json")

$hostResults = foreach ($hostItem in $hosts) {
    $ping = Test-Connection -ComputerName $hostItem.IP -Count 1 -Quiet -ErrorAction SilentlyContinue
    [pscustomobject]@{
        name = $hostItem.Name
        ip = $hostItem.IP
        role = $hostItem.Role
        ping = [bool]$ping
        checked_at = (Get-Date).ToString("s")
    }
}
$hostResults | Export-Csv -NoTypeInformation -Encoding UTF8 -Path (Join-Path $auditPath "hosts.csv")

$allPortChecks = @($commonPortChecks + $specificPortChecks) | Sort-Object IP, Port -Unique
$portResults = foreach ($check in $allPortChecks) {
    $open = Test-TcpPort -Address $check.IP -Port $check.Port -TimeoutMilliseconds $TimeoutMs
    [pscustomobject]@{
        host = $check.Host
        ip = $check.IP
        port = $check.Port
        service = $check.Service
        reachable = [bool]$open
        checked_at = (Get-Date).ToString("s")
    }
}
$portResults | Export-Csv -NoTypeInformation -Encoding UTF8 -Path (Join-Path $auditPath "ports.csv")

arp -a | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "arp-a_REVIEW_BEFORE_COMMIT.txt")
route print | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "route-print_REVIEW_BEFORE_COMMIT.txt")
ipconfig /all | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "ipconfig-all_REVIEW_BEFORE_COMMIT.txt")

$responding = @($hostResults | Where-Object { $_.ping }).Count
$reachablePorts = @($portResults | Where-Object { $_.reachable }).Count

$summary = @()
$summary += "# EdSys Live Audit Sanitized Summary"
$summary += ""
$summary += ("- Audit ID: ``{0}``" -f $AuditId)
$summary += "- Generated: $((Get-Date).ToString('s'))"
$summary += ("- Output path: ``{0}``" -f $auditPath)
$summary += "- Scope: known EdSys LAN hosts only"
$summary += "- Safety: read-only ping and TCP checks; raw evidence kept outside Git"
$summary += ""
$summary += "## Host Reachability"
$summary += ""
$summary += "| Host | IP | Ping |"
$summary += "| --- | --- | --- |"
foreach ($row in $hostResults) {
    $summary += "| $($row.name) | $($row.ip) | $($row.ping) |"
}
$summary += ""
$summary += "## Reachable Ports"
$summary += ""
$summary += "| Host | IP | Port | Service |"
$summary += "| --- | --- | --- | --- |"
foreach ($row in ($portResults | Where-Object { $_.reachable } | Sort-Object host, port)) {
    $summary += "| $($row.host) | $($row.ip) | $($row.port) | $($row.service) |"
}
$summary += ""
$summary += "## Counts"
$summary += ""
$summary += "- Hosts responding to ping: $responding"
$summary += "- Reachable TCP checks: $reachablePorts"
$summary += ""
$summary += "## Review Notes"
$summary += ""
$summary += '- Review raw files ending in `REVIEW_BEFORE_COMMIT` before copying anything into Git.'
$summary += "- Do not treat closed ports as proof a service is absent; firewalls and bind addresses can hide services."
$summary | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "SANITIZED_SUMMARY.md")

$networkUpdates = @()
$networkUpdates += "# Proposed network updates from read-only audit."
$networkUpdates += "# Review manually before applying to EdSys-Master/data/network-map.yml."
$networkUpdates += "observed_hosts:"
foreach ($row in $hostResults) {
    $networkUpdates += "  - hostname: $($row.name)"
    $networkUpdates += "    ip: $($row.ip)"
    $networkUpdates += "    ping: $($row.ping.ToString().ToLowerInvariant())"
    $networkUpdates += "    source: $AuditId"
    $networkUpdates += "    confidence: pending-review"
}
$networkUpdates | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "PROPOSED_NETWORK_UPDATES.yml")

$serviceUpdates = @()
$serviceUpdates += "# Proposed service updates from read-only audit."
$serviceUpdates += "# Review manually before applying to EdSys-Master/data/service-catalog.yml."
$serviceUpdates += "observed_ports:"
foreach ($row in ($portResults | Where-Object { $_.reachable } | Sort-Object host, port)) {
    $serviceUpdates += "  - host: $($row.host)"
    $serviceUpdates += "    ip: $($row.ip)"
    $serviceUpdates += "    port: $($row.port)"
    $serviceUpdates += "    service_hint: $($row.service)"
    $serviceUpdates += "    source: $AuditId"
    $serviceUpdates += "    confidence: pending-review"
}
$serviceUpdates | Set-Content -Encoding UTF8 -Path (Join-Path $auditPath "PROPOSED_SERVICE_UPDATES.yml")

Write-Output "Audit complete: $auditPath"
Write-Output "Hosts responding to ping: $responding"
Write-Output "Reachable TCP checks: $reachablePorts"
