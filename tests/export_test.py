"""Run with:  blender --background --factory-startup --python tests/export_test.py

Builds a small model, exports it for every engine, re-imports each file and
checks size, triangle count, pivot, and that the scene was left untouched.
"""

import os
import sys

import bpy
from mathutils import Vector

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import export_tools, scene_tools  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
ref2blender.register()

ns = scene_tools.new_namespace()
ok, out = scene_tools.run_code(
    """
red = make_material("Red", (0.8, 0.1, 0.1))
# Asymmetric on purpose: long in X, a nose pointing to -Y (the front), off-centre.
bpy.ops.mesh.primitive_cube_add(size=1, location=(3, 2, 0.25))
body = bpy.context.object; body.name = "Body"; body.scale = (2, 1, 0.5)
body.modifiers.new("Bevel", "BEVEL").width = 0.02
body.data.materials.append(red)
bpy.ops.mesh.primitive_cone_add(radius1=0.2, depth=0.6, location=(3, 1.2, 0.25), rotation=(1.5708, 0, 0))
nose = bpy.context.object; nose.name = "Nose"
nose.data.materials.append(make_material("Blue", (0.1, 0.2, 0.8)))
# A hand-built mesh without UVs, to exercise the automatic unwrap.
mesh = bpy.data.meshes.new("Fin")
mesh.from_pydata([(2.8, 2, 0.4), (3.2, 2, 0.4), (3.2, 2.3, 0.4), (2.8, 2.3, 0.4)], [], [(0, 1, 2, 3)])
fin = bpy.data.objects.new("Fin", mesh); collection.objects.link(fin)
fin.data.materials.append(red)
""",
    ns,
)
assert ok, out
scene = bpy.context.scene
print(export_tools.game_report(scene, budget=100))
names_before = sorted(o.name for o in bpy.data.objects)
meshes_before = len(bpy.data.meshes)
expected_tris = sum(
    export_tools.triangle_count(o.evaluated_get(bpy.context.evaluated_depsgraph_get()).data)
    for o in scene_tools.target_objects(scene)
)


def imported_bounds(path):
    before = set(bpy.data.objects)
    if path.endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    bpy.context.view_layer.update()
    lo = Vector((1e9,) * 3)
    hi = Vector((-1e9,) * 3)
    tris = 0
    total = Vector((0, 0, 0))
    count = 0
    meshes = [o for o in new if o.type == "MESH"]
    for o in meshes:
        tris += export_tools.triangle_count(o.data)
        for v in o.data.vertices:
            p = o.matrix_world @ v.co
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
            total += p
            count += 1
    points = [o.matrix_world @ v.co for o in meshes for v in o.data.vertices]
    info = (len(meshes), tris, lo.copy(), hi.copy(), points,
            [tuple(round(s, 3) for s in o.scale) for o in meshes])
    for o in new:
        data = o.data
        bpy.data.objects.remove(o, do_unlink=True)
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
    return info


for engine in ("unity", "unreal", "godot", "generic"):
    info = export_tools.export_model(os.path.join(OUT, f"Test_{engine}"), engine=engine)
    print(export_tools.describe(info))
    assert info["triangles"] == expected_tris, (info["triangles"], expected_tris)
    assert info["auto_unwrapped"] == ["Fin"], info["auto_unwrapped"]
    assert sorted(o.name for o in bpy.data.objects) == names_before, "scene objects changed"
    assert len(bpy.data.meshes) == meshes_before, "leaked meshes"
    # The front is the side that ends in a point: the tip of the nose cone.
    count, tris, lo, hi, points, scales = imported_bounds(info["path"])
    size = hi - lo
    print(f"  reimport: {count} mesh, {tris} tris, min={tuple(round(v, 3) for v in lo)} "
          f"max={tuple(round(v, 3) for v in hi)} object scale={scales}")
    assert count == 1 and tris == expected_tris
    assert abs(lo.z) < 1e-3, "pivot is not at the base"
    assert abs(size.z - 0.5) < 1e-3
    if engine == "unreal":  # turned 90 degrees: the nose (front) now points to +X
        assert abs(size.x - 1.6) < 2e-2 and abs(size.y - 2.0) < 2e-2, size
        tip = [p.y for p in points if p.x > hi.x - 0.02]
        assert max(tip) - min(tip) < 0.05, "front should point to +X"
    else:
        assert abs(size.x - 2.0) < 2e-2 and abs(size.y - 1.6) < 2e-2, size
        tip = [p.x for p in points if p.y < lo.y + 0.02]
        assert max(tip) - min(tip) < 0.05, "front should point to -Y"
        assert abs(lo.x + hi.x) < 1e-3, "pivot is not centred"

