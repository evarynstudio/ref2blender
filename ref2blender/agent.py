"""The modelling agent: Claude plans, writes Blender Python, looks at renders, fixes.

API requests run in a worker thread; everything that touches bpy runs on the
main thread from a bpy.app.timers callback.
"""

import base64
import os
import textwrap
import threading
import traceback

import bpy

from . import build_tools, compare_tools, i18n, scene_tools, script_store
from .i18n import tr

MAX_TOKENS = 64000
FALLBACK_BETA = "server-side-fallback-2026-07-01"

SYSTEM_PROMPT = """\
You are a 3D modeller working inside a live Blender {version} session. The user gives you \
reference images (and maybe a short description) and wants a 3D model of what they show, \
built directly in their scene. You cannot see the viewport; your only eyes are the \
render_views tool, so use it.

How to work
1. Study the references first. Work out which view each one shows, the overall proportions \
(height : width : depth), the main volumes, and which details matter. If only one view is \
given, infer the hidden sides sensibly.
2. Build with execute_blender_python, in a few steps rather than one giant script: main \
volumes first, then secondary shapes, then details and materials. Errors come back to you as \
tracebacks, so just fix and rerun.
3. After the main volumes exist, and again after detailing, call render_views and compare \
the renders against the references side by side: silhouette, proportions, placement of \
parts, colours. Fix what does not match. Two or three review rounds is normal; stop when the \
model reads clearly as the reference, not when it is merely valid geometry.
4. Finish with a short summary for the user (what you built, what is approximate), written \
in the language of their description, or {language} if they wrote none.

Getting it right without piecemeal fixes
- Measure before building. From each reference, read the proportions as fractions of the \
overall length and height: where the highest point is, where each wheel, leg or handle sits, \
how tall and wide each main volume is. Write them as one table of named numbers at the top of \
the script; every part takes its position and size from that table.
- One script builds the whole model. It starts by removing what it created before and rebuilds \
everything. To correct the model, change numbers in the table and run the script again; do not \
move or rescale objects one by one. Parts that touch each other must be derived from the same \
numbers, so they move together and never end up buried or floating.
- Silhouette before details. Name the three to five features that make the subject \
recognisable (a roofline, a stance, the shape of a handle) and get those right in the main \
volumes. Add no details until the side and front outlines match the references.
- Compare like with like. For each reference photo, estimate where the camera stood and call compare_reference with that camera: it lays the model's outline over the reference and, when the reference has a plain background, scores the overlap and shows what is missing or extra. Adjust the camera until the overlay sits right, then correct the measures, largest difference first. Use the fixed orthographic views for drawings and blueprints, and render_views for a plain look at the model.
- Before you finish: no part hidden inside another, none floating; round parts that stand on \
the ground use a segment count divisible by 4 so the lowest point is exactly Z=0; left and \
right parts come from the same numbers, mirrored.
- Tell the user which sides you had no reference for and therefore inferred.

Scene conventions
- Z is up, units are metres. The model's front faces -Y, so the "front" render (camera on \
-Y looking towards +Y) should match a front reference; its right side is +X. Rest the model \
on the ground plane (lowest point at Z=0), centred on the origin. Use a realistic size unless \
the user gives one.
- Everything you create belongs in the collection "{collection}". It is already the active \
collection, and the variable `collection` points to it, so bpy.ops primitives land there; \
for objects made through bpy.data use collection.objects.link(obj). Leave everything outside \
that collection untouched: the user may have their own work in the scene.
- The Python namespace persists between calls (bpy, bmesh, mathutils, math, Vector are \
preloaded), so you can define helper functions once and reuse them.
- Renders use the Workbench engine, which shows each material's viewport colour. The helper \
make_material(name, (r, g, b), metallic=0.0, roughness=0.5) creates a material with both the \
Principled base colour and the viewport colour set; assign it with obj.data.materials.append(mat).

Modelling advice
- Choose the technique that fits the shape: explicit vertex coordinates and faces \
(mesh.from_pydata or bmesh) for profiles and custom silhouettes, primitives for simple \
volumes, and modifiers for the rest (Mirror for symmetric objects, Screw for lathe shapes, \
Solidify for thin shells, Bevel for real-world edges, Subdivision Surface for organic forms). \
Curves with bevel depth work well for pipes, cables and handles.
- Prefer the data API and bmesh over bpy.ops where practical; operators depend on selection \
and mode and fail in surprising ways.
- Give objects meaningful names, apply smooth shading where the surface is curved, and keep \
separate parts as separate objects so the user can edit them.
- The model is a game asset. Keep the mesh efficient: no faces hidden inside other parts, \
no subdivision beyond what the silhouette needs, bevels with few segments. Each tool result \
reports the triangle count; if the user gives a triangle budget, stay within it and check it \
before you finish. Use few materials (each is a draw call) and reuse them across parts.
- If the user asks for several separate assets (a set of props, variants), build each one in \
its own sub-collection: call new_asset("Name") first, which creates it and makes it active, \
and space the assets apart along X so they do not overlap. Share materials and proportions \
across the set so it looks like one family.
- Only use what Blender's Python already provides. Do not read or write files, start \
processes or use the network: the code runs on the user's machine and only needs to build \
geometry.
"""

