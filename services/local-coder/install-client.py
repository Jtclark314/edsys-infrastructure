#!/usr/bin/env python3
"""Install the checksum-pinned OpenCode release without touching active state."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request

source = Path(__file__).resolve().parent
lock = json.loads((source / 'opencode-client.lock.json').read_text())
root = Path('/mnt/ai-store/apps/local-coder')
if not Path('/mnt/ai-store').is_mount():
    raise SystemExit('AI Store must be mounted')
target = root / ('opencode-' + lock['version'])
target.mkdir(parents=True, mode=0o700, exist_ok=True)
with tempfile.TemporaryDirectory(prefix='opencode-install-', dir=root) as temporary:
    archive = Path(temporary) / 'release.tar.gz'
    urllib.request.urlretrieve(lock['url'], archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest() != lock['sha256']:
        raise SystemExit('OpenCode release checksum mismatch')
    with tarfile.open(archive) as package:
        members = package.getmembers()
        if len(members) != 1 or members[0].name != 'opencode' or not members[0].isfile():
            raise SystemExit('Unexpected OpenCode release layout')
        package.extractall(temporary, filter='data')
    binary = Path(temporary) / 'opencode'
    binary.chmod(0o700)
    version = subprocess.check_output([str(binary), '--version'], text=True).strip()
    if version not in (lock['version'], 'opencode v' + lock['version']):
        raise SystemExit('OpenCode release version mismatch')
    binary.replace(target / 'opencode')
print('Installed checksum-verified OpenCode ' + lock['version'])
