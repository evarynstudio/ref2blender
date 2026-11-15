"""Run with:  blender --background --factory-startup --python tests/make_engine_samples.py -- <out_dir> [engine]

Exports the asymmetric test model (2 m long in X, 1.6 m deep, 0.5 m tall, with a
cone "nose" marking the front) so an engine import can be checked by hand or by
tests/unity/Ref2BlenderVerify.cs.
"""

import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import export_tools, scene_tools  # noqa: E402

args = sys.argv[sys.argv.index("--") + 1:]
out_dir = args[0]
engine = args[1] if len(args) > 1 else "unity"

ref2blender.register()
ok, out = scene_tools.run_code(
    """
red = make_material("Red", (0.8, 0.1, 0.1))
bpy.ops.mesh.primitive_cube_add(size=1, location=(3, 2, 0.25))
body = bpy.context.object; body.name = "Body"; body.scale = (2, 1, 0.5)
body.modifiers.new("Bevel", "BEVEL").width = 0.02
body.data.materials.append(red)
bpy.ops.mesh.primitive_cone_add(radius1=0.2, depth=0.6, location=(3, 1.2, 0.25), rotation=(1.5708, 0, 0))
nose = bpy.context.object; nose.name = "Nose"
nose.data.materials.append(make_material("Blue", (0.1, 0.2, 0.8)))
""",
    scene_tools.new_namespace(),
)
assert ok, out
print(export_tools.describe(export_tools.export_model(os.path.join(out_dir, "Plain"), engine=engine)))
print(export_tools.describe(export_tools.export_model(
    os.path.join(out_dir, "Full"), engine=engine, lods=2, collision=True, palette=True)))
ref2blender.unregister()
