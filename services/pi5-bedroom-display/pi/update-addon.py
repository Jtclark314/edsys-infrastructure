#!/usr/bin/env python3
"""Update only this owned add-on while disabled; keep Kodi and its player running."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

ADDON='service.edsys.bedroom-display'


def main(archive):
    if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!='edsys-edge-livingroom':raise RuntimeError('Reviewed Pi root only')
    if Path('/run/edsys-bedroom-display/control.sock').exists():raise RuntimeError('Disable only the owned add-on first; keep Kodi running')
    target=Path('/usr/share/kodi/addons')/ADDON
    if target.is_symlink() or not target.is_dir() or 'id="'+ADDON+'"' not in (target/'addon.xml').read_text():raise RuntimeError('Unmanaged add-on target')
    expected={'addon.xml','default.py','service.py','model.py','local_control.py','views.json','manual_protocol.py','manual_display.py','decrypt_screen.py','assets/black.png', *('assets/art-'+name+'.png' for name in ['dunes','orbit','hills','tide']),'assets/panel.png',*('assets/butler-'+p+'.png' for p in ['idle','listening','thinking','speaking-0','speaking-1','failed'])}
    expected|={'character.py',*(f'assets/phil/{state}-{frame:03}.png' for state in ['idle','listening','thinking','speaking','greeting','error'] for frame in range(48))}
    with zipfile.ZipFile(archive) as z:
        if len(z.namelist())!=len(expected) or set(z.namelist())!={ADDON+'/'+name for name in expected}:raise RuntimeError('Unexpected add-on archive')
        if any(i.file_size>2*1024*1024 for i in z.infolist()) or sum(i.file_size for i in z.infolist())>180*1024*1024:raise RuntimeError('Oversized add-on')
        payload={name:z.read(ADDON+'/'+name) for name in expected}
    for name,raw in payload.items():
        if name.endswith('.py'):compile(raw,name,'exec')
        if name.startswith('assets/phil/') and (raw[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',raw[16:24])!=(512,768)):raise RuntimeError('Unexpected Phil frame format')
    backup=Path('/var/backups/edsys-bedroom-display')/('manual-screen-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    backup.mkdir(mode=0o700);shutil.copytree(target,backup/'addon',ignore=shutil.ignore_patterns('__pycache__'))
    profile=Path('/var/lib/edsys-voice/.kodi/userdata/guisettings.xml')
    (backup/'manifest.json').write_text(json.dumps({'guisettings_sha256':hashlib.sha256(profile.read_bytes()).hexdigest(),'owned_files':sorted(payload),'before_hashes':{str(p.relative_to(target)):hashlib.sha256(p.read_bytes()).hexdigest() for p in target.rglob('*') if p.is_file() and '__pycache__' not in p.parts}}))
    for name,raw in payload.items():
        path=target/name;path.parent.mkdir(exist_ok=True,parents=True)
        fd,tmp=tempfile.mkstemp(prefix='.screen-',dir=path.parent)
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.chmod(tmp,0o644);os.replace(tmp,path)
    print(json.dumps({'installed_files':len(payload),'private_recovery':str(backup),'kodi_restart_performed':False}))

if __name__=='__main__':main(Path(sys.argv[1]))
