"""Ref2Blender: build game-ready 3D models from reference images with an AI assistant."""

bl_info = {
    "name": "Ref2Blender",
    "author": "Ref2Blender contributors",
    "version": (1, 2, 0),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar (N) > Ref2Blender",
    "description": "Build game-ready 3D models from reference images with an AI assistant",
    "category": "3D View",
}

import importlib
import importlib.util
import os
import subprocess
import sys

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import AddonPreferences, Operator, Panel, PropertyGroup, UIList

# The Anthropic SDK (only needed for the API-key mode) is installed into a private
# folder so Blender's own Python packages are left alone. Appended, not prepended,
# so Blender's modules win.
VENDOR_DIR = bpy.utils.user_resource("SCRIPTS", path="ref2blender_vendor", create=True)
if VENDOR_DIR not in sys.path:
    sys.path.append(VENDOR_DIR)

from . import agent, bridge, export_tools, i18n, scene_tools, script_store  # noqa: E402
from .i18n import tr  # noqa: E402


def sdk_available():
    return importlib.util.find_spec("anthropic") is not None


def get_prefs(context):
    return context.preferences.addons[__package__].preferences


def api_key_available(context):
    """A key in the preferences or in the ANTHROPIC_API_KEY environment variable."""
    return bool(get_prefs(context).api_key.strip() or os.environ.get("ANTHROPIC_API_KEY", "").strip())


def _api_mode_ready(cls, context):
    """Shared poll of the buttons that call the Claude API: say why they are greyed out."""
    if not sdk_available():
        cls.poll_message_set(tr("The Claude library is missing: install it from the panel."))
        return False
    if not api_key_available(context):
        cls.poll_message_set(tr("This button needs an Anthropic API key. With MCP, ask your AI assistant instead."))
        return False
    return True


VIEW_ITEMS = [
    ("AUTO", "Auto", "Let the AI work out which view this is"),
    ("FRONT", "Front", ""),
    ("BACK", "Back", ""),
    ("LEFT", "Left", ""),
    ("RIGHT", "Right", ""),
    ("TOP", "Top", ""),
    ("BOTTOM", "Bottom", ""),
    ("PERSP", "Perspective", ""),
]

_NAME_HINTS = [
    ("FRONT", ("front", "davant", "frontal")),
    ("BACK", ("back", "darrere", "posterior", "rear")),
    ("LEFT", ("left", "esquerr")),
    ("RIGHT", ("right", "dreta")),
    ("TOP", ("top", "superior", "dalt")),
    ("BOTTOM", ("bottom", "inferior", "sota")),
    ("PERSP", ("persp", "34", "3-4")),
]


def guess_view(filename):
    name = os.path.splitext(filename)[0].lower()
    for view, hints in _NAME_HINTS:
        if any(h in name for h in hints):
            return view
    if "side" in name or "lateral" in name:
        return "RIGHT"
    return "AUTO"


# ------------------------------------------------------------------ properties

class R2B_RefImage(PropertyGroup):
    path: StringProperty(name="File", subtype="FILE_PATH")
    view: EnumProperty(name="View", items=VIEW_ITEMS, default="AUTO")


class R2B_Param(PropertyGroup):
    """One measure of the model script (the name is the variable's)."""
    value: FloatProperty(name="Value", precision=3, step=1)
    original: FloatProperty()
    is_int: BoolProperty()
    script: StringProperty()


