"""Run with:  blender --background --factory-startup --python tests/compare_test.py

Outline comparison against a reference image, and the model script kept in the .blend
(recording, the panel's measures, Rebuild).
"""

import os
import struct
import sys

import bpy
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import bridge, compare_tools, scene_tools, script_store  # noqa: E402

ref2blender.register()
OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)
bridge._state["namespace"] = scene_tools.new_namespace()
scene = bpy.context.scene

CODE = """\
# a small van
LENGTH, WIDTH = 2.0, 0.9       # metres
HEIGHT = 1.0
WHEEL_R = 0.25
SEGMENTS = 16
NOSE = -0.2
LABEL = "van"
sizes = (1, 2)

clear_model()
assert SEGMENTS >= 3, "too few segments"
paint = make_material("Paint", (0.1, 0.5, 0.2))
tyre = make_material("Tyre", (0.03, 0.03, 0.03))
box("Body", (WIDTH, LENGTH, HEIGHT - WHEEL_R), (0, 0, WHEEL_R + (HEIGHT - WHEEL_R) / 2), paint)
box("Hood", (WIDTH, 0.5, 0.3), (0, -LENGTH / 2 + NOSE, WHEEL_R + 0.15), paint)
for side in (1, -1):
    cylinder("Wheel" + str(side), WHEEL_R, 0.2, (side * WIDTH / 2, 0.5, WHEEL_R), tyre, axis="X",
             segments=SEGMENTS)
"""


def run(code):
    return bridge._handle({"cmd": "exec", "code": code})


def save(name, rgb):
    path = os.path.join(OUT, name)
    with open(path, "wb") as f:
        f.write(compare_tools._png(rgb))
    return path


def png_size(png):
    return struct.unpack(">II", png[16:24])


def on_background(name, camera=None, view=None, background=(1, 1, 1), size=300):
    """A fake reference picture: the current model rendered over a background."""
    if camera:
        png = scene_tools.render_views([], size, cameras=[camera], transparent=True)[0][1]
    else:
        png = scene_tools.render_views([view], size, transparent=True)[0][1]
    raw = os.path.join(OUT, "raw_" + name)
    with open(raw, "wb") as f:
        f.write(png)
    px, _ = compare_tools._load(raw, long_side=size)
    alpha = px[..., 3:4]
    back = np.broadcast_to(np.asarray(background, dtype=np.float32), px[..., :3].shape)
    return save(name, px[..., :3] * alpha + back * (1 - alpha)), raw


# --- the script is recorded --------------------------------------------------------
reply = run(CODE)
assert reply["ok"], reply
text = bpy.data.texts.get("Ref2Blender_Model.py")
assert text is not None and text.as_string() == CODE and text.use_fake_user
names = [p.name for p in scene.r2b.params]
assert names == ["LENGTH", "WIDTH", "HEIGHT", "WHEEL_R", "SEGMENTS", "NOSE"], names
values = {p.name: (round(p.value, 4), p.is_int) for p in scene.r2b.params}
assert values["NOSE"] == (-0.2, False) and values["SEGMENTS"] == (16.0, True), values
assert not script_store.unscripted()

assert not run("raise ValueError('no')")["ok"]
assert text.as_string() == CODE, "a failing step was recorded"
assert run('box("Roof_Box", (0.5, 0.8, 0.15), (0, 0.2, HEIGHT + 0.075), paint)')["ok"]
assert text.as_string().startswith(CODE) and script_store.STEP_MARK in text.as_string()
assert "Roof_Box" in text.as_string() and len(scene.r2b.params) == 6
reply = bridge._handle({"cmd": "script"})
assert reply["ok"] and reply["scripts"][0]["name"] == text.name and reply["scripts"][0]["asset"] is None
print("script recorded")

# --- outline comparison ------------------------------------------------------------
CAMERA = {"azimuth": 40, "elevation": 12, "lens": 50, "aspect": 1.5}
photo, raw = on_background("ref_photo.png", camera=CAMERA)
drawing, _ = on_background("ref_front.png", view="front")

