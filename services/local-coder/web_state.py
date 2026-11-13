#!/usr/bin/env python3
"""Authenticated local status and fail-closed all-project idle guard for v1/v2."""
import argparse
import base64
import json
import os
from pathlib import Path
import sqlite3
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path('/mnt/ai-store/local-coder')


def read_api(path, port=4096):
    private = json.loads((ROOT / 'web/proxy.json').read_text())
    authorization = 'Basic ' + base64.b64encode(('opencode:' + private['backendPassword']).encode()).decode()
    request = Request(f'http://127.0.0.1:{port}' + path, headers={'Authorization': authorization})
    with urlopen(request, timeout=15) as response:
        return json.load(response)


def terminal_clients(process_root=Path('/proc')):
    """The V2 web API cannot see a separate standalone terminal instance."""
    clients = []
    for process in process_root.iterdir():
        if not process.name.isdigit():
            continue
        try:
            arguments = (process / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
            if not arguments or Path(os.fsdecode(arguments[0])).name != 'opencode' or b'run' not in arguments[1:]:
                continue
            environment = dict(value.split(b'=', 1) for value in (process / 'environ').read_bytes().split(b'\0') if b'=' in value)
            if environment.get(b'XDG_DATA_HOME') != os.fsencode(ROOT / 'data'):
                continue
            clients.append({'pid': int(process.name), 'directory': str((process / 'cwd').resolve()),
                            'standalone': b'--standalone' in arguments})
        except (OSError, ValueError):
            continue
    return clients


def status(port=4096):

    def get(path):
        return read_api(path, port)

    try:
        health = get('/api/info')
        version = health.get('version', '')
    except (HTTPError, ValueError):
        version = get('/global/health')['version']
    if version.startswith('2.'):
        busy = get('/api/session/active')['data']
    elif version.startswith('1.'):
        # V1 status is scoped to one location. Checking only the server's cwd
        # misses work in other projects and can cancel a real generation.
        busy = {}
        database = ROOT / 'data/opencode/opencode.db'
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            directories = [r[0] for r in db.execute('SELECT DISTINCT directory FROM session')]
        for directory in directories:
            for session, state in get('/session/status?directory=' + quote(directory, safe='')).items():
                if state.get('type') != 'idle':
                    busy[session] = {'directory': directory, 'state': state}
    else:
        raise ValueError('Unsupported OpenCode server version; refuse restart')
    clients = terminal_clients()
    return {'version': version, 'busy': busy, 'terminal_clients': clients, 'idle': not busy and not clients}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-idle', action='store_true')
    args = parser.parse_args()
    result = status()
    print(json.dumps(result))
    if args.require_idle and not result['idle']:
        raise SystemExit('OpenCode has active web or terminal work; let it finish or obtain explicit cancellation authority')