TOOLS = [
    {
        "name": "execute_blender_python",
        "description": (
            "Run Python code in the user's Blender session (main thread, Object Mode). "
            "Returns anything printed to stdout, the traceback if it raised, and a summary "
            "of the objects currently in the Ref2Blender collection with their dimensions. "
            "The namespace persists between calls."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "Python source to execute."},
            },
            "required": ["code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "render_views",
        "description": (
            "Render the Ref2Blender collection, framed automatically, and return one image "
            "per requested view. front/back/left/right/top/bottom are orthographic (front = "
            "camera on -Y looking towards +Y; right = camera on +X); three_quarter is a "
            "perspective view from the front-right and above, three_quarter_back from the "
            "back-left and above. Use it to compare your model against the references. "
            "cameras adds free cameras, rendered as camera_1, camera_2...: each is an object with azimuth (degrees round the model: 0 looks at its front, 90 at its right side, 180 at its back, -90 at its left), elevation (degrees above the horizon), lens (focal length in mm, 50 by default, 0 for orthographic) and aspect (image width / height). Use one to look at the model from the angle a reference photo was taken from."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "views": {
                    "type": "array",
                    "items": {"type": "string", "enum": list(scene_tools.VIEWS)},
                },
                "cameras": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "azimuth": {"type": "number"},
                            "elevation": {"type": "number"},
                            "lens": {"type": "number"},
                            "aspect": {"type": "number"},
                        },
                        "additionalProperties": False,
                    },
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "compare_reference",
        "description": (
            "Lay the model's outline over one of the reference images and measure the match. "
            "Give the reference's number (1 for the first image) and either a fixed view or a "
            "camera placed where the picture was taken from (azimuth, elevation, lens; the "
            "picture's own aspect is used). Returns an overlay (magenta = the model's outline) "
            "and, when the reference has a plain or transparent background, the overlap of the "
            "two outlines as a percentage, where the model is missing or extra, and a "
            "difference image. If the subject cannot be told from the background, pass "
            "subject_box = [left, top, right, bottom] as fractions of the image from its "
            "top-left corner; you then get the overlay only."
        ),
        "eager_input_streaming": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "integer", "description": "Number of the reference image, from 1."},
                "view": {"type": "string", "enum": list(scene_tools.VIEWS)},
                "camera": {
                    "type": "object",
                    "properties": {
                        "azimuth": {"type": "number"},
                        "elevation": {"type": "number"},
                        "lens": {"type": "number"},
                    },
                    "additionalProperties": False,
                },
                "subject_box": {"type": "array", "items": {"type": "number"}},
            },
            "required": ["reference"],
            "additionalProperties": False,
        },
    },
]

VIEW_LABELS = {
    "AUTO": "view not specified",
    "FRONT": "front view",
    "BACK": "back view",
    "LEFT": "left side view",
    "RIGHT": "right side view",
    "TOP": "top view",
    "BOTTOM": "bottom view",
    "PERSP": "perspective / three-quarter view",
}


class Session:
    def __init__(self):
        self.messages = []
        self.references = []
        self.namespace = None
        self.client = None
        self.request = {}
        self.running = False
        self.status = ""
        self.log = []
        self.steps = 0
        self.max_steps = 25
        self.tokens_in = 0
        self.tokens_out = 0
        self.generation = 0  # bumped on cancel so stale worker results are ignored
        self.result = None
        self.stream = None

    def add_log(self, text):
        for line in str(text).splitlines():
            line = line.strip()
            if not line:
                continue
            self.log.extend(textwrap.wrap(line, 58) or [line])
        del self.log[:-200]
        print("[Ref2Blender]", text)


SESSION = Session()


# ----------------------------------------------------------------- public API

STYLE_NOTES = {
    "FREE": "",
    "LOWPOLY": "Style: low poly with flat shading and flat colours, like a stylised mobile/indie game asset. "
               "Simplify shapes to their essential planes; no bevels or subdivision.",
    "STYLIZED": "Style: stylised, with smooth simplified forms, slightly exaggerated proportions and clean colours.",
    "REALISTIC": "Style: realistic proportions and detail, with bevelled edges and smooth shading where appropriate.",
}


