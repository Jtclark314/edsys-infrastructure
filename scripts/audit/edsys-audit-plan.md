# EdSys Live Audit Plan

Status: starting baseline.

Purpose: gather current machine and service facts for EdSys without changing live systems and without committing raw output.

## Output Location

Raw audit output should be written outside tracked repos:

```text
C:\EdSys-Codex\_local-audits\edsys-YYYYMMDD-HHMMSS\
```

Do not commit raw output unless it has been reviewed and sanitized.

## Known Hosts

| IP | Name | Notes |
| --- | --- | --- |
| `192.168.50.1` | `edcore` | Router/DHCP/DNS authority |
| `192.168.50.5` | `pihole-primary` | Primary Pi-hole |
| `192.168.50.6` | `pihole-secondary` | Planned or to be confirmed |
| `192.168.50.50` | `9950x` | Primary workstation/Docker/AI/Plex host |
| `192.168.50.51` | `pve-node0` | Proxmox node |
| `192.168.50.52` | `pve-node1` | Proxmox node |
| `192.168.50.53` | `pve-node2` | Proxmox node |
| `192.168.50.54` | `master-bedroom-htpc` | HTPC endpoint |
| `192.168.50.75` | `home-assistant` | Home Assistant VM |
| `192.168.50.78` | `family-services` | Likely Nextcloud/family services |
| `192.168.50.201` | `arr-server` | Media automation host |

## Known Ports To Check

- `22` SSH
- `53` DNS on DNS hosts
- `80` HTTP
- `443` HTTPS
- `3000` Open WebUI/Grafana depending host
- `3001` Uptime Kuma
- `3002` AnythingLLM
- `3005` Homepage
- `32400` Plex
- `5000` / `5500` Frigate
- `5678` n8n
- `6333` Qdrant
- `7997` Infinity embeddings
- `8096` Jellyfin
- `8123` Home Assistant
- `8181` Tautulli if present
- `9000` Portainer if present
- `9443` Portainer HTTPS if present
- `10200` Wyoming Piper
- `10300` Wyoming Whisper
- `11434` Ollama

## Guardrails

- Do not use credentials unless existing SSH key auth works without prompting.
- Do not hard-code passwords.
- Do not dump environment variables.
- Do not read `.env` files.
- Do not read app databases.
- Do not collect service logs without a separate request.
- Save raw evidence locally, then prepare a sanitized summary for review.
