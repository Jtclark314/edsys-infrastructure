"""Inspect existing qualification meshes, save PLY and reopen it independently."""
import json
import os
from pathlib import Path
import numpy as np
import trimesh

root = Path(os.environ['EDSYS_3D_WORKSPACE']) / 'projects/example-project'
reports = []
for name in ['cadquery-sample.stl', 'blender-sample-mm.stl', 'openscad-sample.stl', 'openscad-reopened.stl']:
    mesh = trimesh.load_mesh(root / 'exports' / name)
    assert isinstance(mesh, trimesh.Trimesh)
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.is_volume
    assert np.isfinite(mesh.vertices).all() and mesh.volume > 0
    assert np.allclose(mesh.extents, [30,20,10], atol=0.01)
    reports.append({'file':name,'watertight':True,'volume_mm3':float(mesh.volume),
                    'bounds_mm':mesh.extents.tolist(),'faces':len(mesh.faces)})
    path = root / 'exports' / (Path(name).stem + '.ply')
    mesh.export(path)
    reopened = trimesh.load_mesh(path)
    assert reopened.is_volume and np.allclose(reopened.bounds, mesh.bounds)
    assert abs(reopened.volume - mesh.volume) < 0.01
report = {'status':'pass', 'version':trimesh.__version__, 'meshes':reports,
          'PLY_roundtrips':len(reports), 'manufacturing_approval':False}
(root / 'brief/qualification/trimesh.json').write_text(json.dumps(report, indent=2))
print('PASS Trimesh', trimesh.__version__, 'four watertight meshes, dimensions and PLY save/reopen')