class R2B_Settings(PropertyGroup):
    images: CollectionProperty(type=R2B_RefImage)
    params: CollectionProperty(type=R2B_Param)
    active_image: IntProperty()
    description: StringProperty(
        name="Description",
        description="Optional: what it is, dimensions, level of detail",
    )
    feedback: StringProperty(
        name="Changes",
        description="What the AI should change or improve in the current model",
    )
    style: EnumProperty(
        name="Style",
        items=[
            ("FREE", "Free", "The AI chooses from the images"),
            ("LOWPOLY", "Low poly", "Flat faces and flat colours"),
            ("STYLIZED", "Stylised", "Smooth, simplified shapes"),
            ("REALISTIC", "Realistic", "Real proportions and detail"),
        ],
        default="FREE",
    )
    tri_budget: IntProperty(
        name="Max triangles",
        description="Triangle budget for the model (0 = no limit)",
        default=0, min=0, soft_max=50000, step=500,
    )
    export_engine: EnumProperty(
        name="Engine",
        items=[
            ("unity", "Unity", "FBX with scale 1 and no rotation"),
            ("unreal", "Unreal Engine", "FBX with the front towards +X"),
            ("godot", "Godot", "GLB"),
            ("generic", "Generic (GLB)", "Binary glTF for other engines"),
        ],
        default="unity",
    )
    export_join: BoolProperty(
        name="Join into a single mesh",
        description="Export all parts as one object",
        default=True,
    )
    export_lods: IntProperty(
        name="LOD levels",
        description="Extra versions, each with half the triangles (0 = none)",
        default=0, min=0, max=export_tools.MAX_LODS,
    )
    export_collision: BoolProperty(
        name="Convex collision",
        description="Add a simplified collision mesh",
        default=False,
    )
    export_palette: BoolProperty(
        name="Palette (single material)",
        description="Turn every colour into one swatch texture and a single material",
        default=False,
    )
    export_each_asset: BoolProperty(
        name="One file per asset",
        description="Export each sub-collection to its own file in the chosen folder",
        default=False,
    )
    replace_previous: BoolProperty(
        name="Delete the previous model",
        description="Empty the Ref2Blender collection before generating",
        default=True,
    )
    max_steps: IntProperty(
        name="Max steps",
        description="Maximum number of AI turns (each turn runs code or looks at renders)",
        default=25, min=3, max=80,
    )


class R2B_Preferences(AddonPreferences):
    bl_idname = __package__

    language: EnumProperty(name="Language", items=i18n.LANGUAGES, default="AUTO")
    safe_mode: BoolProperty(
        name="Safe mode (recommended)",
        description=(
            "Refuse generated code that touches files, processes or the network. "
            "A guard rail, not a full sandbox"
        ),
        default=True,
    )
    api_key: StringProperty(
        name="Anthropic API key",
        description="If empty, the ANTHROPIC_API_KEY environment variable is used",
        subtype="PASSWORD",
    )
    model: EnumProperty(
        name="Model",
        items=[
            ("claude-opus-5-5", "Claude Opus 5.5", "Recommended"),
            ("claude-fable-5-2", "Claude Fable 5.2", "Most capable, more expensive"),
            ("claude-sonnet-5-5", "Claude Sonnet 5.5", "Faster and cheaper"),
        ],
        default="claude-opus-5-5",
    )
    effort: EnumProperty(
        name="Effort",
        description="How much the model reasons at each step: more effort is better but slower and costlier",
        items=[("low", "Low", ""), ("medium", "Medium", ""), ("high", "High", ""), ("xhigh", "Very high", "")],
        default="high",
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "language", text=tr("Language"))
        layout.prop(self, "safe_mode", text=tr("Safe mode (recommended)"))
        layout.label(text=tr("Safe mode blocks file, process and network access in generated code."), icon="INFO")
        layout.separator()
        if not sdk_available():
            layout.operator("r2b.install_deps", text=tr("Install the Claude library"), icon="IMPORT")
        layout.prop(self, "api_key", text=tr("Anthropic API key"))
        layout.prop(self, "model", text=tr("Model"))
        layout.prop(self, "effort", text=tr("Effort"))
        layout.label(text=tr("Get a key at console.anthropic.com"), icon="INFO")


# ------------------------------------------------------------------- operators

class R2B_OT_install_deps(Operator):
    bl_idname = "r2b.install_deps"
    bl_label = "Install the Claude library"
    bl_description = "Download the 'anthropic' package from PyPI into a private folder of the add-on"

    def execute(self, context):
        py = sys.executable
        if context.window:
            context.window.cursor_set("WAIT")
        try:
            subprocess.run([py, "-m", "ensurepip"], capture_output=True)
            proc = subprocess.run(
                [py, "-m", "pip", "install", "--upgrade", "--target", VENDOR_DIR, "anthropic"],
                capture_output=True, text=True,
            )
        finally:
            if context.window:
                context.window.cursor_set("DEFAULT")
        if proc.returncode != 0:
            print(proc.stdout, proc.stderr)
            self.report({"ERROR"}, tr("Installation failed. See the system console."))
            return {"CANCELLED"}
        importlib.invalidate_caches()
        self.report({"INFO"}, tr("Library installed."))
        return {"FINISHED"}


