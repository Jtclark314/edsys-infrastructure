#!/usr/bin/env python3
"""Atomically refresh HA's local read-only catalog over existing hub SSH/QGA.

No new listener, API credential, arbitrary destination or query logging.
"""
from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import zlib

from export_knowledge import export

RECEIVER = r'''
import base64,hashlib,json,os,pathlib,sqlite3,sys,tempfile,zlib
p=json.load(sys.stdin);limit=16*1024*1024
decoder=zlib.decompressobj();data=decoder.decompress(base64.b64decode(p['data'],validate=True),limit+1)
if len(data)>limit or decoder.unconsumed_tail or not decoder.eof:raise RuntimeError('Catalog size/encoding rejected')
if hashlib.sha256(data).hexdigest()!=p['sha256']:raise RuntimeError('Catalog digest rejected')
root=pathlib.Path('/config/edsys_voice_knowledge')
if root.is_symlink():raise RuntimeError('Catalog directory symlink rejected')
root.mkdir(mode=0o700,parents=True,exist_ok=True)
destination=root/'index.sqlite'
if destination.is_symlink():raise RuntimeError('Catalog symlink rejected')
fd,name=tempfile.mkstemp(prefix='.index-',suffix='.sqlite',dir=root);tmp=pathlib.Path(name)
try:
 with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 os.chmod(tmp,0o640)
 with sqlite3.connect(tmp.as_uri()+'?mode=ro',uri=True) as c:
  if c.execute('pragma quick_check').fetchone()[0]!='ok':raise RuntimeError('Catalog integrity rejected')
  metadata=dict(c.execute('select key,value from metadata'))
  if metadata.get('voice_scope')!='edsys-only' or metadata.get('schema_version')!='2':raise RuntimeError('Catalog scope/schema rejected')
 os.replace(tmp,destination)
 fd=os.open(root,os.O_RDONLY);os.fsync(fd);os.close(fd)
 print(json.dumps({'updated':True,'sources':int(metadata['source_count']),'chunks':int(metadata['chunk_count']),'sha256':p['sha256']}))
finally:
 tmp.unlink(missing_ok=True)
'''


def main():
    with tempfile.TemporaryDirectory(prefix="edsys-voice-catalog-") as folder:
        path = Path(folder) / "index.sqlite"
        summary = export(path)
        raw = path.read_bytes()
        payload = {"sha256": hashlib.sha256(raw).hexdigest(), "data": base64.b64encode(zlib.compress(raw, 6)).decode()}
        command = shlex.join(["qm", "guest", "exec", "300", "--pass-stdin", "1", "--timeout", "45", "--", "docker", "exec", "-i", "homeassistant", "python3", "-c", RECEIVER])
        process = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ClearAllForwardings=yes", "-o", "ConnectTimeout=10", "pve-node3", command], input=json.dumps(payload), capture_output=True, text=True, timeout=55)
        if process.returncode:
            raise RuntimeError("Existing hub-to-HA catalog transport failed; no credential fallback")
        result = json.loads(process.stdout)
        if result.get("exitcode") or result.get("out-truncated"):
            raise RuntimeError("Catalog receiver rejected update; prior catalog retained")
        report = json.loads(result["out-data"])
        if report.get("sha256") != payload["sha256"]:
            raise RuntimeError("Catalog readback digest mismatch")
        print(json.dumps({**summary, "updated": True, "digest_verified": True}))


if __name__ == "__main__":
    main()
