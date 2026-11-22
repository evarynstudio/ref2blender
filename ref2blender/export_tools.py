"""Game-engine export and game-readiness report for the Ref2Blender collection.

Export never modifies the user's objects: it works on temporary copies with
modifiers applied, optionally joined, re-pivoted, UV-unwrapped, with LODs, a
collision hull and a shared palette texture.
"""

import math
import os

import bmesh
import bpy
from mathutils import Color, Matrix, Vector

from . import scene_tools

# engine -> file format
ENGINES = {
    "unity": "fbx",
    "unreal": "fbx",
    "godot": "glb",
    "generic": "glb",
}
PIVOTS = ("bottom", "center", "origin")
MAX_LODS = 4
COLLISION_MAX_TRIS = 128
PALETTE_CELL = 8  # pixels per colour swatch


def triangle_count(mesh):
    return sum(len(p.vertices) - 2 for p in mesh.polygons)


# ----------------------------------------------------------------------- report

def game_report(scene, budget=0, asset=None):
    """Text report on how game-ready the current model (or one asset) is."""
    objs = scene_tools.target_objects(scene, asset)
    if not objs:
        return "No geometry to report on."
    depsgraph = bpy.context.evaluated_depsgraph_get()
    total_tris = 0
    materials = set()
    warnings = []
    lines = []
    for obj in objs:
        ev = obj.evaluated_get(depsgraph)
        mesh = ev.to_mesh()
        try:
            tris = triangle_count(mesh)
            ngons = sum(1 for p in mesh.polygons if len(p.vertices) > 4)
            has_uv = bool(mesh.uv_layers)
            mats = [m.name for m in mesh.materials if m]
        finally:
            ev.to_mesh_clear()
        total_tris += tris
        materials.update(mats)
        lines.append(f"- {obj.name}: {tris} tris, materials: {', '.join(mats) or 'NONE'}"
                     f"{'' if has_uv else ', no UVs'}{f', {ngons} n-gons' if ngons else ''}")
        if not mats:
            warnings.append(f"{obj.name} has no material (engines show it pink or default grey).")
        if tris == 0:
            warnings.append(f"{obj.name} has no faces.")
        if any(s < 0 for s in obj.scale):
            warnings.append(f"{obj.name} has negative scale (normals are fixed on export, but check it).")

    lo, hi = scene_tools._bounds(objs)
    size = hi - lo
    title = f'asset "{asset}"' if asset else "whole collection"
    head = [
        f"Game-readiness report ({title}): {len(objs)} objects, {total_tris} triangles, {len(materials)} materials",
        f"Size: {size.x:.3f} x {size.y:.3f} x {size.z:.3f} m (X x Y x Z); lowest point Z={lo.z:.3f}",
    ]
    if budget:
        verdict = "within" if total_tris <= budget else "OVER"
        head.append(f"Triangle budget: {total_tris} / {budget} ({verdict} budget)")
        if total_tris > budget:
            warnings.append(f"Over the triangle budget by {total_tris - budget}.")
    if len(materials) > 4:
        warnings.append(f"{len(materials)} materials: each one is a separate draw call once the mesh is "
                        "joined. Exporting with a palette collapses them into one.")
    if abs(lo.z) > 0.005:
        warnings.append("The model does not rest on Z=0.")
    assets = scene_tools.asset_names(scene)
    if assets and not asset:
        head.append(f"Assets (sub-collections): {', '.join(assets)}")
    out = head + lines
    out.append("Warnings:" if warnings else "No warnings.")
    out.extend(f"  ! {w}" for w in warnings)
    return "\n".join(out)


# ---------------------------------------------------------------- export pieces

