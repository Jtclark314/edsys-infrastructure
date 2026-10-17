#!/usr/bin/env python3
"""Select and validate one exported tracker locally, without printing its keys."""
import datetime as dt
import json
import os
from pathlib import Path
import sys

from findmy import FindMyAccessory

ROOT = Path('/home/jeremy/.local/share/edsys-findmy/2026-10-17/private')


def validate(data):
    for key, length in [('master_key', 28), ('skn', 32), ('sks', 32)]:
        if not isinstance(data.get(key), str) or len(bytes.fromhex(data[key])) != length:
            raise ValueError('Missing or incompatible tracker key information')
    if not isinstance(data.get('identifier'), str) or not data['identifier']:
        raise ValueError('Tracker identifier is missing')
    now = dt.datetime.now(dt.timezone.utc)
    for field in ['paired_at', 'alignment_date']:
        value = data.get(field)
        if not isinstance(value, str):
            raise ValueError('Pairing or key alignment date is missing')
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or not dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc) < parsed <= now + dt.timedelta(days=1):
            raise ValueError('Pairing or key alignment date is invalid')
    if type(data.get('alignment_index')) is not int or data['alignment_index'] < 0:
        raise ValueError('Key alignment index is missing')
    # Exercise the actual pinned library parser. Never log the parsed object.
    accessory = FindMyAccessory.from_json(data)
    if accessory is None:
        raise ValueError('Pinned FindMy parser rejected the tracker')


def main():
    os.umask(0o077)
    if not sys.stdin.isatty():
        raise SystemExit('Use a private interactive terminal to choose the tracker.')
    paths = sorted((ROOT / 'export/keys').glob('*.json'))
    if not paths:
        raise SystemExit('No exported tracker JSON files were found. Stop and report the export stage.')
    print('\nChoose one already-paired tracker for the HA trial:')
    for index, path in enumerate(paths, 1):
        data = json.loads(path.read_text())
        # Keep terminal controls and multiline names out of the local menu.
        label = str(data.get('name', 'Unnamed tracker'))[:100]
        label = ''.join(c if c.isprintable() else ' ' for c in label)
        print(f'{index}. {label}')
    try:
        index = int(input('Tracker number: ')) - 1
        if index < 0 or index >= len(paths):
            raise ValueError('Invalid selection')
        chosen = paths[index]
        data = json.loads(chosen.read_text())
        validate(data)
    except (ValueError, TypeError, KeyError):
        raise SystemExit('Selection or tracker data is incompatible. No tracker was staged; report this status without pasting keys.') from None
    destination = ROOT / 'selected-tracker.json'
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump(data, output)
        output.write('\n')
    print('One tracker validated and staged privately. Tell Codex: tracker export completed.')


if __name__ == '__main__':
    main()