sep = export_tools.export_model(os.path.join(OUT, "Test_separate"), engine="unity", join=False)
assert sep["objects"] == 3

# --- LODs + collision + palette -------------------------------------------------
counts_before = (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images), len(bpy.data.collections))
for engine, collider in (("unity", "Test_full_unity_Collider"), ("unreal", "UCX_Test_full_unreal_00"),
                         ("godot", "Test_full_godot-convcolonly")):
    name = f"Test_full_{engine}"
    counts_before = (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images),
                     len(bpy.data.collections))
    full = export_tools.export_model(os.path.join(OUT, name), engine=engine,
                                     lods=2, collision=True, palette=True)
    print(export_tools.describe(full))
    lods = full["lod_triangles"]
    assert len(lods) == 3 and lods[0] == expected_tris and lods[0] > lods[1] > lods[2] > 0, lods
    assert 0 < full["collision_triangles"] <= export_tools.COLLISION_MAX_TRIS
    assert full["materials"] == [f"{name}_Palette"] and full["palette_colours"] == 2
    assert os.path.isfile(os.path.join(OUT, f"{name}_palette.png"))
    assert (len(bpy.data.meshes), len(bpy.data.materials), len(bpy.data.images),
            len(bpy.data.collections)) == counts_before, "export leaked datablocks"
    assert sorted(o.name for o in bpy.data.objects) == names_before

    before = set(bpy.data.objects)
    if full["path"].endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=full["path"])
    else:
        bpy.ops.import_scene.gltf(filepath=full["path"])
    new = {o.name: o for o in bpy.data.objects if o not in before}
    print("  reimported objects:", sorted(new))
    for expected in (f"{name}_LOD0", f"{name}_LOD1", f"{name}_LOD2", collider):
        assert any(n.startswith(expected) for n in new), f"missing {expected}"
    lod0 = next(o for n, o in new.items() if n.startswith(f"{name}_LOD0"))
    uvs = {tuple(round(c, 3) for c in d.uv) for d in lod0.data.uv_layers.active.data}
    assert len(uvs) == 2, f"palette UVs should hit exactly 2 swatches, got {uvs}"
    for o in new.values():
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.outliner.orphans_purge(do_recursive=True)

# --- asset sets -----------------------------------------------------------------
ok, out = scene_tools.run_code(
    """
for i, name in enumerate(("Crate", "Barrel")):
    new_asset(name)
    if name == "Crate":
        bpy.ops.mesh.primitive_cube_add(size=1, location=(10 + i * 3, 0, 0.5))
    else:
        bpy.ops.mesh.primitive_cylinder_add(radius=0.4, depth=1.2, vertices=12, location=(10 + i * 3, 0, 0.6))
    bpy.context.object.name = name + "_mesh"
    bpy.context.object.data.materials.append(make_material("Wood", (0.4, 0.25, 0.1)))
""",
    ns,
)
assert ok, out
assert scene_tools.asset_names(scene) == ["Crate", "Barrel"], scene_tools.asset_names(scene)
infos = export_tools.export_all(os.path.join(OUT, "set"), engine="unity", collision=True)
assert [os.path.basename(i["path"]) for i in infos] == ["Crate.fbx", "Barrel.fbx"]
assert infos[0]["size_m"] == [1.0, 1.0, 1.0] and infos[0]["triangles"] == 12
renders = scene_tools.render_views(["three_quarter"], 256, asset="Barrel")
assert renders[0][1][1:4] == b"PNG"
print(export_tools.game_report(scene, asset="Crate"))
assert scene_tools.clear_collection(scene) == 5 and not scene_tools.asset_names(scene)
ref2blender.unregister()
print("EXPORT OK")
