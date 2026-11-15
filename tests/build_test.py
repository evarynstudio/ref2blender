"""Run with:  blender --background --factory-startup --python tests/build_test.py

Modelling helpers (under safe mode) and free render cameras.
"""

import os
import struct
import sys

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import build_tools, scene_tools  # noqa: E402

ref2blender.register()
OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)

# --- helpers, through run_code with safe mode on --------------------------------
CODE = """
removed = clear_model()
paint = make_material("Paint", (0.2, 0.4, 0.1))
glass = make_material("Glass", (0.05, 0.05, 0.08))
tyre = make_material("Tyre", (0.03, 0.03, 0.03))
rim = make_material("Rim", (0.8, 0.6, 0.2))

SECTIONS = [  # y, half width, height
    (-2.0, 0.6, 0.5),
    (0.0, 0.9, 0.8),
    (2.0, 0.7, 0.6),
]
body = loft_symmetric("Body", [(y, [(0, 0.1), (w, 0.1), (w, h), (0, h)]) for y, w, h in SECTIONS], paint)
arch = cylinder("Arch", 0.4, 3.0, (0, -1.2, 0.3), axis="X")
boolean(body, arch)
row = lerp_table(SECTIONS, -1.0)
assert abs(row[1] - 0.75) < 1e-6 and abs(row[2] - 0.65) < 1e-6, row

cabin = loft("Cabin", [[(0.6, y, 0.75), (0.4, y, 1.2), (-0.4, y, 1.2), (-0.6, y, 0.75)] for y in (-0.6, 0.0, 0.8)],
             paint, cap=True)
gone = delete_faces(cabin, lambda c: abs(c.z - 0.75) < 1e-4)
panes = inset_panel(cabin, lambda c: abs(c.x) > 0.3, glass, thickness=0.05)

wheel = revolve("Wheel", [(0, -0.1), (0.3, -0.1, 0), (0.3, 0.1, 0), (0.2, 0.1, 0), (0, 0.05, 1)],
                [tyre, rim], axis="X", segments=16, location=(0.8, -1.2, 0.3))
plank = box("Plank", (1.6, 0.3, 0.04), (0, 1.9, 1.0), paint, rotation=(-6, 0, 0))
print("removed", removed, "gone", gone, "panes", panes, "objects", len(collection.objects))
"""
ns = scene_tools.new_namespace()
for name in build_tools.HELPERS:
    assert name in ns, name
ok, out = scene_tools.run_code(CODE, ns, safe=True)
assert ok, out
assert "gone 2 panes 4 objects 4" in out, out
objs = bpy.data.collections["Ref2Blender"].objects
assert set(o.name for o in objs) == {"Body", "Cabin", "Wheel", "Plank"}, [o.name for o in objs]
assert "Arch" not in bpy.data.objects and "Arch" not in bpy.data.meshes, "boolean tool not cleaned up"

body = objs["Body"]
assert not body.modifiers and len(body.data.polygons) > 12, len(body.data.polygons)
lo = min((body.matrix_world @ v.co).z for v in body.data.vertices)
assert abs(lo - 0.1) < 1e-4, lo

wheel = objs["Wheel"]
zs = [(wheel.matrix_world @ v.co).z for v in wheel.data.vertices]
xs = [(wheel.matrix_world @ v.co).x for v in wheel.data.vertices]
assert abs(min(zs)) < 1e-5, min(zs)                       # 16 segments: rests on the ground
assert abs(min(xs) - 0.7) < 1e-5 and abs(max(xs) - 0.9) < 1e-5, (min(xs), max(xs))
assert {p.material_index for p in wheel.data.polygons} == {0, 1}
assert [m.name for m in wheel.data.materials] == ["Tyre", "Rim"]

cabin = objs["Cabin"]
assert [m.name for m in cabin.data.materials] == ["Paint", "Glass"]
assert sum(p.material_index == 1 for p in cabin.data.polygons) == 4
assert abs(objs["Plank"].rotation_euler.x + 0.10472) < 1e-4

