#!/usr/bin/env python3
"""Create a manual demo ZIP outside source. Never installs or contacts the Pi."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = 'service.edsys.bedroom-display'
sys.path.insert(0,str(ROOT))
from character import animation_files


def build(destination,animation_dir):
    files = [('kodi/addon.xml','addon.xml'), ('kodi/default.py','default.py'),
             ('model.py','model.py'), ('views.json','views.json'),
             ('kodi/service.py','service.py'), ('local_control.py','local_control.py'),
             ('manual_protocol.py','manual_protocol.py'),('kodi/manual_display.py','manual_display.py'),('kodi/decrypt_screen.py','decrypt_screen.py')]
    files.extend((str(p.relative_to(ROOT)), str(p.relative_to(ROOT))) for p in sorted((ROOT/'assets').glob('*.png')))
    if len(files) != 21:
        raise RuntimeError('Generate character assets and the four original art presets first')
    manifest=json.loads((animation_dir/'manifest.json').read_text())
    expected=animation_files()
    if set(manifest['files'])!=expected or manifest.get('fps')!=12 or manifest.get('remotion')!='4.0.489':
        raise RuntimeError('Render the exact bounded Phil animation set first')
    for name in expected:
        raw=(animation_dir/name).read_bytes()
        if len(raw)>1024*1024 or raw[:8]!=b'\x89PNG\r\n\x1a\n' or struct.unpack('>II',raw[16:24])!=(512,768) or hashlib.sha256(raw).hexdigest()!=manifest['files'][name]:
            raise RuntimeError('Invalid rendered Phil frame')
    files.append(('character.py','character.py'))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as z:
        for source, target in files:
            z.write(ROOT/source, ADDON+'/'+target)
        for name in sorted(expected):z.write(animation_dir/name,ADDON+'/'+name)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination', type=Path)
    p.add_argument('--animation-dir',type=Path,required=True)
    a=p.parse_args();build(a.destination,a.animation_dir)