def build_user_content(images, description, style="FREE", tri_budget=0):
    """images: list of (path, view_id). Runs on the main thread."""
    content = []
    for i, (path, view) in enumerate(images, 1):
        media_type, data = scene_tools.encode_reference_image(path)
        label = f"Reference image {i} of {len(images)}: {VIEW_LABELS.get(view, VIEW_LABELS['AUTO'])} ({os.path.basename(path)})"
        content.append({"type": "text", "text": label})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
    text = "Build a 3D model of what these reference images show."
    if description.strip():
        text += f"\n\nUser's description / instructions:\n{description.strip()}"
    if STYLE_NOTES.get(style):
        text += "\n\n" + STYLE_NOTES[style]
    if tri_budget:
        text += f"\n\nTriangle budget: at most {tri_budget} triangles in total."
    content.append({"type": "text", "text": text})
    return content


def start(client, model, effort, max_steps, user_content, new_conversation, references=None):
    s = SESSION
    if s.running:
        raise RuntimeError(tr("A generation is already running."))
    if new_conversation or s.namespace is None:
        s.messages = []
        s.references = list(references or [])
        s.namespace = scene_tools.new_namespace()
        s.log = []
        s.tokens_in = s.tokens_out = 0
    s.client = client
    s.request = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "betas": [FALLBACK_BETA],
        "fallbacks": "default",
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "cache_control": {"type": "ephemeral"},
        "system": SYSTEM_PROMPT.format(
            version=bpy.app.version_string, collection=scene_tools.COLLECTION_NAME,
            language=i18n.SUMMARY_LANGUAGE[i18n.language()],
        ) + "\n" + build_tools.DOC,
        "tools": TOOLS,
    }
    s.messages.append({"role": "user", "content": user_content})
    s.max_steps = max_steps
    s.steps = 0
    s.running = True
    s.result = None
    _begin_request()
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.25)


def cancel():
    s = SESSION
    if not s.running:
        return
    s.generation += 1
    stream, s.stream = s.stream, None
    if stream is not None:
        try:
            stream.close()
        except Exception:
            pass
    _finish(tr("Cancelled"))


def has_conversation():
    return bool(SESSION.messages)


# --------------------------------------------------------------- worker thread

