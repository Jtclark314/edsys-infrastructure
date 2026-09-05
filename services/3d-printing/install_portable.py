#!/usr/bin/python3
"""Install ONE pinned portable app; qualify it before installing the next."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import edsys_3d

source = Path(__file__).resolve().parent
root = edsys_3d.storage()
app = sys.argv[1]
record = json.loads((source / 'releases.json').read_text())[app]
target = root / json.loads((source / 'apps.json').read_text())[app]['executable']
archive = root / 'cache/archives' / record['filename']
if target.exists():
    raise SystemExit('Existing version retained; refusing to overwrite it')
if not archive.exists():
    partial = archive.with_suffix(archive.suffix + '.partial')
    subprocess.run(['curl', '-fL', '--retry', '3', '--connect-timeout', '20',
                    '-o', str(partial), record['url']], check=True)
    if hashlib.file_digest(partial.open('rb'), 'sha256').hexdigest() != record['sha256']:
        raise SystemExit('Downloaded SHA256 mismatch; nothing installed')
    partial.replace(archive)
if hashlib.file_digest(archive.open('rb'), 'sha256').hexdigest() != record['sha256']:
    raise SystemExit('Archive SHA256 mismatch; nothing installed')
if archive.name.endswith('.AppImage'):
    archive.chmod(0o700)
    parent = target.parent.parent
    parent.mkdir(mode=0o700)
    log = root / 'temporary' / (app + '-extraction.log')
    with log.open('w') as output:
        subprocess.run([str(archive), '--appimage-extract'], cwd=parent,
                       stdout=output, stderr=subprocess.STDOUT, check=True)
else:
    with tarfile.open(archive) as bundle:
        bundle.extractall(root / 'apps', filter='data')
assert target.exists(), 'Expected portable executable not found'
print('Installed', app, record['version'], 'from verified archive; qualification required')
