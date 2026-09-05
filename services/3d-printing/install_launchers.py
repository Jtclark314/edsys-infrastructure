#!/usr/bin/python3
"""Create only EdSys-prefixed user launchers after app qualification."""
import os
from pathlib import Path
import subprocess
import edsys_3d

edsys_3d.storage()
home = Path.home()
menu = home / '.local/share/applications'
desktop = home / 'Desktop/3D Printing'
menu.mkdir(parents=True, exist_ok=True)
desktop.mkdir(exist_ok=True)
items = [('workspace', '3D Workspace', 'workspace', False)]
items += [(key, name, key, False) for key, name in [
    ('freecad','3D FreeCAD'), ('blender','3D Blender'), ('openscad','3D OpenSCAD'),
    ('orcaslicer','3D OrcaSlicer'), ('creality-print','3D Creality Print')]]
items += [('cadquery','3D CadQuery Console','cadquery -i',True),
          ('trimesh','3D Mesh Validation Console','trimesh -i',True)]
for key, name, command, terminal in items:
    content = ('[Desktop Entry]\nType=Application\nVersion=1.0\n'
               f'Name={name}\nExec=/usr/local/bin/edsys-3d {command}\n'
               f'Terminal={str(terminal).lower()}\nIcon=applications-engineering\n'
               'Categories=Graphics;Engineering;\n'
               'Comment=Mount-guarded AI Store 3D workspace; no printer approval implied\n')
    for folder in (menu, desktop):
        target = folder / ('edsys-3d-' + key + '.desktop')
        if target.exists() and target.read_text() != content:
            raise SystemExit('Conflicting launcher retained: ' + str(target))
        target.write_text(content)
        target.chmod(0o755)
        if folder == desktop:
            subprocess.run(['gio','set',str(target),'metadata::trusted','true'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
subprocess.run(['update-desktop-database',str(menu)],check=True)
print('Eight guarded launchers installed in the menu and Desktop/3D Printing')