def _describe_error(exc):
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return tr("Invalid API key. Check it in the add-on preferences.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return tr("The API key has no permission for this model.")
    if isinstance(exc, anthropic.NotFoundError):
        return tr("Model not found. Choose another one in the preferences.")
    if isinstance(exc, anthropic.RateLimitError):
        return tr("Rate limit reached. Wait a moment and press Refine to continue.")
    if isinstance(exc, anthropic.BadRequestError):
        return tr("Request rejected by the API: {error}", error=exc.message)
    if isinstance(exc, anthropic.APIStatusError):
        return tr("API error ({code}): {error}", code=exc.status_code, error=exc.message)
    if isinstance(exc, anthropic.APIConnectionError):
        return tr("Network error. Check your Internet connection.")
    if isinstance(exc, TypeError) and "authentication" in str(exc):
        return tr("API key missing: set it in the add-on preferences.")
    return f"{type(exc).__name__}: {exc}"


def _worker(session, generation, kwargs):
    try:
        with session.client.beta.messages.stream(**kwargs) as stream:
            session.stream = stream
            message = stream.get_final_message()
        session.result = (generation, "ok", message)
    except Exception as exc:  # described and reported from the main thread
        traceback.print_exc()
        session.result = (generation, "error", exc)
    finally:
        session.stream = None


def _begin_request():
    s = SESSION
    s.steps += 1
    s.status = tr("Step {step}/{total}: Claude is thinking…", step=s.steps, total=s.max_steps)
    kwargs = dict(s.request, messages=list(s.messages))
    threading.Thread(target=_worker, args=(s, s.generation, kwargs), daemon=True).start()


# ----------------------------------------------------------------- main thread

def _finish(status):
    s = SESSION
    s.running = False
    s.status = status
    s.add_log(status)
    _redraw()


def _redraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for win in wm.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def _tick():
    s = SESSION
    if not s.running:
        return None
    if s.result is None:
        return 0.25
    generation, kind, payload = s.result
    s.result = None
    if generation != s.generation:
        return 0.25
    try:
        if kind == "error":
            _finish(tr("Error: {error}", error=_describe_error(payload)))
        else:
            _handle_response(payload)
    except Exception as exc:
        traceback.print_exc()
        _finish(tr("Internal error: {error}", error=exc))
    _redraw()
    return 0.25 if s.running else None


def _handle_response(message):
    s = SESSION
    usage = message.usage
    s.tokens_in += (
        (usage.input_tokens or 0)
        + (getattr(usage, "cache_read_input_tokens", 0) or 0)
        + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    )
    s.tokens_out += usage.output_tokens or 0

    for block in message.content:
        if block.type == "text" and block.text.strip():
            s.add_log(block.text)

    if message.stop_reason == "refusal":
        _finish(tr("The model declined the request."))
        return

    if message.content:
        # Keep the full content (thinking and fallback blocks included) so the
        # next request replays the turn exactly as the API produced it.
        s.messages.append({"role": "assistant", "content": message.content})

    tool_uses = [b for b in message.content if b.type == "tool_use"]
    if not tool_uses:
        if message.stop_reason == "max_tokens":
            _finish(tr("Response cut off by the token limit. Press Refine to continue."))
        else:
            _finish(tr("Done ✔"))
        return

    truncated = message.stop_reason == "max_tokens"
    results = []
    for i, block in enumerate(tool_uses):
        cut_off = truncated and i == len(tool_uses) - 1
        results.append(_run_tool(block, cut_off))
    s.messages.append({"role": "user", "content": results})

    if s.steps >= s.max_steps:
        _finish(tr("Step limit reached. Press Refine to continue."))
    else:
        _begin_request()


def _tool_error(block, text):
    return {"type": "tool_result", "tool_use_id": block.id, "content": text, "is_error": True}


def _run_tool(block, cut_off):
    """Validate the tool input ourselves (eager input streaming skips server-side
    validation) and run it."""
    s = SESSION
    args = block.input if isinstance(block.input, dict) else {}
    if cut_off:
        return _tool_error(
            block, "This tool call was cut off by the output limit and was not run. "
            "Send it again, split into smaller steps."
        )

    if block.name == "execute_blender_python":
        code = args.get("code")
        if not isinstance(code, str) or not code.strip():
            return _tool_error(block, "Invalid input: 'code' must be a non-empty string.")
        s.status = tr("Step {step}/{total}: running code in Blender…", step=s.steps, total=s.max_steps)
        before = script_store.snapshot()
        ok, output = scene_tools.run_code(code, s.namespace)
        if ok:
            script_store.record(code, before)
        s.add_log(tr("▶ code executed") if ok else tr("✖ error in the code (Claude will fix it)"))
        text = (output or "(no output)") + "\n\n" + scene_tools.scene_summary(bpy.context.scene)
        result = {"type": "tool_result", "tool_use_id": block.id, "content": text}
        if not ok:
            result["is_error"] = True
        return result

    if block.name == "render_views":
        views = args.get("views") or []
        if not isinstance(views, list) or any(v not in scene_tools.VIEWS for v in views):
            return _tool_error(
                block, f"Invalid input: 'views' must be a list drawn from {list(scene_tools.VIEWS)}."
            )
        try:
            cameras = scene_tools.check_cameras(args.get("cameras"))
        except ValueError as exc:
            return _tool_error(block, f"Invalid input: {exc}")
        if not views and not cameras:
            return _tool_error(block, "Invalid input: give at least one view or one camera.")
        views = list(dict.fromkeys(views))
        s.status = tr("Step {step}/{total}: rendering views…", step=s.steps, total=s.max_steps)
        try:
            renders = scene_tools.render_views(views, cameras=cameras)
        except Exception as exc:
            traceback.print_exc()
            return _tool_error(block, f"Render failed: {exc}")
        s.add_log(tr("📷 views: {views}", views=", ".join(name for name, _ in renders)))
        content = []
        for name, png in renders:
            content.append({"type": "text", "text": f"Render: {name}"})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode("ascii"),
                },
            })
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}

    if block.name == "compare_reference":
        index = args.get("reference")
        if isinstance(index, bool) or not isinstance(index, int) or not 1 <= index <= len(s.references):
            return _tool_error(
                block, f"Invalid input: 'reference' must be a number from 1 to {len(s.references)}.")
        s.status = tr("Step {step}/{total}: comparing with the reference…", step=s.steps, total=s.max_steps)
        try:
            result = compare_tools.compare_reference(
                s.references[index - 1], args.get("view"), args.get("camera"), args.get("subject_box"))
        except (ValueError, RuntimeError) as exc:
            return _tool_error(block, str(exc).replace("'box'", "'subject_box'"))
        except Exception as exc:
            traceback.print_exc()
            return _tool_error(block, f"Comparison failed: {exc}")
        if result["iou"] is None:
            s.add_log(tr("🔍 comparison with reference {index}", index=index))
        else:
            s.add_log(tr("🔍 comparison with reference {index}: {score}% overlap",
                         index=index, score=round(result["iou"] * 100)))
        content = [{"type": "text", "text": result["text"]}]
        for name, png in result["images"]:
            content.append({"type": "text", "text": f"Image: {name}"})
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.standard_b64encode(png).decode("ascii"),
                },
            })
        return {"type": "tool_result", "tool_use_id": block.id, "content": content}

    return _tool_error(block, f"Unknown tool: {block.name}")
