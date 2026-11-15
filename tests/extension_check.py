"""Checks the add-on when installed as a Blender extension (package name bl_ext.<repo>.ref2blender).

Run against a throwaway profile so the real one is untouched:
    set BLENDER_USER_RESOURCES=<temp dir>
    blender --command extension install-file -r user_default -e dist/ref2blender-<v>.zip
    blender --background --python tests/extension_check.py
"""

import bpy

module = next((name for name in bpy.context.preferences.addons.keys() if name.endswith(".ref2blender")), None)
assert module and module.startswith("bl_ext."), list(bpy.context.preferences.addons.keys())
prefs = bpy.context.preferences.addons[module].preferences
assert prefs.safe_mode is True and prefs.language == "AUTO"

import importlib

scene_tools = importlib.import_module(module + ".scene_tools")
i18n = importlib.import_module(module + ".i18n")
bridge = importlib.import_module(module + ".bridge")

# Preferences are found through the extension's package name.
prefs.language = "CA"
assert i18n.tr("Connect") == "Connecta"
prefs.language = "EN"
assert i18n.tr("Connect") == "Connect"

ns = scene_tools.new_namespace()
ok, out = scene_tools.run_code("import os", ns)
assert not ok and "safe mode" in out
prefs.safe_mode = False
ok, out = scene_tools.run_code("import os\nprint('unsafe ok')", ns)
assert ok and "unsafe ok" in out
prefs.safe_mode = True

assert bpy.ops.r2b.bridge_start() == {"FINISHED"} and bridge.is_running()
assert bpy.ops.r2b.bridge_stop() == {"FINISHED"} and not bridge.is_running()
print("EXTENSION OK:", module)
