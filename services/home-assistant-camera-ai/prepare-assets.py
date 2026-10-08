#!/usr/bin/env python3
"""Download the reviewed stock timeline build to a private deployment directory."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('output', type=Path, help='Staging directory outside this repository')
args = parser.parse_args()
source = Path(__file__).resolve().parent
output = args.output.resolve()
repository = source.parents[1]
if output == repository or repository in output.parents:
    parser.error('Keep downloaded build assets outside the repository')
lock = json.loads((source / 'timeline-lock.json').read_text())
for name, digest in lock['files'].items():
    url = f"{lock['repository'].replace('https://github.com/', 'https://raw.githubusercontent.com/')}/{lock['version']}/dist/{name}"
    data = urllib.request.urlopen(url, timeout=30).read()
    if hashlib.sha256(data).hexdigest() != digest:
        raise SystemExit(f'Checksum mismatch: {name}; nothing from this file installed')
    path = output / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
print(f"Verified {len(lock['files'])} timeline files at {lock['version']}")
