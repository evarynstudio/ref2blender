"""Run with:  blender --background --factory-startup --python tests/safe_test.py

Safe mode must let ordinary modelling code through and refuse file, process,
network and deferred-code access, both statically and at run time.
"""

import os
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import safety, scene_tools  # noqa: E402

ref2blender.register()
ns = scene_tools.new_namespace()
MARKER = os.path.join(ROOT, "tests", "out", "safe_mode_marker.txt")
if os.path.exists(MARKER):
    os.remove(MARKER)

ALLOWED = [
    # typical generated modelling code
    """
import math, random
from mathutils import Vector, Matrix
import bmesh
from dataclasses import dataclass

@dataclass
class Part:
    name: str
    size: float
    def __post_init__(self):
        self.size = float(self.size)

class Builder:
    def __init__(self, name):
        self.name = name
    def build(self):
        bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
        obj = bpy.context.object
        obj.name = self.name
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = 0.02
        bpy.ops.object.shade_smooth()
        return obj

obj = Builder(Part("Box", 1).name).build()
obj.data.materials.append(make_material("Red", (0.8, 0.1, 0.1)))
mesh = bpy.data.meshes.new("Tri")
mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
tri = bpy.data.objects.new("Tri", mesh)
collection.objects.link(tri)
bm = bmesh.new(); bm.from_mesh(mesh); bmesh.ops.triangulate(bm, faces=bm.faces[:]); bm.to_mesh(mesh); bm.free()
bpy.context.scene.render.resolution_x = 1920
areas = [a.type for a in bpy.context.screen.areas] if bpy.context.screen else []
print(len(collection.objects), getattr(obj, "name"), hasattr(obj, "location"), sorted([3, 1, 2]))
""",
    "import numpy as np\nprint(np.zeros(3).sum())",
]

BLOCKED = [
    f"open(r'{MARKER}', 'w').write('x')",
    "import os",
    "import subprocess",
    "from os import path",
    "import socket",
    "import urllib.request",
    "__import__('os')",
    "eval('1+1')",
    "exec('x = 1')",
    "().__class__.__bases__[0].__subclasses__()",
    "bpy.__builtins__",
    "bpy.ops.wm.quit_blender()",
    "bpy.ops.wm.save_as_mainfile(filepath='x.blend')",
    "o = bpy.ops\no.wm.quit_blender()",
    "from bpy import ops",
    "bpy.ops.export_scene.fbx(filepath='x.fbx')",
    "bpy.ops.image.open(filepath='x.png')",
    "bpy.ops.script.python_file_run(filepath='x.py')",
    "bpy.data.images.load('x.png')",
    "bpy.data.libraries",
    "bpy.app.handlers.frame_change_post.append(print)",
    "bpy.app.timers.register(print)",
    "bpy.context.scene.render.filepath = 'C:/x'",
    "import numpy as np\nnp.zeros(3).tofile('x.bin')",
    "getattr(bpy.ops, 'wm')",
    "getattr(bpy, 'o' + 'ps')",
    "getattr(print, '__self__')",
    "import importlib",
    "import sys",
    "bpy.utils.register_class(object)",
]

for code in ALLOWED:
    ok, out = scene_tools.run_code(code, ns, safe=True)
    assert ok, f"safe mode wrongly blocked:\n{code}\n{out}"
    print("allowed ->", out.splitlines()[0] if out else "(no output)")

for code in BLOCKED:
    ok, out = scene_tools.run_code(code, ns, safe=True)
    assert not ok and "safe mode" in out, f"NOT blocked:\n{code}\n{out}"
print(f"blocked {len(BLOCKED)} unsafe snippets")
assert not os.path.exists(MARKER)

# With safe mode off the same namespace runs unrestricted code again.
ok, out = scene_tools.run_code("import os\nprint(os.path.basename('a/b.txt'))", ns, safe=False)
assert ok and "b.txt" in out, out
# ...and turning it back on restores the restrictions.
ok, out = scene_tools.run_code("print(os.getcwd())", ns, safe=True)
assert not ok, "a module imported with safe mode off stayed reachable"

assert scene_tools.safe_mode_enabled() is True  # default when the preference is unreadable
ref2blender.unregister()
print("SAFE OK")