class _Temp:
    """Tracks every datablock created for an export so it can all be removed."""

    def __init__(self, scene):
        self.collection = bpy.data.collections.new("R2B_Export")
        scene.collection.children.link(self.collection)
        self.meshes = []
        self.materials = []
        self.images = []

    def mesh(self, mesh):
        self.meshes.append(mesh)
        return mesh

    def obj(self, name, data):
        obj = bpy.data.objects.new(name, data)
        self.collection.objects.link(obj)
        return obj

    def cleanup(self):
        for obj in list(self.collection.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(self.collection)
        for group, store in ((self.meshes, bpy.data.meshes), (self.materials, bpy.data.materials),
                             (self.images, bpy.data.images)):
            for block in group:
                try:
                    if block.users == 0:
                        store.remove(block)
                except ReferenceError:
                    pass


def _mesh_copy(obj, depsgraph):
    ev = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(ev, preserve_all_data_layers=True, depsgraph=depsgraph)
    if not mesh.polygons:
        bpy.data.meshes.remove(mesh)
        return None
    mesh.transform(obj.matrix_world)
    if obj.matrix_world.determinant() < 0:
        mesh.flip_normals()
    return mesh


def _skeleton_of(sources):
    """The armature that deforms the exported parts, or None. Raises if there are several."""
    rigs = []
    for o in sources:
        for mod in o.modifiers:
            if mod.type == "ARMATURE" and mod.object and mod.object.type == "ARMATURE" \
                    and mod.show_viewport and mod.object not in rigs:
                rigs.append(mod.object)
    if len(rigs) > 1:
        raise RuntimeError(
            "These parts use more than one skeleton (" + ", ".join(r.name for r in rigs) + "). "
            "Export each character on its own, with its asset name.")
    return rigs[0] if rigs else None


def _select_only(view_layer, objs):
    for o in view_layer.objects:
        o.select_set(False)
    for o in objs:
        o.select_set(True)
    view_layer.objects.active = objs[0]


def _unwrap(obj, view_layer):
    _select_only(view_layer, [obj])
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(island_margin=0.02)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")


def _apply_palette(copies, asset_name, folder, temp):
    """Collapse every material into one that samples a small colour-swatch texture.
    Returns the path of the PNG written next to the model."""
    colours = {}  # material name -> linear rgba
    roughness = []
    for c in copies:
        for mat in c.data.materials:
            if mat and mat.name not in colours:
                colours[mat.name] = tuple(mat.diffuse_color)
                roughness.append(mat.roughness)
    default_key = None
    if any((not c.data.materials) or any(m is None for m in c.data.materials) for c in copies):
        default_key = "\0default"
        colours[default_key] = (0.6, 0.6, 0.6, 1.0)
    names = list(colours)
    grid = max(1, math.ceil(math.sqrt(len(names))))
    side = grid * PALETTE_CELL

    image = bpy.data.images.new(f"{asset_name}_palette", side, side, alpha=False)
    temp.images.append(image)
    pixels = [0.0] * (side * side * 4)
    centres = {}
    for i, name in enumerate(names):
        col, row = i % grid, i // grid
        r, g, b = Color(colours[name][:3]).from_scene_linear_to_srgb()
        for y in range(row * PALETTE_CELL, (row + 1) * PALETTE_CELL):
            for x in range(col * PALETTE_CELL, (col + 1) * PALETTE_CELL):
                k = (y * side + x) * 4
                pixels[k:k + 4] = (r, g, b, 1.0)
        centres[name] = ((col + 0.5) / grid, (row + 0.5) / grid)
    image.pixels.foreach_set(pixels)
    path = os.path.join(folder, f"{asset_name}_palette.png")
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()

    mat = bpy.data.materials.new(f"{asset_name}_Palette")
    temp.materials.append(mat)
    mat.use_nodes = True
    mat.roughness = sum(roughness) / len(roughness) if roughness else 0.6
    nodes = mat.node_tree.nodes
    bsdf = next(n for n in nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Roughness"].default_value = mat.roughness
    tex = nodes.new("ShaderNodeTexImage")
    tex.image = image
    tex.interpolation = "Closest"
    mat.node_tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])

    for c in copies:
        mesh = c.data
        slot_names = [m.name if m else default_key for m in mesh.materials] or [default_key]
        uv = mesh.uv_layers.active or mesh.uv_layers.new(name="UVMap")
        for poly in mesh.polygons:
            key = slot_names[poly.material_index] if poly.material_index < len(slot_names) else default_key
            u, v = centres[key]
            for li in poly.loop_indices:
                uv.data[li].uv = (u, v)
            poly.material_index = 0
        mesh.materials.clear()
        mesh.materials.append(mat)
    return path, len(names)


