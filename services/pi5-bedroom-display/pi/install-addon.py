#!/usr/bin/env python3
"""Install only the owned Kodi qualification add-on while its display is stopped."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import zipfile

ADDON='service.edsys.bedroom-display'


def main(archive):
    if os.geteuid()!=0 or subprocess.check_output(['hostname'],text=True).strip()!='edsys-edge-livingroom':
        raise RuntimeError('Pi root installation only')
    if subprocess.run(['systemctl','is-active','--quiet','edsys-bedroom-display.service']).returncode==0:
        raise RuntimeError('Stop only the display unit before add-on installation')
    target=Path('/usr/share/kodi/addons')/ADDON
    if target.exists():
        raise RuntimeError('Fresh add-on install only; compare owned source before updating')
    manifest=Path('/usr/share/kodi/system/addon-manifest.xml')
    original=manifest.read_text()
    if ADDON in original or original.count('</addons>')!=1:
        raise RuntimeError('Unexpected add-on manifest')
    checkpoint=Path('/var/backups/edsys-bedroom-display')/('pre-addon-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    checkpoint.mkdir(mode=0o700)
    shutil.copy2(manifest,checkpoint/'addon-manifest.xml')
    shutil.copytree('/var/lib/edsys-voice/.kodi',checkpoint/'kodi-profile',symlinks=True)
    allowed={'addon.xml','default.py','service.py','model.py','local_control.py','views.json','manual_protocol.py','manual_display.py','decrypt_screen.py',
             'assets/panel.png','assets/black.png', *('assets/art-'+name+'.png' for name in ['dunes','orbit','hills','tide']), *('assets/butler-'+p+'.png' for p in
                ['idle','listening','thinking','speaking-0','speaking-1','failed'])}
    allowed|={'character.py',*(f'assets/phil/{state}-{frame:03}.png' for state in ['idle','listening','thinking','speaking','greeting','error'] for frame in range(96))}
    with zipfile.ZipFile(archive) as z:
        names=set(z.namelist())
        if len(z.namelist())!=len(allowed) or names!={ADDON+'/'+name for name in allowed}:
            raise RuntimeError('Unexpected archive contents')
        if any(i.file_size>2*1024*1024 for i in z.infolist()) or sum(i.file_size for i in z.infolist())>360*1024*1024:raise RuntimeError('Oversized add-on')
        for name in sorted(names):
            raw=z.read(name)
            if name.endswith('.py'):compile(raw,name,'exec')
            if '/assets/phil/' in name and (raw[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',raw[16:24])!=(512,768)):raise RuntimeError('Unexpected Phil frame format')
        for name in sorted(names):
            p=Path('/usr/share/kodi/addons')/name
            p.parent.mkdir(parents=True,exist_ok=True,mode=0o755)
            p.write_bytes(z.read(name));p.chmod(0o644)
    # Optional system add-ons are enabled on first discovery. Never make this
    # qualification add-on a required component of Kodi startup.
    manifest.write_text(original.replace('</addons>','  <addon optional="true">'+ADDON+'</addon>\n</addons>'))
    print('Owned add-on installed; manifest/profile recovery checkpoint:',checkpoint)


if __name__=='__main__':
    main(Path(sys.argv[1]))
