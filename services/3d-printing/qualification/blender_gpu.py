"""Optional small GPU gate AFTER the CPU baseline; does not change saved defaults."""
import bpy
import json
import os
from pathlib import Path
import subprocess

free = int(subprocess.check_output(['nvidia-smi','--query-gpu=memory.free','--format=csv,noheader,nounits'],text=True).strip())
if free < 2048:
    raise RuntimeError('GPU qualification deferred: less than 2 GiB free VRAM; do not stop EdSys services')
root=Path(os.environ['EDSYS_3D_WORKSPACE'])/'projects/example-project'
prefs=bpy.context.preferences.addons['cycles'].preferences
prefs.compute_device_type='CUDA'
prefs.get_devices()
devices=[d for d in prefs.devices if d.type=='CUDA']
if not devices:
    raise RuntimeError('No CUDA device enumerated; CPU baseline remains authoritative')
for d in prefs.devices:
    d.use=d.type=='CUDA'
s=bpy.context.scene
s.render.engine='CYCLES'
s.cycles.device='GPU'
s.cycles.samples=4
s.render.resolution_x=64
s.render.resolution_y=64
s.render.resolution_percentage=100
s.render.threads_mode='FIXED'
s.render.threads=2
s.render.filepath=str(root/'previews/blender-cuda-gate.png')
bpy.ops.render.render(write_still=True)
(root/'brief/qualification/blender-gpu.json').write_text(json.dumps({'status':'pass','scope':'Tiny separate CUDA render only; production scenes and GUI acceleration not qualified','devices':[d.name for d in devices],'initial_free_vram_mib':free,'default_render_settings_changed':False})+'\n')
print('PASS: separate 64x64 CUDA render; no saved defaults changed')