def _hull_mesh(coords, name):
    bm = bmesh.new()
    for co in coords:
        bm.verts.new(co)
    result = bmesh.ops.convex_hull(bm, input=bm.verts[:])
    drop = {e for key in ("geom_interior", "geom_unused") for e in result[key]
            if isinstance(e, bmesh.types.BMVert)}
    if drop:
        bmesh.ops.delete(bm, geom=list(drop), context="VERTS")
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _decimated_mesh(source_mesh, ratio, name, temp, context):
    obj = temp.obj("R2B_decimate", temp.mesh(source_mesh.copy()))
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.ratio = ratio
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    mesh = bpy.data.meshes.new_from_object(obj.evaluated_get(depsgraph),
                                           preserve_all_data_layers=True, depsgraph=depsgraph)
    mesh.name = name
    bpy.data.objects.remove(obj, do_unlink=True)
    return temp.mesh(mesh)


def _collision_mesh(source_mesh, name, temp, context):
    hull = temp.mesh(_hull_mesh([v.co.copy() for v in source_mesh.vertices], name))
    tris = triangle_count(hull)
    if tris > COLLISION_MAX_TRIS:
        # Simplify, then hull again so the result is still convex.
        reduced = _decimated_mesh(hull, COLLISION_MAX_TRIS / tris, name, temp, context)
        hull = temp.mesh(_hull_mesh([v.co.copy() for v in reduced.vertices], name))
    return hull


def _collision_name(engine, asset_name):
    if engine == "unreal":
        return f"UCX_{asset_name}_00"   # Unreal's convex-collision naming convention
    if engine == "godot":
        return f"{asset_name}-convcolonly"  # Godot import hint: convex collision, no visual
    return f"{asset_name}_Collider"     # picked up by unity/Ref2BlenderPostprocessor.cs


# ----------------------------------------------------------------------- export

