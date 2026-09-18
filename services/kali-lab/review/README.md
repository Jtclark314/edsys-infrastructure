# Kali home-network review

`kali_network_review.py` is a Python 3 standard-library script for the current
Kali VM's user-mode NAT path. The desktop copy belongs in
`/home/jeremy/Documents/kali_network_review.py`. Open it in Mousepad to read or
edit; run it from a terminal as the personal user, without sudo.

## Start here

Preview the default selected-server plan, with no network traffic or reports:

```bash
python3 ~/Documents/kali_network_review.py
```

Review pve-node3, including open-port inventory, light service identification,
SSH host keys and offered algorithms, TLS certificates, and HTTP headers:

```bash
python3 ~/Documents/kali_network_review.py --execute
```

Inventory the approved home subnet; this skips detailed application probes:

```bash
python3 ~/Documents/kali_network_review.py --profile inventory --targets 192.168.50.0/24 --execute
```

Then investigate selected addresses and ports (at most eight hosts per detail
run). Optional SMB checks negotiate dialect/capability/signing information;
they do not enumerate shares or read files:

```bash
python3 ~/Documents/kali_network_review.py --targets 192.168.50.51 192.168.50.54 --ports 22,80,443,445,8006 --smb-checks --execute
```

Use `--exclude ADDRESS [ADDRESS ...]` for devices you want left alone.
`--ports 1-1024,8006` replaces the default selection. At most 4096 ports are
accepted per run. See `--help` or the opening comments for all options.

## What the evidence means

- The default checks Nmap's 100 highest-frequency TCP ports plus explicit
  home-lab ports such as 8006 and 3020. It is deliberately narrower than the
  default 1000-port scan and is not exhaustive. Every report lists its ports.
- `--unprivileged -sT` explicitly selects TCP connect scans despite Kali's
  privileged wrapper. `-Pn` avoids relying on discovery through user-mode NAT;
  its assumed-up status is never reported as a confirmed device count.
- Detail probes only ports observed open during the same run. Ports 111, 515,
  631, 2049 and 9100 remain inventory-only to avoid RPC/printing probes.
- Method `table` means a port-number guess. Method `probed` means a response
  fingerprint; it still does not prove an exact installed build or vulnerability.
- Named NSE scripts are `ssh-hostkey`, `ssh2-enum-algos`, `ssl-cert`, and optional
  `smb2-security-mode` / `smb2-capabilities`. No broad vulnerability-script set.
- curl uses HEAD requests, ignores personal curl configuration and proxy
  environment, follows no redirects, supplies no credentials, and retains TLS
  verification. OpenSSL captures protocol/cipher/certificate verification
  diagnostics. Only positively identified HTTP/TLS services are enriched,
  capped at eight endpoints per host. IP-based checks may differ from access
  through a hostname, and self-signed certificates can make curl fail.
- The port-scan cap defaults to 40 probes/second; it does not limit every NSE,
  version-detection, or application request. Individual hosts have 180-second
  Nmap limits; initial inventory has a 30-minute process limit. A subnet run
  may take many minutes or finish partially. Timeouts are recorded, not hidden.
- No UDP, ARP, OS fingerprinting, password guessing or exploitation. Active
  probes can affect fragile devices; begin with a known server. Missing results
  do not prove a host offline or secure.

## Reports and recovery

Each executed run creates a private timestamped directory under
`~/Documents/network-reviews/`. Read `SUMMARY.md` first. `ports.csv` and
`services.json` carry structured findings, `coverage.json` lists exact scope,
`commands.json` records command arguments/status/timeouts, and raw Nmap and
HTTP/TLS logs preserve the evidence. Reports stay private outside Git/RAG.
No service, firewall, package or persistent system setting is changed.

Ctrl+C terminates the current tool process group and writes a partial summary.
Exit code 0 means inventory completed and every launched command returned
success, not that the network passed a security audit. Exit code 2 means a
failed/incomplete check or invalid arguments; inspect the report or terminal.
Existing reports and the original user scan are preserved. Removing the new
script/README reverses this deployment; no daemon or scheduled task exists.

## Offline verification

```bash
python3 -m unittest discover -s services/kali-lab/review/tests -v
```

Tests use synthetic XML and mocked network tools, including scope rejection,
NAT status interpretation, detail-port selection, dry-run side-effect checks,
partial coverage, metadata escaping and local process timeouts. Guest checks
cover parsing/help/plan output and installed NSE metadata. Delivery does not
execute a live network scan; real-world scan results remain unverified until
an owner-initiated run.
