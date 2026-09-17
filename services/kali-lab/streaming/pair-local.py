#!/usr/bin/env python3
"""Pair the sole pending relay client; never emit PIN or credential material."""
import base64
import json
from pathlib import Path
import re
import ssl
import sys
import time
import urllib.request

pin = sys.stdin.readline(32).strip()
if not re.fullmatch(r'[0-9]{4}', pin):
    raise SystemExit('Expected a four-digit PIN on stdin')
p = Path.home() / '.config/sunshine/web-admin.json'
if p.is_symlink() or p.stat().st_mode & 0o077:
    raise SystemExit('Private admin recovery file required')
auth = json.loads(p.read_text())
headers = {'Authorization': 'Basic ' + base64.b64encode(
    (auth['username'] + ':' + auth['password']).encode()).decode(),
    'Content-Type': 'application/json'}
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()))
def request(data=None):
    req = urllib.request.Request('https://127.0.0.1:47990/api/pin',
        headers=headers, data=None if data is None else json.dumps(data).encode())
    with opener.open(req, timeout=5) as result:
        return json.load(result)
for _ in range(15):
    pending = request().get('pairings', [])
    if len(pending) == 1 and pending[0].get('address') == '192.168.77.1':
        if request({'pin': pin, 'name': 'Nimo', 'pairing_id': pending[0]['id']}).get('status'):
            print('Nimo pairing accepted.')
            break
    elif pending:
        raise SystemExit('Unexpected pending pairing; nothing approved')
    time.sleep(1)
else:
    raise SystemExit('No matching Nimo pairing request was accepted')