def export_model(filepath, engine="unity", join=True, pivot="bottom", triangulate=False,
                 lods=0, collision=False, palette=False, asset=None):
    """Export the Ref2Blender collection (or one asset sub-collection) for a game
    engine. Returns an info dict."""
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {list(ENGINES)}")
    if pivot not in PIVOTS:
        raise ValueError(f"pivot must be one of {list(PIVOTS)}")
    if not 0 <= lods <= MAX_LODS:
        raise ValueError(f"lods must be between 0 and {MAX_LODS}")
    fmt = ENGINES[engine]
    filepath = os.path.abspath(bpy.path.abspath(filepath))
    filepath = os.path.splitext(filepath)[0] + "." + fmt
    folder = os.path.dirname(filepath)
    asset_name = os.path.splitext(os.path.basename(filepath))[0]
    exporter = bpy.ops.export_scene.fbx if fmt == "fbx" else bpy.ops.export_scene.gltf
    if not exporter.poll():
        raise RuntimeError(f"Blender's {fmt.upper()} exporter is not available (enable it in Preferences > Add-ons).")

    with bpy.context.temp_override(**scene_tools._view3d_override()):
        context = bpy.context
        scene = context.scene
        view_layer = context.view_layer
        if context.object and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        view_layer.update()
        sources = scene_tools.target_objects(scene, asset)
        if not sources:
            raise RuntimeError("Nothing to export: no geometry found.")
        skeleton = _skeleton_of(sources)
        if skeleton and (lods or collision):
            raise RuntimeError("LODs and collision are not available for characters with a "
                               "skeleton yet: export without them.")
        if lods or collision:
            join = True  # LODs and the collision hull describe one mesh
        saved_pose = skeleton.data.pose_position if skeleton else None
        skeleton_name = skeleton.name if skeleton else None

        prev_selected = [o for o in view_layer.objects if o.select_get()]
        prev_active = view_layer.objects.active
        original_names = [(o, o.name) for o in sources]
        temp = _Temp(scene)
        try:
            os.makedirs(folder, exist_ok=True)
            # Free the real names so the exported objects carry them, not "Name.001".
            for o, name in original_names:
                o.name = "__r2b_src_" + name
            if skeleton:
                # Export the rest pose: the skin is bound in it, whatever pose the
                # character is in now. Restored below.
                skeleton.name = "__r2b_src_" + skeleton_name
                skeleton.data.pose_position = "REST"
                view_layer.update()
            depsgraph = context.evaluated_depsgraph_get()
            copies = []
            for (o, name) in original_names:
                mesh = _mesh_copy(o, depsgraph)
                if mesh is not None:
                    copy = temp.obj(name, temp.mesh(mesh))
                    if skeleton:
                        for group in o.vertex_groups:
                            if group.name not in copy.vertex_groups:
                                copy.vertex_groups.new(name=group.name)
                    copies.append(copy)
            if not copies:
                raise RuntimeError("Nothing to export: the objects have no faces.")
            view_layer.update()

            palette_path, palette_colours, unwrapped = None, 0, []
            if palette:
                palette_path, palette_colours = _apply_palette(copies, asset_name, folder, temp)
            else:
                # Unwrap before joining: a joined mesh would give parts without
                # UVs a degenerate (all-zero) UV map.
                for c in copies:
                    if not c.data.uv_layers:
                        _unwrap(c, view_layer)
                        unwrapped.append(c.name)

            if join and len(copies) > 1:
                _select_only(view_layer, copies)
                bpy.ops.object.join()
                copies = [copies[0]]

            # Pivot: move the geometry so the chosen point sits at the object origin.
            lo = Vector((math.inf,) * 3)
            hi = Vector((-math.inf,) * 3)
            for c in copies:
                for v in c.data.vertices:
                    lo = Vector(map(min, lo, v.co))
                    hi = Vector(map(max, hi, v.co))
            center = (lo + hi) / 2
            pivot_point = {"bottom": Vector((center.x, center.y, lo.z)),
                           "center": center,
                           "origin": Vector((0, 0, 0))}[pivot]
            fix = Matrix.Translation(-pivot_point)
            if engine == "unreal":
                # Unreal's forward is +X; after its importer mirrors Y, Blender's +X
                # stays +X, so turn the model's front (-Y) towards +X.
                fix = Matrix.Rotation(math.pi / 2, 4, "Z") @ fix
            rig_copy = None
            if skeleton:
                rig_copy = temp.obj(skeleton_name, skeleton.data)
                rig_copy.matrix_world = fix @ skeleton.matrix_world
            materials = set()
            for c in copies:
                c.data.transform(fix)
                if triangulate:
                    bm = bmesh.new()
                    bm.from_mesh(c.data)
                    bmesh.ops.triangulate(bm, faces=bm.faces[:])
                    bm.to_mesh(c.data)
                    bm.free()
                c.data.update()
                materials.update(m.name for m in c.data.materials if m)
            tris = sum(triangle_count(c.data) for c in copies)

            export_objs = list(copies)
            bones = 0
            if rig_copy:
                view_layer.update()
                for c in copies:
                    c.parent = rig_copy
                    c.matrix_parent_inverse = rig_copy.matrix_world.inverted()
                    c.modifiers.new("Armature", "ARMATURE").object = rig_copy
                export_objs.append(rig_copy)
                bones = len(rig_copy.data.bones)
            lod_tris = []
            collision_tris = 0
            if join:
                main = copies[0]
                main.name = asset_name
                main.data.name = asset_name
                if collision:
                    hull = _collision_mesh(main.data, _collision_name(engine, asset_name), temp, context)
                    export_objs.append(temp.obj(hull.name, hull))
                    collision_tris = triangle_count(hull)
                if lods:
                    lod_objs = [main]
                    for level in range(1, lods + 1):
                        mesh = _decimated_mesh(main.data, 0.5 ** level, f"{asset_name}_LOD{level}", temp, context)
                        lod_objs.append(temp.obj(mesh.name, mesh))
                    main.name = main.data.name = f"{asset_name}_LOD0"  # Unity builds a LODGroup from these names
                    lod_tris = [triangle_count(o.data) for o in lod_objs]
                    export_objs.extend(lod_objs[1:])
                    if engine == "unreal":
                        # Unreal reads LODs from an FBX LodGroup node.
                        group = temp.obj(asset_name, None)
                        group["fbx_type"] = "LodGroup"
                        for o in lod_objs:
                            o.parent = group
                        export_objs.append(group)
            view_layer.update()
            _select_only(view_layer, export_objs)

            if fmt == "fbx":
                options = dict(
                    filepath=filepath, use_selection=True, object_types={"MESH", "EMPTY", "ARMATURE"},
                    use_mesh_modifiers=False, mesh_smooth_type="FACE",
                    add_leaf_bones=False, bake_anim=False,
                    axis_forward="-Z", axis_up="Y",
                )
                if engine == "unity":
                    # Imports with scale 1 and rotation 0.
                    options.update(apply_scale_options="FBX_SCALE_UNITS", bake_space_transform=True)
                else:
                    options.update(apply_scale_options="FBX_SCALE_NONE")
                bpy.ops.export_scene.fbx(**options)
            else:
                bpy.ops.export_scene.gltf(
                    filepath=filepath, export_format="GLB", use_selection=True, export_yup=True,
                )
            size = hi - lo
            info = {
                "path": filepath, "engine": engine, "format": fmt,
                "bytes": os.path.getsize(filepath),
                "objects": len(copies), "triangles": tris,
                "materials": sorted(materials),
                "size_m": [round(size.x, 4), round(size.y, 4), round(size.z, 4)],
                "pivot": pivot, "auto_unwrapped": unwrapped,
                "lod_triangles": lod_tris, "collision_triangles": collision_tris,
                "palette": palette_path, "palette_colours": palette_colours,
                "skeleton": skeleton_name, "bones": bones,
            }
        finally:
            temp.cleanup()
            for o, name in original_names:
                o.name = name
            if skeleton:
                skeleton.name = skeleton_name
                skeleton.data.pose_position = saved_pose
            for o in view_layer.objects:
                o.select_set(False)
            for o in prev_selected:
                try:
                    o.select_set(True)
                except (ReferenceError, RuntimeError):
                    pass
            try:
                view_layer.objects.active = prev_active
            except ReferenceError:
                pass
    return info


