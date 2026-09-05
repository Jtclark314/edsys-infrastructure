"""Resolve BUNDLED generic profiles for offline software tests, not a printer."""
import json
import os
from pathlib import Path

root = Path(os.environ['EDSYS_3D_WORKSPACE'])
profiles = root / 'apps/orcaslicer-2.4.2/squashfs-root/resources/profiles'
out = root / 'projects/example-project/slicing/qualification-only-profiles'
out.mkdir(exist_ok=True)


def resolve(directory, name, seen=()):
    if name in seen:
        raise ValueError('Inheritance cycle')
    paths = list(directory.rglob(name + '.json'))
    if len(paths) != 1:
        raise ValueError('Ambiguous bundled profile: ' + name)
    obj = json.loads(paths[0].read_text())
    parent = obj.pop('inherits', '')
    result = resolve(directory, parent, (*seen, name)) if parent else {}
    result.update(obj)
    return result


for kind, directory, name in [
    ('machine', profiles / 'Custom/machine', 'MyMarlin 0.4 nozzle'),
    ('process', profiles / 'Custom/process', '0.20mm Standard @MyMarlin'),
    ('filament', profiles / 'OrcaFilamentLibrary/filament', 'Generic PLA @System'),
]:
    obj = resolve(directory, name)
    obj.update(name='QUALIFICATION ONLY - ' + kind, type=kind, **{'from':'user'})
    # CLI compatibility compares the inherited SYSTEM identity, not the user label.
    obj['inherits'] = name
    if kind == 'process':
        obj['compatible_printers'] = ['MyMarlin 0.4 nozzle']
        obj['print_settings_id'] = obj['name']
    elif kind == 'machine':
        obj['printer_settings_id'] = obj['name']
    else:
        obj['filament_settings_id'] = [obj['name']]
    obj.pop('setting_id', None)
    (out / (kind + '.json')).write_text(json.dumps(obj, indent=2))
(out / 'README.md').write_text('Bundled generic Marlin/PLA test inputs, NOT accepted printer/material profiles. No G-code is sent to any device.\n')
print('Resolved generic offline qualification profiles; no physical printer selected')