class R2B_OT_add_images(Operator):
    bl_idname = "r2b.add_images"
    bl_label = "Add images"
    bl_description = "Choose one or more reference images"

    files: CollectionProperty(type=bpy.types.OperatorFileListElement, options={"HIDDEN", "SKIP_SAVE"})
    directory: StringProperty(subtype="DIR_PATH", options={"HIDDEN"})
    filter_image: BoolProperty(default=True, options={"HIDDEN"})
    filter_folder: BoolProperty(default=True, options={"HIDDEN"})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        settings = context.scene.r2b
        for f in self.files:
            if not f.name:
                continue
            item = settings.images.add()
            item.name = f.name
            item.path = os.path.join(self.directory, f.name)
            item.view = guess_view(f.name)
        settings.active_image = len(settings.images) - 1
        return {"FINISHED"}


class R2B_OT_remove_image(Operator):
    bl_idname = "r2b.remove_image"
    bl_label = "Remove image"

    @classmethod
    def poll(cls, context):
        return len(context.scene.r2b.images) > 0

    def execute(self, context):
        settings = context.scene.r2b
        settings.images.remove(settings.active_image)
        settings.active_image = max(0, min(settings.active_image, len(settings.images) - 1))
        return {"FINISHED"}


def _make_client(operator, context):
    if not sdk_available():
        operator.report({"ERROR"}, tr("The Claude library is missing: install it from the panel."))
        return None
    if not api_key_available(context):
        operator.report({"ERROR"}, tr("This button needs an Anthropic API key. With MCP, ask your AI assistant instead."))
        return None
    import anthropic

    key = get_prefs(context).api_key.strip()
    try:
        return anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
    except Exception:
        operator.report({"ERROR"}, tr("API key missing: set it in the add-on preferences."))
        return None


class R2B_OT_generate(Operator):
    bl_idname = "r2b.generate"
    bl_label = "Generate model"
    bl_description = "Send the images to Claude and build the model in the scene"

    @classmethod
    def poll(cls, context):
        if agent.SESSION.running or not _api_mode_ready(cls, context):
            return False
        if not context.scene.r2b.images:
            cls.poll_message_set(tr("Add at least one reference image."))
            return False
        return True

    def execute(self, context):
        settings = context.scene.r2b
        prefs = get_prefs(context)
        client = _make_client(self, context)
        if client is None:
            return {"CANCELLED"}
        try:
            content = agent.build_user_content(
                [(img.path, img.view) for img in settings.images], settings.description,
                settings.style, settings.tri_budget,
            )
        except Exception as exc:
            self.report({"ERROR"}, tr("Could not read an image: {error}", error=exc))
            return {"CANCELLED"}
        if settings.replace_previous:
            scene_tools.clear_collection(context.scene)
            script_store.forget()
        agent.start(client, prefs.model, prefs.effort, settings.max_steps, content, new_conversation=True,
                    references=[bpy.path.abspath(img.path) for img in settings.images])
        return {"FINISHED"}


class R2B_OT_refine(Operator):
    bl_idname = "r2b.refine"
    bl_label = "Refine"
    bl_description = "Ask for changes to the current model (or continue if it stopped)"

    @classmethod
    def poll(cls, context):
        return not agent.SESSION.running and agent.has_conversation() and _api_mode_ready(cls, context)

    def execute(self, context):
        settings = context.scene.r2b
        prefs = get_prefs(context)
        client = _make_client(self, context)
        if client is None:
            return {"CANCELLED"}
        feedback = settings.feedback.strip() or (
            "Continue: compare the current model against the references again and improve what is still off."
        )
        content = [{"type": "text", "text": feedback}]
        agent.start(client, prefs.model, prefs.effort, settings.max_steps, content, new_conversation=False)
        settings.feedback = ""
        return {"FINISHED"}


class R2B_OT_cancel(Operator):
    bl_idname = "r2b.cancel"
    bl_label = "Stop"

    def execute(self, context):
        agent.cancel()
        return {"FINISHED"}


