"""Private SSH transport for the work laptop's interactive desktop dispatcher."""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

STATE = Path.home() / '.local/share/edsys-work-laptop-control'
CONFIG = Path(os.environ.get('EDSYS_WORK_LAPTOP_CONFIG', str(STATE / 'config.json')))


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def config() -> dict:
    return json.loads(CONFIG.read_text())


def remote(script: str, timeout: int = 50) -> str:
    c = config()
    prefix = "$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false);"
    encoded = base64.b64encode((prefix + script).encode('utf-16le')).decode()
    if len(encoded) > 28000:
        raise ValueError('Command exceeds Windows argument limit; transfer a script file with SCP and invoke its path')
    p = subprocess.run(['ssh', '-F', c['ssh_config'], '-o', 'BatchMode=yes',
                        '-o', 'ConnectTimeout=10', c['host'],
                        'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -EncodedCommand ' + encoded],
                       capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(p.stderr[:3000] or 'SSH command failed')
    return p.stdout.strip().lstrip('\ufeff')


@contextmanager
def serialized():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (STATE / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def validate_request(action: str, payload: dict) -> None:
    if action not in {'status', 'ui', 'screenshot', 'launch', 'powershell'}:
        raise ValueError('Unknown desktop action')
    if len(json.dumps(payload)) > 32000:
        raise ValueError('Request too large; use SSH file transfer for larger scripts/data')
    if action in {'ui', 'launch'}:
        args = payload.get('arguments', [])
        if not isinstance(args, list) or not all(isinstance(a, str) and '\0' not in a for a in args):
            raise ValueError('Arguments must be strings without NUL')


def download_artifact(name: str) -> Path:
    if not re.fullmatch(r'[a-f0-9]{32}\.(png|mp4)', name):
        raise ValueError('Unexpected artifact filename')
    c = config()
    folder = STATE / 'artifacts'
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = folder / name
    subprocess.run(['scp', '-q', '-F', c['ssh_config'],
                    c['host'] + ':' + c['remote_root'] + '/artifacts/' + name, str(path)],
                   check=True, capture_output=True, timeout=45)
    path.chmod(0o600)
    # Screenshots/recordings are short-lived evidence, never repository inputs.
    cutoff = datetime.now().timestamp() - 86400
    for old in folder.iterdir():
        if old.is_file() and old.stat().st_mtime < cutoff:
            old.unlink()
    return path


def request(action: str, **payload) -> dict:
    validate_request(action, payload)
    with serialized():
        c = config()
        ident = uuid.uuid4().hex
        body = dict(payload, id=ident, action=action,
                    expires=(datetime.now(timezone.utc) + timedelta(seconds=40)).isoformat())
        encoded = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
        # Atomic enqueue, one attempt. A lost response never causes replay.
        script = f"""
$r={literal(c['remote_root'])};$id='{ident}'
if(-not (Test-Path "$r/heartbeat.json")){{throw 'Desktop agent has no heartbeat'}}
$h=Get-Content "$r/heartbeat.json" -Raw -Encoding UTF8|ConvertFrom-Json
if(([DateTime]::UtcNow-[DateTime]::Parse($h.at).ToUniversalTime()).TotalSeconds -gt 90){{throw 'Desktop agent heartbeat is stale'}}
[IO.File]::WriteAllBytes("$r/requests/$id.tmp",[Convert]::FromBase64String('{encoded}'))
Move-Item "$r/requests/$id.tmp" "$r/requests/$id.json"
$end=[DateTime]::UtcNow.AddSeconds(35)
while([DateTime]::UtcNow -lt $end){{
 if(Test-Path "$r/responses/$id.json"){{Get-Content "$r/responses/$id.json" -Raw -Encoding UTF8;exit 0}}
 Start-Sleep -Milliseconds 150
}}
if(Test-Path "$r/requests/$id.json"){{Remove-Item "$r/requests/$id.json" -Force}}
throw 'Desktop response timed out; outcome unknown. Inspect before retry; no automatic replay.'
"""
        result = json.loads(remote(script))
        if result.get('id') != ident:
            raise RuntimeError('Response identity mismatch')
        if not result.get('ok'):
            raise RuntimeError(result.get('error', 'Desktop action failed'))
        value = result['value']
        if value.get('artifact'):
            value['local_artifact'] = str(download_artifact(value['artifact']))
        return value


def ui(arguments: list[str]) -> dict:
    result = request('ui', arguments=arguments)
    raw = result.get('stdout', '').strip()
    if raw:
        try:
            result['output'] = json.loads(raw)
            del result['stdout']
        except json.JSONDecodeError:
            pass
    return result


def powershell(script: str, administrative: bool = False) -> dict:
    """Use the normal desktop for application COM; SSH for administration."""
    if not administrative:
        return request('powershell', script=script)
    return {'stdout': remote(script), 'context': 'administrative SSH session, not interactive desktop'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='action', required=True)
    sub.add_parser('status')
    sub.add_parser('pause')
    u = sub.add_parser('ui'); u.add_argument('arguments', nargs=argparse.REMAINDER)
    s = sub.add_parser('screenshot'); s.add_argument('--monitor', type=int, default=-1)
    r = sub.add_parser('powershell'); r.add_argument('--file', required=True); r.add_argument('--admin', action='store_true')
    l = sub.add_parser('launch'); l.add_argument('executable'); l.add_argument('arguments', nargs=argparse.REMAINDER)
    args = p.parse_args()
    if args.action == 'ui':
        value = ui(args.arguments)
    elif args.action == 'powershell':
        value = powershell(Path(args.file).read_text(), args.admin)
    elif args.action == 'pause':
        remote('[IO.File]::WriteAllText(' + literal(config()['remote_root'] + '/paused') + ",'Paused from 9950x')")
        value = {'paused': True}
    else:
        value = request(args.action, **{k: v for k, v in vars(args).items() if k != 'action'})
    print(json.dumps(value, indent=2, ensure_ascii=False))
    if value.get('exit_code', 0):
        sys.exit(1)


if __name__ == '__main__':
    main()
