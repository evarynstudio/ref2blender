"""Main-thread helpers: run generated code, render check views, encode images."""

import base64
import builtins
import contextlib
import io
import math
import os
import tempfile
import traceback
import types

import bmesh
import bpy
import mathutils
from mathutils import Vector

from . import safety

_SAFE_BUILTINS = safety.safe_builtins()

COLLECTION_NAME = "Ref2Blender"
GEOMETRY_TYPES = {"MESH", "CURVE", "SURFACE", "META", "FONT"}
MAX_OUTPUT_CHARS = 8000
RENDER_SIZE = 768

# name -> (direction from the model towards the camera, orthographic?)
VIEWS = {
    "front": (Vector((0, -1, 0)), True),
    "back": (Vector((0, 1, 0)), True),
    "right": (Vector((1, 0, 0)), True),
    "left": (Vector((-1, 0, 0)), True),
    "top": (Vector((0, 0, 1)), True),
    "bottom": (Vector((0, 0, -1)), True),
    "three_quarter": (Vector((1, -1, 0.6)), False),
    "three_quarter_back": (Vector((-1, 1, 0.6)), False),
}


# ---------------------------------------------------------------- collection

def ensure_collection(scene):
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        col = bpy.data.collections.new(COLLECTION_NAME)
    if not scene.user_of_id(col):
        scene.collection.children.link(col)
    return col


def _find_layer_collection(layer_col, name):
    if layer_col.collection.name == name:
        return layer_col
    for child in layer_col.children:
        found = _find_layer_collection(child, name)
        if found:
            return found
    return None


def activate_collection(context):
    """Make the Ref2Blender collection active so bpy.ops primitives land in it."""
    col = ensure_collection(context.scene)
    layer_col = _find_layer_collection(context.view_layer.layer_collection, col.name)
    if layer_col:
        context.view_layer.active_layer_collection = layer_col
    return col


def clear_collection(scene):
    col = bpy.data.collections.get(COLLECTION_NAME)
    if col is None:
        return 0
    objs = list(col.all_objects)
    for obj in objs:
        bpy.data.objects.remove(obj, do_unlink=True)
    for child in list(col.children_recursive):
        bpy.data.collections.remove(child)
    return len(objs)


def find_asset(name):
    """An asset is a sub-collection of the Ref2Blender collection."""
    root = bpy.data.collections.get(COLLECTION_NAME)
    if root is None:
        return None
    return next((c for c in root.children_recursive if c.name == name), None)


def asset_names(scene):
    root = bpy.data.collections.get(COLLECTION_NAME)
    if root is None:
        return []
    return [c.name for c in root.children
            if any(o.type in GEOMETRY_TYPES for o in c.all_objects)]


def target_objects(scene, asset=None):
    if asset:
        col = find_asset(asset)
        if col is None:
            raise ValueError(f'No asset named "{asset}". Assets: {asset_names(scene) or "none"}')
    else:
        col = bpy.data.collections.get(COLLECTION_NAME)
    objs = [o for o in col.all_objects if o.type in GEOMETRY_TYPES] if col else []
    return [o for o in objs if not o.hide_render]


def _new_asset(name):
    """Create (or reuse) an asset sub-collection and make it the active collection."""
    context = bpy.context
    root = ensure_collection(context.scene)
    col = find_asset(name)
    if col is None:
        col = bpy.data.collections.new(name)
        root.children.link(col)
    layer_col = _find_layer_collection(context.view_layer.layer_collection, col.name)
    if layer_col:
        context.view_layer.active_layer_collection = layer_col
    return col


# ------------------------------------------------------------- code execution

def _view3d_override():
    wm = bpy.context.window_manager
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                for region in area.regions:
                    if region.type == "WINDOW":
                        return {"window": win, "screen": win.screen, "area": area, "region": region}
    return {}


def _make_material(name, color, metallic=0.0, roughness=0.5):
    """Create (or reuse) a material whose viewport colour matches its base colour."""
    rgba = tuple(color) if len(color) == 4 else (*color, 1.0)
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    mat.diffuse_color = rgba
    mat.metallic = metallic
    mat.roughness = roughness
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = rgba
        bsdf.inputs["Metallic"].default_value = metallic
        bsdf.inputs["Roughness"].default_value = roughness
    return mat


