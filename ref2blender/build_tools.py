"""Modelling helpers preloaded into the namespace of generated code.

They cover the shapes that hand-written scripts get wrong most often (lofted bodies,
lathe shapes, cut-outs, inset panels) and all create their object in the active
collection, so a script can stay a table of measures plus a few calls.
Angles are in degrees, sizes in metres.
"""

import math

import bmesh
import bpy
from mathutils import Euler, Matrix, Vector

AXES = {"X": Vector((1, 0, 0)), "Y": Vector((0, 1, 0)), "Z": Vector((0, 0, 1))}


def _materials(material):
    if material is None:
        return []
    return list(material) if isinstance(material, (list, tuple)) else [material]


def _axis_matrix(axis):
    """Rotation taking +Z to the given axis."""
    if axis not in AXES:
        raise ValueError(f'axis must be "X", "Y" or "Z", not {axis!r}')
    return AXES[axis].to_track_quat("Z", "Y").to_matrix().to_4x4()


def _finish(name, bm, material, location=(0, 0, 0), rotation=(0, 0, 0)):
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    for mat in _materials(material):
        mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    obj.rotation_euler = Euler([math.radians(a) for a in rotation])
    bpy.context.view_layer.active_layer_collection.collection.objects.link(obj)
    return obj


def clear_model():
    """Remove everything in the active collection (and the meshes nothing else uses).
    Call it first in a script that rebuilds the whole model."""
    col = bpy.context.view_layer.active_layer_collection.collection
    removed = 0
    for obj in list(col.all_objects):
        data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        removed += 1
        if isinstance(data, bpy.types.Mesh) and data.users == 0:
            bpy.data.meshes.remove(data)
    return removed


def lerp_table(rows, x):
    """Interpolate a table of number rows sorted by their first column; returns the row at x
    (clamped to the ends). Use it to place details on a surface defined by sections."""
    rows = list(rows)
    if x <= rows[0][0]:
        return list(rows[0])
    for a, b in zip(rows, rows[1:]):
        if a[0] <= x <= b[0]:
            t = (x - a[0]) / (b[0] - a[0]) if b[0] != a[0] else 0.0
            return [p + (q - p) * t for p, q in zip(a, b)]
    return list(rows[-1])