# Running the same script again rebuilds instead of piling up.
ok, out = scene_tools.run_code(CODE, ns, safe=True)
assert ok and "removed 4" in out and len(objs) == 4, out
assert len([m for m in bpy.data.meshes if m.name.startswith(("Body", "Cabin", "Wheel", "Plank"))]) == 4

ok, out = scene_tools.run_code('loft("Bad", [[(0, 0, 0)]])', ns, safe=True)
assert not ok and "loft needs" in out, out
print("helpers ok")

# --- the helper list given to the MCP side matches the add-on's -------------------
server_src = open(os.path.join(ROOT, "src", "ref2blender_mcp", "server.py"), encoding="utf-8").read()
scope = {}
exec('HELPERS_DOC = """' + server_src.split('HELPERS_DOC = """')[1].split('"""')[0] + '"""', scope)
assert scope["HELPERS_DOC"] == build_tools.DOC, "HELPERS_DOC in server.py differs from build_tools.DOC"
for name in build_tools.HELPERS:
    assert name + "(" in build_tools.DOC, name
print("helper docs in step")


# --- free cameras ------------------------------------------------------------------
def png_size(png):
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", png[16:24])


def green_fraction(png, name):
    path = os.path.join(OUT, name + ".png")
    with open(path, "wb") as f:
        f.write(png)
    img = bpy.data.images.load(path)
    px = list(img.pixels)
    bpy.data.images.remove(img)
    hits = sum(1 for i in range(0, len(px), 4) if px[i + 1] > px[i] * 1.2 and px[i + 1] > px[i + 2] * 1.2)
    return hits / (len(px) / 4)


cams = [{"azimuth": 35, "elevation": 8, "lens": 35, "aspect": 1.5},
        {"azimuth": 90, "elevation": 0, "lens": 0},
        {"azimuth": -150, "elevation": 30, "aspect": 0.5}]
before = (bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y)
renders = scene_tools.render_views(["right"], 240, cameras=cams)
assert [n for n, _ in renders] == ["right", "camera_1", "camera_2", "camera_3"]
sizes = [png_size(p) for _, p in renders]
assert sizes == [(240, 240), (240, 160), (240, 240), (120, 240)], sizes
assert (bpy.context.scene.render.resolution_x, bpy.context.scene.render.resolution_y) == before
assert "R2B_TempCam" not in bpy.data.objects

# azimuth 90, orthographic, is the same picture as the fixed "right" view
assert renders[0][1] == renders[2][1] or abs(len(renders[0][1]) - len(renders[2][1])) < 64
# every camera frames the model: it is visible and does not fill the picture
for name, png in renders:
    frac = green_fraction(png, "cam_" + name)
    assert 0.05 < frac < 0.9, (name, frac)

assert scene_tools.check_cameras(None) == []
assert scene_tools.check_cameras([{}])[0] == {"azimuth": 0.0, "elevation": 10.0, "lens": 50.0, "aspect": 1.0}
for bad in ([{"azimuth": "x"}], [{"elevation": 95}], [{"lens": 5}], [{"zoom": 2}], "front", [1],
            [{"aspect": True}], [{}] * 7):
    try:
        scene_tools.check_cameras(bad)
    except ValueError:
        continue
    raise AssertionError(f"accepted {bad!r}")
print("cameras ok")
print("BUILD OK")

# --- a session that never connected must not delete another session's connection file
from ref2blender import bridge  # noqa: E402

assert not bridge.is_running()
existed = os.path.exists(bridge.INFO_PATH)
if not existed:
    os.makedirs(os.path.dirname(bridge.INFO_PATH), exist_ok=True)
    open(bridge.INFO_PATH, "w").close()
bridge.stop()
ref2blender.unregister()
assert os.path.exists(bridge.INFO_PATH), "stop() removed a connection file it did not own"
if not existed:
    os.remove(bridge.INFO_PATH)
print("BRIDGE FILE OK")
