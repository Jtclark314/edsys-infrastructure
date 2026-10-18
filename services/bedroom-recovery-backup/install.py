#!/usr/bin/env python3
"""Install only this collector runtime. The existing backup owns its schedule."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile

SOURCE=Path(__file__).resolve().parent
TARGET=Path('/opt/edsys-bedroom-recovery')
FILES=('collect.py','common.py','pi_worker.py','nimo_worker.ps1')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--expected',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
    expected=json.loads(a.expected.read_text())
    if set(expected)!={'pi','nimo','abs_image_id'} or len(expected['pi'])!=602 or len(expected['nimo'])!=11:
        raise ValueError('Unexpected source pin manifest')
    for name in FILES:
        if name.endswith('.py'):compile((SOURCE/name).read_text(),name,'exec')
    if not a.apply:print('Collector installation candidate validated; no changes');return
    if os.geteuid()!=0 or TARGET.is_symlink():raise ValueError('root and non-symlink runtime required')
    if TARGET.exists():
        previous=json.loads((TARGET/'installed.json').read_text())
        for name,h in previous['files'].items():
            if (TARGET/name).is_symlink() or hashlib.sha256((TARGET/name).read_bytes()).hexdigest()!=h:
                raise ValueError('Runtime drift; preserve it')
    TARGET.mkdir(mode=0o700,exist_ok=True)
    hashes={}
    for name in FILES+('expected.json',):
        raw=json.dumps(expected,sort_keys=True).encode() if name=='expected.json' else (SOURCE/name).read_bytes()
        fd,tmp=tempfile.mkstemp(prefix='.install-',dir=TARGET)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o600);os.replace(tmp,TARGET/name);hashes[name]=hashlib.sha256(raw).hexdigest()
    path=TARGET/'installed.json';path.write_text(json.dumps({'files':hashes},sort_keys=True));path.chmod(0o600)
    print(json.dumps({'installed_files':len(hashes),'existing_backup_timer_reused':True}))


if __name__=='__main__':main()