def box(name, size, location, material=None, rotation=(0, 0, 0)):
    """A box of the given (x, y, z) size centred on location."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=Vector(size), verts=bm.verts)
    return _finish(name, bm, material, location, rotation)


def cylinder(name, radius, depth, location, material=None, axis="Z", segments=16, radius2=None):
    """A cylinder (or cone frustum when radius2 is given) whose length runs along axis."""
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=segments, radius1=radius,
                          radius2=radius if radius2 is None else radius2, depth=depth,
                          matrix=_axis_matrix(axis))
    return _finish(name, bm, material, location)


def loft(name, sections, material=None, cap=True):
    """Skin a surface through sections: each one a closed ring of (x, y, z) points, all with
    the same number of points in the same order."""
    counts = {len(ring) for ring in sections}
    if len(sections) < 2 or len(counts) != 1 or counts.pop() < 3:
        raise ValueError("loft needs at least 2 sections with the same number (3 or more) of points")
    bm = bmesh.new()
    rings = [[bm.verts.new(p) for p in ring] for ring in sections]
    n = len(rings[0])
    for a, b in zip(rings, rings[1:]):
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new((a[i], a[j], b[j], b[i]))
    if cap:
        bm.faces.new(rings[0])
        bm.faces.new(rings[-1])
    return _finish(name, bm, material)


def loft_symmetric(name, stations, material=None, cap=True):
    """Loft a left/right symmetric body along Y. Each station is (y, half_profile), where
    half_profile lists (x, z) points for the +X half, from the bottom centre line round to
    the top centre line; points with x = 0 lie on the centre line and are not mirrored."""
    sections = []
    for y, half in stations:
        half = [tuple(p) for p in half]
        mirrored = [(-x, z) for x, z in reversed(half) if x > 1e-9]
        sections.append([(x, y, z) for x, z in half + mirrored])
    return loft(name, sections, material, cap)


def revolve(name, profile, material=None, axis="Z", segments=16, location=(0, 0, 0)):
    """Lathe a profile round an axis. profile lists (radius, height) points along the axis,
    optionally (radius, height, material_index) to choose the material of the band that ends
    at that point. A first or last point with radius 0 closes that end to a point; otherwise
    the end is capped flat. Use segments divisible by 4 for parts that stand on the ground."""
    if len(profile) < 2:
        raise ValueError("revolve needs at least 2 profile points")
    bm = bmesh.new()
    rot = _axis_matrix(axis)
    loops = []
    for point in profile:
        r, h = point[0], point[1]
        if r <= 1e-9:
            loops.append([bm.verts.new(rot @ Vector((0, 0, h)))])
        else:
            loops.append([bm.verts.new(rot @ Vector((r * math.cos(2 * math.pi * i / segments),
                                                      r * math.sin(2 * math.pi * i / segments), h)))
                          for i in range(segments)])
    for k in range(len(loops) - 1):
        a, b = loops[k], loops[k + 1]
        index = profile[k + 1][2] if len(profile[k + 1]) > 2 else 0
        for i in range(segments):
            j = (i + 1) % segments
            if len(a) == 1 and len(b) == 1:
                continue
            if len(a) == 1:
                face = bm.faces.new((a[0], b[i], b[j]))
            elif len(b) == 1:
                face = bm.faces.new((a[i], a[j], b[0]))
            else:
                face = bm.faces.new((a[i], a[j], b[j], b[i]))
            face.material_index = index
    if len(loops[0]) > 1:
        bm.faces.new(loops[0]).material_index = profile[1][2] if len(profile[1]) > 2 else 0
    if len(loops[-1]) > 1:
        bm.faces.new(loops[-1]).material_index = profile[-1][2] if len(profile[-1]) > 2 else 0
    return _finish(name, bm, material, location)


def boolean(target, tool, operation="DIFFERENCE"):
    """Apply a boolean to target using the object tool, then delete the tool.
    operation: "DIFFERENCE" (cut the tool out), "UNION" or "INTERSECT"."""
    if operation not in ("DIFFERENCE", "UNION", "INTERSECT"):
        raise ValueError('operation must be "DIFFERENCE", "UNION" or "INTERSECT"')
    mod = target.modifiers.new("R2B_Boolean", "BOOLEAN")
    mod.operation = operation
    mod.object = tool
    if hasattr(mod, "solver"):
        mod.solver = "EXACT"
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    baked = bpy.data.meshes.new_from_object(target.evaluated_get(depsgraph))
    old = target.data
    target.modifiers.remove(mod)
    target.data = baked
    baked.name = old.name
    if old.users == 0:
        bpy.data.meshes.remove(old)
    tool_mesh = tool.data
    bpy.data.objects.remove(tool, do_unlink=True)
    if tool_mesh.users == 0:
        bpy.data.meshes.remove(tool_mesh)
    return target


def inset_panel(obj, select, material, thickness=0.04):
    """Turn a region of faces into an inset panel of another material (windows, screens,
    doors): select(center) receives each face centre in object space and returns True for
    the faces of the region; the region keeps a frame of the given thickness."""
    mesh = obj.data
    if material.name not in mesh.materials:
        mesh.materials.append(material)
    index = mesh.materials.find(material.name)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    faces = [f for f in bm.faces if select(f.calc_center_median())]
    if faces:
        if thickness > 0:
            bmesh.ops.inset_region(bm, faces=faces, thickness=thickness, use_even_offset=True)
        for face in faces:
            face.material_index = index
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return len(faces)


def delete_faces(obj, select):
    """Delete the faces whose centre makes select(center) true (faces hidden inside
    another part, for example). Returns how many were removed."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    faces = [f for f in bm.faces if select(f.calc_center_median())]
    bmesh.ops.delete(bm, geom=faces, context="FACES")
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    return len(faces)


HELPERS = {
    "clear_model": clear_model,
    "lerp_table": lerp_table,
    "box": box,
    "cylinder": cylinder,
    "loft": loft,
    "loft_symmetric": loft_symmetric,
    "revolve": revolve,
    "boolean": boolean,
    "inset_panel": inset_panel,
    "delete_faces": delete_faces,
}

DOC = """\
Helpers preloaded for modelling (angles in degrees, sizes in metres; each returns the object):
- clear_model(): empty the active collection; call it first so one script rebuilds everything.
- box(name, size, location, material=None, rotation=(0, 0, 0))
- cylinder(name, radius, depth, location, material=None, axis="Z", segments=16, radius2=None)
- loft(name, sections, material=None, cap=True): skin through rings of (x, y, z) points.
- loft_symmetric(name, stations, material=None, cap=True): left/right symmetric body along Y; \
each station is (y, [(x, z), ...]) for the +X half, from bottom centre round to top centre.
- revolve(name, profile, material=None, axis="Z", segments=16, location=(0, 0, 0)): lathe \
(radius, height[, material_index]) points round an axis; wheels, bottles, columns.
- boolean(target, tool, operation="DIFFERENCE"): apply and delete the tool (wheel arches, holes).
- inset_panel(obj, select, material, thickness=0.04): faces whose centre passes select(center) \
become an inset panel of another material (windows, screens).
- delete_faces(obj, select): remove faces hidden inside other parts.
- lerp_table(rows, x): interpolate a table of sections, to place details on its surface.
material may be one material or a list (for material_index)."""
