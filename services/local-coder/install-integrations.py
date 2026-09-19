#!/usr/bin/env python3
"""Restore the exact reviewed GitHub MCP artifact; never fetch a floating version."""
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

source = Path(__file__).resolve().parent
lock = json.loads((source / 'github-mcp.lock.json').read_text())
root = Path('/mnt/ai-store/apps/local-coder') / ('github-mcp-' + lock['version'])
root.mkdir(parents=True, mode=0o700, exist_ok=True)
binary = root / 'github-mcp-server'
if binary.is_file() and hashlib.sha256(binary.read_bytes()).hexdigest() == lock['binary_sha256']:
    print('Reviewed GitHub MCP executable already installed')
else:
    with urllib.request.urlopen(lock['url'], timeout=120) as response:
        blob = response.read()
    if hashlib.sha256(blob).hexdigest() != lock['archive_sha256']:
        raise SystemExit('GitHub MCP archive checksum mismatch')
    archive = root / 'github-mcp-server.tar.gz'
    archive.write_bytes(blob)
    with tarfile.open(archive) as tf:
        member = next(m for m in tf.getmembers() if Path(m.name).name == 'github-mcp-server' and m.isfile())
        executable = tf.extractfile(member).read()
    if hashlib.sha256(executable).hexdigest() != lock['binary_sha256']:
        raise SystemExit('GitHub MCP executable checksum mismatch')
    binary.write_bytes(executable)
    binary.chmod(0o700)
    print('Reviewed GitHub MCP executable restored')