def new_namespace():
    return {
        "__name__": "__main__",
        "bpy": bpy,
        "bmesh": bmesh,
        "mathutils": mathutils,
        "math": math,
        "Vector": Vector,
        "COLLECTION_NAME": COLLECTION_NAME,
        "make_material": _make_material,
        "new_asset": _new_asset,
    }


def safe_mode_enabled():
    addon = bpy.context.preferences.addons.get(__package__)
    return getattr(addon.preferences, "safe_mode", True) if addon else True


def run_code(code, namespace, safe=None):
    """Execute generated code on the main thread. Returns (ok, output_text).
    `safe` defaults to the add-on preference (on unless the user turned it off)."""
    if safe is None:
        safe = safe_mode_enabled()
    if safe:
        try:
            safety.check(code)
        except safety.Blocked as exc:
            return False, safety.MESSAGE.format(reason=exc)
        namespace["__builtins__"] = _SAFE_BUILTINS
        # Modules imported while safe mode was off must not stay reachable.
        for key, value in list(namespace.items()):
            if (isinstance(value, types.ModuleType)
                    and value.__name__.split(".")[0] not in safety.ALLOWED_MODULES):
                del namespace[key]
    else:
        namespace["__builtins__"] = builtins
    buf = io.StringIO()
    ok = True
    with bpy.context.temp_override(**_view3d_override()):
        context = bpy.context
        try:
            if context.object and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        namespace["collection"] = activate_collection(context)
        try:
            with contextlib.redirect_stdout(buf):
                exec(compile(code, "<ref2blender>", "exec"), namespace)
        except safety.Blocked as exc:
            ok = False
            buf.write("\n" + safety.MESSAGE.format(reason=exc))
        except (Exception, SystemExit):
            ok = False
            buf.write("\n" + traceback.format_exc())
        try:
            if context.object and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        context.view_layer.update()
        try:
            bpy.ops.ed.undo_push(message="Ref2Blender")
        except Exception:
            pass

    out = buf.getvalue().strip()
    if len(out) > MAX_OUTPUT_CHARS:
        dropped = len(out) - MAX_OUTPUT_CHARS
        out = f"[... {dropped} earlier characters of output omitted ...]\n" + out[-MAX_OUTPUT_CHARS:]
    return ok, out