class R2B_OT_export(Operator):
    bl_idname = "r2b.export"
    bl_label = "Export to game"
    bl_description = (
        "Export the Ref2Blender collection ready for the engine: modifiers applied, pivot at "
        "the base, UVs and correct scale. Does not modify the objects in the scene"
    )

    filepath: StringProperty(subtype="FILE_PATH")
    filter_glob: StringProperty(default="*.fbx;*.glb", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return bool(scene_tools.target_objects(context.scene))

    def invoke(self, context, event):
        if not self.filepath:
            self.filepath = "Model"
        context.window_manager.fileselect_add(self)
        return {"RUNNING_MODAL"}

    def execute(self, context):
        settings = context.scene.r2b
        options = dict(
            engine=settings.export_engine, join=settings.export_join, lods=settings.export_lods,
            collision=settings.export_collision, palette=settings.export_palette,
        )
        try:
            if settings.export_each_asset:
                infos = export_tools.export_all(os.path.dirname(bpy.path.abspath(self.filepath)), **options)
            else:
                infos = [export_tools.export_model(self.filepath, **options)]
        except Exception as exc:
            self.report({"ERROR"}, tr("Export failed: {error}", error=exc))
            return {"CANCELLED"}
        for info in infos:
            print("[Ref2Blender]", export_tools.describe(info))
        total = sum(info["triangles"] for info in infos)
        self.report({"INFO"}, tr("Exported {files} file(s), {tris} triangles in total",
                                 files=len(infos), tris=total))
        return {"FINISHED"}


class R2B_OT_rebuild(Operator):
    bl_idname = "r2b.rebuild"
    bl_label = "Rebuild"
    bl_description = (
        "Write the measures above into the model script and run it again. The Ref2Blender "
        "collection is emptied first, so objects you added to it by hand are removed"
    )

    @classmethod
    def poll(cls, context):
        return not agent.SESSION.running and bool(script_store.stored())

    def invoke(self, context, event):
        if script_store.unscripted():
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        ok, output = script_store.rebuild(context.scene)
        if not ok:
            print("[Ref2Blender]", output)
            last = output.strip().splitlines()[-1] if output.strip() else ""
            self.report({"ERROR"}, tr("The script failed: {error}", error=last))
            return {"CANCELLED"}
        tris = sum(len(p.vertices) - 2 for o in scene_tools.target_objects(context.scene)
                   if o.type == "MESH" for p in o.data.polygons)
        self.report({"INFO"}, tr("Model rebuilt: {tris} triangles", tris=tris))
        return {"FINISHED"}


class R2B_OT_reload_params(Operator):
    bl_idname = "r2b.reload_params"
    bl_label = "Reload the measures"
    bl_description = "Read the measures from the script again, dropping the values edited here"

    def execute(self, context):
        script_store.sync_parameters(context.scene)
        return {"FINISHED"}


class R2B_OT_bridge_start(Operator):
    bl_idname = "r2b.bridge_start"
    bl_label = "Connect"
    bl_description = (
        "Open a local port so your AI assistant (Claude Code, Claude Desktop, Cursor...) "
        "can build models in this scene through the Ref2Blender MCP server"
    )

    def execute(self, context):
        try:
            bridge.start()
        except OSError as exc:
            self.report({"ERROR"}, tr("Could not open port {port}: {error}",
                                      port=bridge.DEFAULT_PORT, error=exc))
            return {"CANCELLED"}
        return {"FINISHED"}


class R2B_OT_bridge_stop(Operator):
    bl_idname = "r2b.bridge_stop"
    bl_label = "Disconnect"

    def execute(self, context):
        bridge.stop()
        return {"FINISHED"}


# -------------------------------------------------------------------------- UI

class R2B_UL_images(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        row = layout.row(align=True)
        row.label(text=item.name, icon="IMAGE_DATA")
        row.prop(item, "view", text="")


class R2B_PT_main(Panel):
    bl_idname = "R2B_PT_main"
    bl_label = "Ref2Blender"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Ref2Blender"

    def draw(self, context):
        layout = self.layout
        settings = context.scene.r2b
        session = agent.SESSION
        prefs = get_prefs(context)

        box = layout.box()
        box.label(text=tr("With your AI assistant (MCP)"), icon="LINKED")
        if bridge.is_running():
            box.label(text=tr("Connected on port {port} · {count} requests",
                              port=bridge.port(), count=bridge.request_count()))
            box.label(text=tr("Give the reference images to your assistant"))
            box.operator("r2b.bridge_stop", text=tr("Disconnect"), icon="UNLINKED")
        else:
            box.operator("r2b.bridge_start", text=tr("Connect"), icon="PLAY")
        if not prefs.safe_mode:
            box.label(text=tr("Safe mode is OFF"), icon="ERROR")

        scripts = script_store.stored()
        if scripts:
            box = layout.box()
            row = box.row()
            row.label(text=tr("Model script"), icon="FILE_SCRIPT")
            row.operator("r2b.reload_params", text="", icon="FILE_REFRESH")
            col = box.column(align=True)
            for param in settings.params:
                col.prop(param, "value", text=param.name)
            box.operator("r2b.rebuild", text=tr("Rebuild"), icon="FILE_REFRESH")
            sub = box.column(align=True)
            sub.scale_y = 0.75
            sub.label(text=tr("Tables and the rest: Text Editor,"))
            sub.label(text=scripts[0].name)

        box = layout.box()
        box.label(text=tr("Export to game"), icon="EXPORT")
        box.prop(settings, "export_engine", text="")
        col = box.column(align=True)
        col.prop(settings, "export_join", text=tr("Join into a single mesh"))
        col.prop(settings, "export_palette", text=tr("Palette (single material)"))
        col.prop(settings, "export_collision", text=tr("Convex collision"))
        col.prop(settings, "export_lods", text=tr("LOD levels"))
        if scene_tools.asset_names(context.scene):
            col.prop(settings, "export_each_asset", text=tr("One file per asset"))
        box.operator("r2b.export", text=tr("Export to game"), icon="EXPORT")

        layout.separator()
        layout.label(text=tr("With an API key"), icon="KEYINGSET")
        if not sdk_available():
            box = layout.box()
            box.label(text=tr("The Claude library must be installed"), icon="ERROR")
            box.operator("r2b.install_deps", text=tr("Install the Claude library"), icon="IMPORT")
            return
        if not api_key_available(context):
            box = layout.box()
            box.label(text=tr("API key missing"), icon="ERROR")
            col = box.column(align=True)
            col.scale_y = 0.75
            col.label(text=tr("Generate and Refine need a key."))
            col.label(text=tr("With MCP (no key), ask your assistant."))
            box.operator("preferences.addon_show", text=tr("Open preferences")).module = __package__

        layout.label(text=tr("Reference images"))
        row = layout.row()
        row.template_list("R2B_UL_images", "", settings, "images", settings, "active_image", rows=3)
        col = row.column(align=True)
        col.operator("r2b.add_images", text="", icon="ADD")
        col.operator("r2b.remove_image", text="", icon="REMOVE")

        layout.prop(settings, "description", text="", icon="TEXT", placeholder=tr("Description (optional)"))
        layout.prop(settings, "style", text=tr("Style"))
        layout.prop(settings, "tri_budget", text=tr("Max triangles"))
        layout.prop(settings, "replace_previous", text=tr("Delete the previous model"))
        layout.prop(settings, "max_steps", text=tr("Max steps"))

        if session.running:
            layout.operator("r2b.cancel", text=tr("Stop"), icon="CANCEL")
        else:
            layout.operator("r2b.generate", text=tr("Generate model"), icon="MESH_MONKEY")
            if agent.has_conversation():
                box = layout.box()
                box.prop(settings, "feedback", text="", placeholder=tr("What do you want to change?"))
                box.operator("r2b.refine", text=tr("Refine"), icon="MODIFIER")

        box = layout.box()
        box.label(text=session.status or tr("Idle"), icon="TIME" if session.running else "INFO")
        if session.tokens_in or session.tokens_out:
            box.label(text=tr("Tokens: {tin} in / {tout} out",
                              tin=f"{session.tokens_in:,}", tout=f"{session.tokens_out:,}"))
        if session.log:
            col = box.column(align=True)
            col.scale_y = 0.75
            for line in session.log[-12:]:
                col.label(text=line)


# -------------------------------------------------------------------- register

CLASSES = (
    R2B_RefImage,
    R2B_Param,
    R2B_Settings,
    R2B_Preferences,
    R2B_OT_install_deps,
    R2B_OT_add_images,
    R2B_OT_remove_image,
    R2B_OT_generate,
    R2B_OT_refine,
    R2B_OT_cancel,
    R2B_OT_export,
    R2B_OT_rebuild,
    R2B_OT_reload_params,
    R2B_OT_bridge_start,
    R2B_OT_bridge_stop,
    R2B_UL_images,
    R2B_PT_main,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.r2b = PointerProperty(type=R2B_Settings)


def unregister():
    agent.cancel()
    bridge.stop()
    del bpy.types.Scene.r2b
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
