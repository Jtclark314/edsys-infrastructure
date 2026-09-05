"""Native editable modeling sample; CPU rendering is the saved default."""
import bpy
import json
import os
from pathlib import Path
from mathutils import Vector

root = Path(os.environ['EDSYS_3D_WORKSPACE']) / 'projects/example-project'
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add()
obj = bpy.context.object
obj.name = 'EditableRoundedBlock'
obj.dimensions = (0.030, 0.020, 0.010)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bevel = obj.modifiers.new('EditableBevel', 'BEVEL')
bevel.width, bevel.segments = 0.001, 3
mat = bpy.data.materials.new('Blue')
mat.diffuse_color = (0.06, 0.28, 0.62, 1)
mat.use_nodes = True
mat.node_tree.nodes['Principled BSDF'].inputs['Base Color'].default_value = mat.diffuse_color
obj.data.materials.append(mat)
scene = bpy.context.scene
scene.unit_settings.system = 'METRIC'
scene.unit_settings.scale_length = 1
scene.render.engine = 'CYCLES'
scene.cycles.device = 'CPU'
scene.cycles.samples = 8
scene.render.threads_mode = 'FIXED'
scene.render.threads = 4
scene.render.resolution_x, scene.render.resolution_y = 384, 256
scene.render.resolution_percentage = 100
bpy.ops.object.camera_add(location=(0.075, -0.085, 0.065))
camera = bpy.context.object
camera.rotation_euler = (-camera.location).to_track_quat('-Z', 'Y').to_euler()
scene.camera = camera
bpy.ops.object.light_add(type='AREA', location=(0.04, -0.04, 0.08))
bpy.context.object.data.energy = 0.15
bpy.context.object.data.shape = 'DISK'
bpy.context.object.data.size = 0.08
scene.world.color = (0.3, 0.3, 0.3)
path = root / 'source/blender-sample.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(path))
bpy.ops.wm.open_mainfile(filepath=str(path))
obj = bpy.data.objects['EditableRoundedBlock']
assert len(obj.modifiers) == 1
assert all(abs(a-b) < 1e-6 for a,b in zip(obj.dimensions, (0.03,0.02,0.01)))
bpy.ops.object.select_all(action='DESELECT')
obj.select_set(True)
bpy.context.view_layer.objects.active = obj
bpy.ops.wm.stl_export(filepath=str(root/'exports/blender-sample-mm.stl'),
                      export_selected_objects=True, global_scale=1000)
bpy.context.scene.render.filepath = str(root / 'previews/blender-cpu.png')
bpy.ops.render.render(write_still=True)
(root / 'brief/qualification/blender.json').write_text(json.dumps({
    'status':'pass', 'version':bpy.app.version_string, 'native_reopen':True,
    'editable_modifier_retained':True, 'render_device':'CPU', 'STL_units':'mm'}))
print('PASS Blender native save/reopen, editable modifier, STL export and CPU render')
