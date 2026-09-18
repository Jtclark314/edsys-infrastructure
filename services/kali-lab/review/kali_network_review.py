#!/usr/bin/env python3
"""Jeremy's home-network review: NAT-aware inventory and selected-host detail.

OPEN THIS FILE IN MOUSEPAD TO READ OR EDIT IT. Opening it does not scan.

START HERE (in Kali's terminal, WITHOUT sudo):
  python3 ~/Documents/kali_network_review.py
      Prints a plan for pve-node3. No network traffic, no report files.

  python3 ~/Documents/kali_network_review.py --execute
      Reviews 192.168.50.54 with service, SSH and TLS checks.

  python3 ~/Documents/kali_network_review.py --profile inventory \
      --targets 192.168.50.0/24 --execute
      Checks common TCP ports plus selected home-lab application ports.

  python3 ~/Documents/kali_network_review.py --targets 192.168.50.51 \
      192.168.50.54 --ports 22,80,443,8006 --execute
      Detailed review of two selected devices and only those ports.

OPTIONAL: add --smb-checks to selected-host detail mode for SMB negotiation
checks, including whether signing is required. No shares/files are read.

Reports: ~/Documents/network-reviews/<timestamp>/SUMMARY.md, ports.csv,
coverage.json, commands.json, and raw Nmap normal/XML/grepable evidence.
Each run creates a new private directory and preserves earlier results.

WHAT THIS DOES:
  * Restricts targets to your approved 192.168.50.0/24; skips .0 and .255.
  * Forces TCP connect scanning, even with Kali's privileged Nmap wrapper.
  * Does NOT count -Pn's 'up' labels as verified devices.
  * Uses a moderate port-scan rate, finite timeouts, and serial enrichment.
    Nmap's rate cap does not cap all version/NSE/application requests.
  * Details only ports observed OPEN in this run. Probe fingerprints are
    distinguished from port-number labels. Printer/RPC ports stay inventory-only.
  * Uses explicitly named SSH/TLS NSE scripts; optional SMB negotiation scripts.
  * Uses HEAD requests for identified HTTP services, with no redirects, cookies,
    credentials, proxy environment, crawling, password guesses or exploitation.
  * Captures OpenSSL's TLS handshake diagnostics; IP/certificate name mismatch
    is not automatically a vulnerability. Name-based virtual hosts may differ.

LIMITS:
  Active checks can still affect fragile devices. Start with a known server.
  User-mode NAT is not a LAN Ethernet connection: ARP, OS fingerprinting,
  raw-packet/UDP scans, inbound callbacks and accurate host counts are outside
  this script. Missing/filtered ports do not prove a device is offline or secure.
  This is an evidence-gathering review, not an automated vulnerability verdict.
  Press Ctrl+C to stop; completed evidence and command statuses are retained.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import html
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

ALLOWED = ipaddress.ip_network('192.168.50.0/24')
# These supplement Nmap's top 100; notably 8006 is absent from its top 1000.
EXTRA_PORTS = {21, 22, 23, 53, 80, 111, 139, 443, 445, 515, 554, 631,
               2049, 3000, 3001, 3002, 3003, 3012, 3020, 3128, 3389, 5900,
               8006, 8008, 8009, 8080, 8081, 8085, 8086, 8095, 8097,
               8099, 8443, 8888, 9000, 9001, 9090, 9091, 9100, 12345}
# No protocol/version probes against printing endpoints or RPC/NFS.
DISCOVERY_ONLY = {111, 515, 631, 2049, 9100}
SCRIPTS = ['ssh-hostkey', 'ssh2-enum-algos', 'ssl-cert']
SMB_SCRIPTS = ['smb2-security-mode', 'smb2-capabilities']
MAX_DETAIL_HOSTS = 8
MAX_ENRICHED_ENDPOINTS = 8


def targets_from(values):
    result = set()
    for value in values:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError as error:
            raise ValueError(f'Use IPv4 addresses/CIDRs, not names or Nmap expressions: {value}') from error
        if network.version != 4 or not network.subnet_of(ALLOWED):
            raise ValueError(f'Target must be within {ALLOWED}: {value}')
        for ip in network:
            if ip not in (ALLOWED.network_address, ALLOWED.broadcast_address):
                result.add(ip)
    if not result:
        raise ValueError('No usable target addresses')
    return [str(ip) for ip in sorted(result)]


def ports_from(value):
    ports = set()
    for item in value.split(','):
        if not re.fullmatch(r'\d+(?:-\d+)?', item):
            raise ValueError('Ports must be comma-separated numbers or ranges')
        ends = list(map(int, item.split('-')))
        low, high = ends[0], ends[-1]
        if not 1 <= low <= high <= 65535:
            raise ValueError('Port range must be within 1..65535')
        ports.update(range(low, high + 1))
        if len(ports) > 4096:
            raise ValueError('Limit each review to at most 4096 selected ports')
    return sorted(ports)


def default_ports(path=Path('/usr/share/nmap/nmap-services')):
    ranked = []
    for line in path.read_text().splitlines():
        words = line.split()
        if len(words) >= 3 and not line.startswith('#') and words[1].endswith('/tcp'):
            try:
                ranked.append((float(words[2]), int(words[1].split('/')[0])))
            except ValueError:
                continue
    if len(ranked) < 100:
        raise ValueError('Nmap TCP frequency database is unavailable or incomplete')
    return sorted(EXTRA_PORTS | {port for _, port in sorted(ranked, reverse=True)[:100]})


def parse_xml(path):
    """Never infer real hosts from Nmap status='up' when -Pn was used."""
    root = ET.parse(path).getroot()
    rows, reported = [], set()
    for host in root.findall('host'):
        address = host.find("address[@addrtype='ipv4']")
        if address is None:
            continue
        ip = address.get('addr')
        reported.add(ip)
        for port in host.findall('ports/port'):
            state = port.find('state')
            if state is None or state.get('state') != 'open':
                continue
            service = port.find('service')
            data = service.attrib if service is not None else {}
            rows.append(dict(ip=ip, port=int(port.get('portid')), protocol=port.get('protocol'),
                             state='open', reason=state.get('reason', ''),
                             service=data.get('name', 'unknown'), method=data.get('method', 'table'),
                             confidence=data.get('conf', ''), product=data.get('product', ''),
                             version=data.get('version', ''), tunnel=data.get('tunnel', ''),
                             scripts=[dict(id=e.get('id'), output=e.get('output', ''))
                                      for e in port.findall('script')]))
    finished = root.find('runstats/finished')
    return rows, reported, finished is not None and finished.get('exit') == 'success'


def csv_cell(value):
    value = str(value).replace('\r', ' ').replace('\n', ' ')
    return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) else value


def md(value):
    return html.escape(str(value)).replace('|', '&#124;').replace('`', "'").replace('\n', ' ')


class Runner:
    def __init__(self, out):
        self.out, self.records = out, []
        self.env = {k: v for k, v in os.environ.items()
                    if k.lower() not in {'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy'}
                    and k != 'NMAP_PRIVILEGED'}

    def save(self):
        (self.out / 'commands.json').write_text(json.dumps(self.records, indent=2) + '\n')

    def run(self, argv, name, timeout):
        record = dict(name=name, argv=argv, timeout_seconds=timeout,
                      started_utc=datetime.now(timezone.utc).isoformat(), status='running')
        self.records.append(record)
        self.save()
        print(f'[{name}] {shlex.join(argv)}', flush=True)
        start = time.monotonic()
        with (self.out / f'{name}.log').open('wb') as log:
            process = None
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log,
                                           stderr=subprocess.STDOUT, env=self.env, start_new_session=True)
                code = process.wait(timeout=timeout)
                record.update(returncode=code, status='completed' if code == 0 else 'failed')
            except OSError as error:
                record.update(returncode=None, status='failed', error=str(error))
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                record.update(returncode=process.returncode if process else None,
                              status='interrupted' if isinstance(error, KeyboardInterrupt) else 'timeout')
                if isinstance(error, KeyboardInterrupt):
                    raise
            finally:
                record['elapsed_seconds'] = round(time.monotonic() - start, 2)
                self.save()
        return record['status'] == 'completed'


def nmap_base(rate):
    return ['nmap', '--unprivileged', '-sT', '-Pn', '-n', '--disable-arp-ping',
            '-T3', '--max-rate', str(rate), '--max-retries', '2',
            '--max-parallelism', '10', '--max-hostgroup', '16', '--reason']


def write_report(out, targets, ports, rows, details, runner, complete, notes):
    detailed = {(r['ip'], r['port']): r for r in details}
    evidence = [dict(row, **{k: v for k, v in detailed.get((row['ip'], row['port']), {}).items()
                            if k not in {'state', 'reason'}}) for row in rows]
    positive = sorted({r['ip'] for r in rows}, key=ipaddress.ip_address)
    coverage = dict(requested_targets=targets, scanned_tcp_ports=ports,
                    addresses_with_open_tcp=positive,
                    targets_without_observed_open_tcp=[ip for ip in targets if ip not in positive],
                    discovery_completed=complete, udp_scanned=False,
                    not_a_verified_device_count=True, notes=notes)
    (out / 'coverage.json').write_text(json.dumps(coverage, indent=2) + '\n')
    (out / 'services.json').write_text(json.dumps(evidence, indent=2) + '\n')
    fields = ['ip', 'port', 'protocol', 'state', 'reason', 'service', 'method',
              'confidence', 'product', 'version', 'tunnel']
    with (out / 'ports.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(fields)
        writer.writerows([[csv_cell(row.get(k, '')) for k in fields] for row in evidence])
    text = ['# Home-network review', '',
            f'- Requested addresses: {len(targets)}; selected TCP ports per address: {len(ports)}.',
            f'- Addresses with observed open TCP ports: {len(positive)}. This is NOT a device count.',
            f'- Open TCP endpoints observed: {len(rows)}; discovery completed: {complete}.',
            '- A completed command is not a security pass. Check command statuses and raw evidence.',
            '- Missing/filtered/no-open results are inconclusive; UDP and unselected ports were not checked.',
            '- Service method `table` is a port-number label. `probed` is a fingerprint, not absolute proof.',
            '- IP-based HTTP/TLS checks can differ from access using the intended DNS hostname.', '',
            '## Open-port evidence', '',
            '| Address | Port | Name | Identification | Product/version |',
            '| --- | --- | --- | --- | --- |']
    for r in evidence:
        text.append('| ' + ' | '.join(map(md, [r['ip'], r['port'], r['service'], r['method'],
                    ' '.join(filter(None, [r['product'], r['version']]))])) + ' |')
    text += ['', '## Follow-up leads, not vulnerability verdicts', '']
    candidates = [r for r in evidence if r['port'] in {21, 23, 445, 2049, 3389, 5900}]
    if not candidates:
        text.append('- No selected review-lead ports were observed. This does not establish security.')
    for r in candidates:
        text.append(f"- {r['ip']}:{r['port']}: confirm the actual service, intended access and authentication.")
    text += ['', '## Scope and execution notes', ''] + ['- ' + md(n) for n in notes]
    for record in runner.records:
        text.append(f"- {record['name']}: {record['status']}; see `{record['name']}.log`.")
    text += ['', '## Read the underlying evidence', '',
             '- `inventory.nmap` / `.xml` / `.gnmap`: initial port evidence.',
             '- `detail-*.nmap` / `.xml`: service fingerprints and selected SSH/TLS/SMB script output.',
             '- `http-*.log`: HTTP headers; authentication challenges are results, not failures.',
             '- `tls-*.log`: OpenSSL protocol/cipher and verification diagnostics.',
             '- `commands.json`: exact arguments, return codes, time limits, and timestamps.',
             '- `coverage.json`: explicit addresses/ports and important coverage limits.',
             '- Keep these reports private: they describe your network and may contain service metadata.', '']
    (out / 'SUMMARY.md').write_text('\n'.join(text))
    (out / 'README.txt').write_text('Run ended. Read SUMMARY.md and commands.json for coverage and failures.\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--targets', nargs='+', default=['192.168.50.54'])
    parser.add_argument('--exclude', nargs='*', default=[])
    parser.add_argument('--profile', choices=['inventory', 'detail'], default='detail')
    parser.add_argument('--ports', help='Explicit TCP ports/ranges, at most 4096; default top 100 plus lab ports')
    parser.add_argument('--rate', type=int, default=40, help='Nmap probe-rate cap, 5..100 (default 40)')
    parser.add_argument('--smb-checks', action='store_true', help='Add SMB negotiation scripts in detail mode')
    parser.add_argument('--execute', action='store_true', help='Perform the printed plan; default is plan-only')
    parser.add_argument('--output-root', type=Path, default=Path.home() / 'Documents/network-reviews')
    args = parser.parse_args(argv)
    try:
        targets = targets_from(args.targets)
        excluded = set(targets_from(args.exclude)) if args.exclude else set()
        targets = [ip for ip in targets if ip not in excluded]
        if not targets:
            raise ValueError('Exclusions removed every target')
        if args.profile == 'detail' and len(targets) > MAX_DETAIL_HOSTS:
            raise ValueError('Detail mode accepts at most 8 hosts; use --profile inventory for the subnet')
        if not 5 <= args.rate <= 100:
            raise ValueError('--rate must be between 5 and 100')
        if args.smb_checks and args.profile != 'detail':
            raise ValueError('--smb-checks requires --profile detail')
        ports = ports_from(args.ports) if args.ports else default_ports()
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f'PLAN: {args.profile}; {len(targets)} addresses; {len(ports)} selected TCP ports each.')
    print('Targets:', ', '.join(targets))
    base = nmap_base(args.rate)
    print(shlex.join(base + ['--host-timeout', '180s', '-p', ','.join(map(str, ports)),
                            '-oA', '<new-report-directory>/inventory', *targets]))
    if args.profile == 'detail':
        print('Then: light version detection and named SSH/TLS scripts on observed open ports;')
        print('HEAD requests and OpenSSL handshakes for positively identified HTTP/TLS services.')
        print('Printing/RPC ports stay discovery-only. At most 8 HTTP/TLS endpoints per host are enriched.')
    if not args.execute:
        print('PLAN ONLY: no network checks performed. Add --execute to run.')
        return 0
    required = ['nmap'] + (['curl', 'openssl'] if args.profile == 'detail' else [])
    missing = [tool for tool in required if not shutil.which(tool)]
    if missing:
        parser.error('Required tools are not installed: ' + ', '.join(missing))
    scripts = SCRIPTS + (SMB_SCRIPTS if args.smb_checks else [])
    if args.profile == 'detail':
        absent = [name for name in scripts if not Path('/usr/share/nmap/scripts', name + '.nse').is_file()]
        if absent:
            parser.error('Required NSE scripts are missing: ' + ', '.join(absent))
    os.umask(0o077)
    args.output_root.mkdir(parents=True, exist_ok=True)
    out = args.output_root / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out.mkdir(mode=0o700)
    (out / 'README.txt').write_text('Run in progress. commands.json records current/failed/finished commands.\n')
    runner = Runner(out)
    rows, details, notes, complete = [], [], [], False
    try:
        ok = runner.run(base + ['--host-timeout', '180s', '-p', ','.join(map(str, ports)),
                               '-oA', str(out / 'inventory'), *targets], 'inventory', 1800)
        try:
            rows, reported, xml_complete = parse_xml(out / 'inventory.xml')
            complete = ok and xml_complete and reported == set(targets)
        except (ET.ParseError, OSError, ValueError) as error:
            notes.append(f'Inventory XML incomplete/unreadable: {type(error).__name__}. Preserve raw logs; rerun selected targets.')
        if not complete:
            notes.append('Discovery did not finish cleanly for every target. Results may be partial.')
        if args.profile == 'detail':
            for ip in targets:
                opened = sorted({r['port'] for r in rows if r['ip'] == ip} - DISCOVERY_ONLY)
                if not opened:
                    continue
                name = 'detail-' + ip
                runner.run(base + ['-sV', '--version-light', '-v', '--script', ','.join(scripts),
                    '--script-args', 'ssh_hostkey=sha256', '--script-timeout', '20s',
                    '--host-timeout', '180s', '-p', ','.join(map(str, opened)),
                    '-oA', str(out / name), ip], name, 240)
                try:
                    identified, _, finished = parse_xml(out / (name + '.xml'))
                    details.extend(identified)
                    if not finished:
                        notes.append(f'{ip}: detail XML marked incomplete; treat fingerprints as partial.')
                except (ET.ParseError, OSError, ValueError):
                    notes.append(f'{ip}: detail XML missing/incomplete; inspect the raw log.')
                    continue
                candidates = [r for r in identified if r['method'] == 'probed'
                              and (r['service'].startswith('http') or r['tunnel'] == 'ssl')]
                if len(candidates) > MAX_ENRICHED_ENDPOINTS:
                    notes.append(f'{ip}: HTTP/TLS enrichment limited to the first 8 identified endpoints.')
                for r in candidates[:MAX_ENRICHED_ENDPOINTS]:
                    tls = r['tunnel'] == 'ssl' or r['service'].startswith('https')
                    endpoint = f"{ip}:{r['port']}"
                    if r['service'].startswith('http'):
                        url = ('https' if tls else 'http') + '://' + endpoint + '/'
                        runner.run(['curl', '-q', '--noproxy', '*', '--proto', '=http,https',
                                    '--connect-timeout', '5', '--max-time', '12', '--max-redirs', '0',
                                    '--head', '--silent', '--show-error', url],
                                   f"http-{ip}-{r['port']}", 15)
                    if tls:
                        runner.run(['openssl', 's_client', '-connect', endpoint, '-brief',
                                    '-verify_ip', ip], f"tls-{ip}-{r['port']}", 15)
        notes.append('No UDP/ARP/OS scan, exhaustive port scan, password guessing or exploit execution was performed.')
        notes.append('No authentication was attempted. HTTP redirects were not followed; TLS trust errors are retained.')
        notes.append('Discovery-only protocol ports: ' + ', '.join(map(str, sorted(DISCOVERY_ONLY))))
    except KeyboardInterrupt:
        notes.append('User interrupted the run. Completed raw evidence is retained; coverage is partial.')
        complete = False
    finally:
        write_report(out, targets, ports, rows, details, runner, complete, notes)
    print(f'REPORT: {out / "SUMMARY.md"}')
    failures = any(r['status'] != 'completed' for r in runner.records)
    return 0 if complete and not failures else 2


if __name__ == '__main__':
    raise SystemExit(main())