result = compare_tools.compare_reference(photo, camera=CAMERA, size=300)
assert result["iou"] > 0.96, result["text"]
assert [n for n, _ in result["images"]] == ["overlay", "difference"]
assert png_size(result["images"][0][1]) == (300, 200), png_size(result["images"][0][1])
assert "plain background" in result["text"] and "ratio of the two outlines matches" in result["text"]

same = compare_tools.compare_reference(drawing, view="front", size=300)
assert same["iou"] > 0.96, same["text"]
alpha = compare_tools.compare_reference(raw, camera=CAMERA, size=300)
assert alpha["iou"] > 0.96 and "transparent background" in alpha["text"], alpha["text"]

# a busy background: no outline, so a box is needed and there is no score
px, _ = compare_tools._load(photo, long_side=300)
noise = np.random.default_rng(1).random(px[..., :3].shape).astype(np.float32)
subject = (np.abs(px[..., :3] - 1).max(axis=2) > 0.1)[..., None]
busy = save("ref_busy.png", np.where(subject, px[..., :3], noise))
try:
    compare_tools.compare_reference(busy, camera=CAMERA, size=300)
except ValueError as exc:
    assert "'box'" in str(exc), exc
else:
    raise AssertionError("scored an image with a busy background")
left, top, right, bottom = compare_tools._bbox(subject[..., 0])
box = [left / px.shape[1], top / px.shape[0], right / px.shape[1], bottom / px.shape[0]]
boxed = compare_tools.compare_reference(busy, camera=CAMERA, box=box, size=300)
assert boxed["iou"] is None and [n for n, _ in boxed["images"]] == ["overlay"], boxed["text"]

reply = bridge._handle({"cmd": "compare", "image": drawing, "view": "front", "size": 256})
assert reply["ok"] and reply["iou"] > 0.96 and len(reply["renders"]) == 2, reply.get("error")
for bad in ({"image": drawing}, {"image": drawing, "view": "front", "camera": CAMERA},
            {"image": drawing, "view": "sideways"}, {"image": os.path.join(OUT, "missing.png"), "view": "front"},
            {"image": __file__, "view": "front"}, {"image": drawing, "view": "front", "box": [0.5, 0, 0.2, 1]},
            {"image": drawing, "view": "front", "size": 9999}, {"image": 3, "view": "front"},
            {"image": drawing, "camera": {"elevation": 120}}):
    reply = bridge._handle(dict(bad, cmd="compare"))
    assert not reply["ok"] and reply["error"], bad
print("comparison ok")

# --- the panel's measures and Rebuild ----------------------------------------------
objects = bpy.data.collections["Ref2Blender"].objects
count = len(objects)
scene.r2b.params["WIDTH"].value = 1.35
scene.r2b.params["SEGMENTS"].value = 8
scene.r2b.params["NOSE"].value = -0.3
assert bpy.ops.r2b.rebuild() == {"FINISHED"}
source = bpy.data.texts["Ref2Blender_Model.py"].as_string()
assert "LENGTH, WIDTH = 2.0, 1.35       # metres" in source, source[:200]
assert "SEGMENTS = 8\n" in source and "NOSE = -0.3\n" in source and "HEIGHT = 1.0\n" in source
objects = bpy.data.collections["Ref2Blender"].objects
assert len(objects) == count and "Roof_Box" in objects, [o.name for o in objects]
assert abs(objects["Body"].dimensions.x - 1.35) < 1e-4
assert len(objects["Wheel1"].data.polygons) == 10
assert abs(scene.r2b.params["WIDTH"].original - 1.35) < 1e-6 and not script_store.unscripted()

wide = compare_tools.compare_reference(drawing, view="front", size=300)
assert wide["iou"] < 0.9 and "too wide" in wide["text"] and "extra" in wide["text"], wide["text"]
print(wide["text"])
for name, png in wide["images"]:
    with open(os.path.join(OUT, "compare_wide_" + name + ".png"), "wb") as f:
        f.write(png)
for name, png in boxed["images"] + result["images"]:
    with open(os.path.join(OUT, "compare_photo_" + name + ".png"), "wb") as f:
        f.write(png)

