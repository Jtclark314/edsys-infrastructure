#!/usr/bin/env python3
"""Create a manual demo ZIP outside source. Never installs or contacts the Pi."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ADDON = 'service.edsys.bedroom-display'


def build(destination):
    files = [('kodi/addon.xml','addon.xml'), ('kodi/default.py','default.py'),
             ('model.py','model.py'), ('views.json','views.json'),
             ('kodi/service.py','service.py'), ('local_control.py','local_control.py'),
             ('manual_protocol.py','manual_protocol.py'),('kodi/manual_display.py','manual_display.py'),('kodi/decrypt_screen.py','decrypt_screen.py')]
    files.extend((str(p.relative_to(ROOT)), str(p.relative_to(ROOT))) for p in sorted((ROOT/'assets').glob('*.png')))
    if len(files) != 17:
        raise RuntimeError('Generate the eight original PNG assets first')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as z:
        for source, target in files:
            z.write(ROOT/source, ADDON+'/'+target)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('destination', type=Path)
    build(p.parse_args().destination)
