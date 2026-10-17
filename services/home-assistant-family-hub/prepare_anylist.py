#!/usr/bin/env python3
"""Download and verify the pinned public AnyList package into a new directory.

This does not contact an AnyList account, install into HA, or restart services.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import stat
import urllib.request
import zipfile

MAX_ARCHIVE = 2 * 1024 * 1024
MAX_UNPACKED = 4 * 1024 * 1024


def verify_archive(raw, pins):
    if len(raw) > MAX_ARCHIVE or hashlib.sha256(raw).hexdigest() != pins['asset_sha256']:
        raise ValueError('Public package digest or size differs')
    files = {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for item in archive.infolist():
            name = PurePosixPath(item.filename)
            if name.is_absolute() or '..' in name.parts or '\\' in item.filename:
                raise ValueError('Unsafe archive path')
            mode = item.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError('Archive links are not supported')
            if item.is_dir():
                continue
            if item.filename not in pins['files'] or item.filename in files:
                raise ValueError('Unexpected or duplicate package member')
            if item.file_size > MAX_UNPACKED:
                raise ValueError('Oversized package member')
            content = archive.read(item)
            if hashlib.sha256(content).hexdigest() != pins['files'][item.filename]:
                raise ValueError('Package member digest differs')
            files[item.filename] = content
    if set(files) != set(pins['files']) or sum(map(len, files.values())) > MAX_UNPACKED:
        raise ValueError('Incomplete or oversized package')
    for name, content in files.items():
        if name.endswith('.py'):
            compile(content, name, 'exec')
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path, help='Verify an already downloaded public archive')
    args = parser.parse_args()
    pins = json.loads(Path(__file__).with_name('anylist-pins.json').read_text())
    if args.output.exists():
        raise SystemExit('Output already exists; inspect it rather than overwrite')
    if args.archive:
        with args.archive.open('rb') as stream:
            raw = stream.read(MAX_ARCHIVE + 1)
    else:
        with urllib.request.urlopen(pins['asset_url'], timeout=30) as response:
            raw = response.read(MAX_ARCHIVE + 1)
    files = verify_archive(raw, pins)
    args.output.mkdir(parents=True, mode=0o700)
    for name, content in files.items():
        target = args.output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o600)
    print(json.dumps({'version': pins['version'], 'verified_files': len(files), 'installed': False}))


if __name__ == '__main__':
    main()