# an object added by hand is not the script's, and a rebuild removes it
bpy.ops.mesh.primitive_cube_add(location=(3, 0, 0.5))
assert len(script_store.unscripted()) == 1
assert bpy.ops.r2b.rebuild() == {"FINISHED"} and len(objects) == count

# a script that fails leaves an error, not a crash
scene.r2b.params["SEGMENTS"].value = 2
assert bpy.ops.r2b.rebuild.poll()
try:
    bpy.ops.r2b.rebuild()
except RuntimeError as exc:
    assert "script failed" in str(exc), exc
else:
    raise AssertionError("a failing rebuild reported success")
scene.r2b.params["SEGMENTS"].value = 16
assert bpy.ops.r2b.rebuild() == {"FINISHED"} and len(objects) == count
print("rebuild ok")

# --- parameters: parsing and writing back ------------------------------------------
SOURCE = 'A = 1\nB, C = -2.5, +3   # "é" ñ\nname = 4\nD = [1, 2]\nE = 1e3\nif A:\n    F = 9\nA = 7\nG = True\n'
found = script_store.parameters(SOURCE)
assert [(n, v, i) for n, v, i, _ in found] == [("A", 1, True), ("B", -2.5, False), ("C", 3, True),
                                              ("E", 1000.0, False)], found
changed = script_store.apply_parameters(SOURCE, {"A": 2.0, "B": 0.30000001192, "C": 3.5, "E": 12})
assert changed == SOURCE.replace("A = 1\n", "A = 2\n").replace("-2.5, +3", "0.3, 3.5").replace("1e3", "12.0"), changed
assert script_store.parameters("def broken(:") == []

# --- assets, and code that no script accounts for ----------------------------------
assert run('new_asset("Crate")\nclear_model()\nSIDE = 0.5\nbox("Crate", (SIDE, SIDE, SIDE), (3, 0, SIDE / 2))')["ok"]
crate = bpy.data.texts.get("Ref2Blender_Model.Crate.py")
assert crate is not None and script_store.asset_of(crate) == "Crate"
assert [t.name for t in script_store.stored()] == ["Ref2Blender_Model.py", "Ref2Blender_Model.Crate.py"]
assert "SIDE" in scene.r2b.params and scene.r2b.params["SIDE"].script == crate.name
assert bpy.ops.r2b.rebuild() == {"FINISHED"}
assert "Crate" in bpy.data.collections and len(bpy.data.collections["Crate"].objects) == 1
assert len(bpy.data.collections["Ref2Blender"].all_objects) == count + 1
assert run(CODE)["ok"]                       # clear_model() on the whole collection drops the asset
assert [t.name for t in script_store.stored()] == ["Ref2Blender_Model.py"]

# --- the script travels with the .blend file ----------------------------------------
blend = os.path.join(OUT, "script_test.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
bpy.ops.wm.read_factory_settings(use_empty=True)
assert not script_store.stored()
bpy.ops.wm.open_mainfile(filepath=blend)
scene = bpy.context.scene
assert script_store.stored()[0].as_string() == CODE
assert [p.name for p in scene.r2b.params][:2] == ["LENGTH", "WIDTH"]
scene.r2b.params["HEIGHT"].value = 1.5
assert bpy.ops.r2b.rebuild() == {"FINISHED"}
assert abs(bpy.data.objects["Body"].dimensions.z - 1.25) < 1e-4
print("saved with the file")

assert bridge._handle({"cmd": "clear"})["ok"]
assert not script_store.stored() and not len(scene.r2b.params)
bpy.ops.mesh.primitive_cube_add()
bpy.context.object.name = "Mine"
bridge._state["namespace"] = scene_tools.new_namespace()
if "Mine" not in bpy.data.collections["Ref2Blender"].objects:
    bpy.data.collections["Ref2Blender"].objects.link(bpy.data.objects["Mine"])
assert run('box("Extra", (1, 1, 1), (0, 3, 0.5))')["ok"]
assert not script_store.stored(), "recorded a step on top of objects no script made"
assert run(CODE)["ok"] and script_store.stored()
print("COMPARE OK")