def export_all(folder, **options):
    """Export every asset sub-collection to its own file in `folder`."""
    scene = bpy.context.scene
    assets = scene_tools.asset_names(scene)
    if not assets:
        raise RuntimeError("No asset sub-collections found. Create them with new_asset(name).")
    options.pop("asset", None)
    return [export_model(os.path.join(folder, name), asset=name, **options) for name in assets]


def describe(info):
    size = " x ".join(f"{v:g}" for v in info["size_m"])
    text = (f"Exported {info['path']} ({info['bytes'] / 1024:.0f} KB) for {info['engine']}: "
            f"{info['objects']} object(s), {info['triangles']} triangles, "
            f"materials: {', '.join(info['materials']) or 'none'}; size {size} m; pivot: {info['pivot']}.")
    if info["lod_triangles"]:
        text += " LODs (triangles): " + ", ".join(str(t) for t in info["lod_triangles"]) + "."
    if info["collision_triangles"]:
        text += f" Convex collision: {info['collision_triangles']} triangles."
    if info.get("skeleton"):
        text += f" Skeleton: {info['skeleton']} ({info['bones']} bones), parts skinned to it."
    if info["palette"]:
        text += f" Palette: {info['palette_colours']} colours in {os.path.basename(info['palette'])}."
    if info["auto_unwrapped"]:
        text += f" Auto-unwrapped UVs for: {', '.join(info['auto_unwrapped'])}."
    return text