def scene_summary(scene):
    objs = target_objects(scene)
    if not objs:
        return f'Collection "{COLLECTION_NAME}" contains no geometry yet.'
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lines = [f'Objects in collection "{COLLECTION_NAME}" ({len(objs)}):']
    total_tris = 0
    for obj in objs[80:]:
        if obj.type == "MESH":
            total_tris += sum(len(p.vertices) - 2 for p in obj.evaluated_get(depsgraph).data.polygons)
    for obj in objs[:80]:
        d, l = obj.dimensions, obj.location
        owner = obj.users_collection[0].name if obj.users_collection else COLLECTION_NAME
        tag = "" if owner == COLLECTION_NAME else f" (asset {owner})"
        info = f"- {obj.name}{tag} [{obj.type}] loc=({l.x:.3f}, {l.y:.3f}, {l.z:.3f}) dims=({d.x:.3f}, {d.y:.3f}, {d.z:.3f})"
        if obj.type == "MESH":
            ev = obj.evaluated_get(depsgraph)
            tris = sum(len(p.vertices) - 2 for p in ev.data.polygons)
            total_tris += tris
            info += f" tris={tris} (with modifiers)"
        lines.append(info)
    if len(objs) > 80:
        lines.append(f"... and {len(objs) - 80} more objects")
    lines.append(f"Total triangles: {total_tris}")
    lo, hi = _bounds(objs)
    lines.append(
        f"Overall bounding box: min=({lo.x:.3f}, {lo.y:.3f}, {lo.z:.3f}) max=({hi.x:.3f}, {hi.y:.3f}, {hi.z:.3f})"
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ rendering

def _bounds(objs):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    for obj in objs:
        ev = obj.evaluated_get(depsgraph)
        for corner in ev.bound_box:
            p = ev.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


class _Restore:
    """Remember attribute values and put them back on exit."""

    def __init__(self):
        self._saved = []

    def set(self, owner, attr, value):
        if not hasattr(owner, attr):
            return
        self._saved.append((owner, attr, getattr(owner, attr)))
        try:
            setattr(owner, attr, value)
        except Exception:
            self._saved.pop()

    def restore(self):
        for owner, attr, value in reversed(self._saved):
            try:
                setattr(owner, attr, value)
            except Exception:
                pass


def render_views(views, size=RENDER_SIZE, asset=None):
    """Render the generated objects (or one asset). Returns a list of (view_name, png_bytes)."""
    scene = bpy.context.scene
    bpy.context.view_layer.update()
    objs = target_objects(scene, asset)
    if not objs:
        raise RuntimeError("Nothing to render: no geometry found.")

    lo, hi = _bounds(objs)
    center = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-4)

    cam_data = bpy.data.cameras.new("R2B_TempCam")
    cam = bpy.data.objects.new("R2B_TempCam", cam_data)
    scene.collection.objects.link(cam)

    keep = _Restore()
    render = scene.render
    shading = scene.display.shading
    keep.set(scene, "camera", cam)
    keep.set(render, "engine", "BLENDER_WORKBENCH")
    keep.set(render, "resolution_x", size)
    keep.set(render, "resolution_y", size)
    keep.set(render, "resolution_percentage", 100)
    keep.set(render, "film_transparent", False)
    keep.set(render, "use_file_extension", True)
    keep.set(render.image_settings, "media_type", "IMAGE")
    keep.set(render.image_settings, "file_format", "PNG")
    keep.set(render.image_settings, "color_mode", "RGB")
    keep.set(shading, "light", "STUDIO")
    keep.set(shading, "color_type", "MATERIAL")
    keep.set(shading, "show_object_outline", True)
    keep.set(shading, "show_cavity", False)
    keep.set(shading, "show_shadows", False)
    # Show only the generated model, not whatever else is in the user's scene.
    wanted = set(objs)
    for other in scene.objects:
        if other not in wanted and other is not cam:
            keep.set(other, "hide_render", True)
    old_path = render.filepath

    results = []
    tmpdir = tempfile.mkdtemp(prefix="ref2blender_")
    try:
        for name in views:
            direction, ortho = VIEWS[name]
            direction = direction.normalized()
            if ortho:
                cam_data.type = "ORTHO"
                cam_data.ortho_scale = radius * 2 * 1.1
                dist = radius * 3 + 1
            else:
                cam_data.type = "PERSP"
                cam_data.lens = 50
                half_fov = cam_data.angle / 2
                dist = radius / math.sin(half_fov) * 1.1
            cam_data.clip_start = max(dist - radius * 1.5, 0.001)
            cam_data.clip_end = dist + radius * 1.5
            cam.location = center + direction * dist
            cam.rotation_euler = direction.to_track_quat("Z", "Y").to_euler()

            path = os.path.join(tmpdir, f"{name}.png")
            render.filepath = path
            bpy.ops.render.render(write_still=True)
            with open(path, "rb") as f:
                results.append((name, f.read()))
            os.remove(path)
    finally:
        render.filepath = old_path
        keep.restore()
        bpy.data.objects.remove(cam, do_unlink=True)
        bpy.data.cameras.remove(cam_data)
        with contextlib.suppress(OSError):
            os.rmdir(tmpdir)
    return results


# --------------------------------------------------------------------- images

_MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif"}
_MAX_RAW_BYTES = 3_500_000
_MAX_SIDE = 2000


def encode_reference_image(path):
    """Return (media_type, base64_data). Re-encodes through Blender when the file
    is too large or in a format the API does not accept."""
    path = bpy.path.abspath(path)
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    ext = os.path.splitext(path)[1].lower()

    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        if w == 0 or h == 0:
            raise ValueError(f"No s'ha pogut llegir la imatge: {path}")
        fits = ext in _MEDIA_TYPES and os.path.getsize(path) <= _MAX_RAW_BYTES and max(w, h) <= _MAX_SIDE
        if fits:
            with open(path, "rb") as f:
                return _MEDIA_TYPES[ext], base64.standard_b64encode(f.read()).decode("ascii")

        scale = min(1.0, _MAX_SIDE / max(w, h))
        if scale < 1.0:
            img.scale(max(1, round(w * scale)), max(1, round(h * scale)))
        fd, tmp = tempfile.mkstemp(suffix=".jpg", prefix="ref2blender_")
        os.close(fd)
        try:
            img.file_format = "JPEG"
            img.filepath_raw = tmp
            img.save()
            with open(tmp, "rb") as f:
                return "image/jpeg", base64.standard_b64encode(f.read()).decode("ascii")
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
    finally:
        bpy.data.images.remove(img)
