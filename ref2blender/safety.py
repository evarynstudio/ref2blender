"""Safe mode for AI-generated code.

Two layers:
  1. a static check of the code's syntax tree, run before anything executes;
  2. restricted builtins at run time (no open/eval/exec, guarded import/getattr).

This is a guard rail against a model wandering outside "build geometry", not a
security sandbox: Python cannot be fully sandboxed inside its own process.
This module must not import bpy, so it can be tested on its own.
"""

import ast
import builtins

ALLOWED_MODULES = {
    "bpy", "bmesh", "mathutils", "bpy_extras", "gpu_extras", "numpy",
    "math", "cmath", "random", "itertools", "functools", "collections", "typing",
    "dataclasses", "enum", "colorsys", "array", "copy", "operator", "heapq", "bisect",
    "statistics", "fractions", "decimal", "string", "re", "time", "abc", "numbers",
}

BLOCKED_NAMES = {
    "open", "eval", "exec", "compile", "__import__", "input", "breakpoint",
    "globals", "locals", "vars", "exit", "quit", "help", "memoryview",
    "__builtins__", "__loader__", "__spec__",
}

# Dunder attributes ordinary modelling code may touch.
ALLOWED_DUNDERS = {
    "__init__", "__name__", "__doc__", "__repr__", "__str__", "__len__", "__iter__",
    "__next__", "__eq__", "__hash__", "__lt__", "__call__", "__enter__", "__exit__",
    "__post_init__", "__getitem__", "__setitem__", "__contains__",
}

# Attribute names that reach files, processes, the network, or code that runs later.
# Matched by name anywhere in the code, so aliasing (w = bpy.ops.wm) does not help.
BLOCKED_ATTRS = {
    # bpy.ops namespaces that have no innocent namesake
    "wm", "script", "preferences", "extensions", "export_scene", "import_scene",
    "export_mesh", "import_mesh", "export_anim", "import_anim", "import_curve",
    # file and session operators / methods
    "open_mainfile", "save_mainfile", "save_as_mainfile", "read_homefile",
    "read_factory_settings", "quit_blender", "url_open", "path_open", "save_userpref",
    "python_file_run", "run_script", "libraries", "load", "save", "save_render",
    "save_as", "write", "pack", "unpack", "pack_all", "unpack_all", "reload",
    "filepath", "filepath_raw", "directory",
    # code that runs later or elsewhere
    "as_module", "handlers", "timers", "driver_namespace", "execfile", "driver_add",
    "expression", "register_class", "unregister_class", "load_scripts", "modules",
    # numpy file access
    "tofile", "fromfile", "memmap", "savetxt", "loadtxt", "genfromtxt", "savez",
    "savez_compressed", "DataSource",
}
# Operator namespaces whose names are also innocent attributes elsewhere
# (scene.render, context.screen, node.image...): blocked only directly under bpy.ops.
BLOCKED_OPS_NAMESPACES = {"image", "screen", "render", "text", "sound", "file", "console"}


class Blocked(Exception):
    """Raised when code is refused by safe mode."""


def _base_name(node):
    """Name of the object an attribute hangs from: `a.b.c` -> 'b' for node c."""
    value = node.value
    if isinstance(value, ast.Attribute):
        return value.attr
    if isinstance(value, ast.Name):
        return value.id
    return None


def check(code):
    """Raise Blocked if the code uses something safe mode does not allow."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return  # let exec report the syntax error with its usual traceback

    def refuse(node, what):
        raise Blocked(f"line {getattr(node, 'lineno', '?')}: {what}")

    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    dynamic_blocked = BLOCKED_ATTRS | BLOCKED_OPS_NAMESPACES | {"ops"}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] not in ALLOWED_MODULES or any(p in dynamic_blocked for p in parts[1:]):
                    refuse(node, f"import of '{alias.name}' is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in ALLOWED_MODULES:
                refuse(node, f"import from '{node.module}' is not allowed")
            parts = (node.module or "").split(".")
            if any(p in dynamic_blocked for p in parts[1:] if p != "ops"):
                refuse(node, f"import from '{node.module}' is not allowed")
            for alias in node.names:
                if alias.name in dynamic_blocked or alias.name == "*":
                    refuse(node, f"importing '{alias.name}' is not allowed")
        elif isinstance(node, ast.Name):
            if node.id in BLOCKED_NAMES:
                refuse(node, f"'{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr.startswith("__") and attr not in ALLOWED_DUNDERS:
                refuse(node, f"attribute '{attr}' is not allowed")
            if attr in BLOCKED_ATTRS:
                refuse(node, f"'.{attr}' is not allowed (file, process, network or deferred-code access)")
            if attr in BLOCKED_OPS_NAMESPACES and _base_name(node) == "ops":
                refuse(node, f"'bpy.ops.{attr}' is not allowed")
            if attr == "ops" and not isinstance(parents.get(node), ast.Attribute):
                # `o = bpy.ops` would let the checks above be sidestepped through an alias.
                refuse(node, "use bpy.ops.<namespace>.<operator>(...) directly, without aliasing bpy.ops")


def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".")[0] not in ALLOWED_MODULES:
        raise Blocked(f"import of '{name}' is not allowed")
    return builtins.__import__(name, globals, locals, fromlist, level)


def _check_dynamic_attr(name):
    if not isinstance(name, str):
        return
    if ((name.startswith("__") and name not in ALLOWED_DUNDERS) or name in BLOCKED_ATTRS
            or name in BLOCKED_OPS_NAMESPACES or name == "ops"):
        raise Blocked(f"attribute '{name}' is not allowed")


def _guarded_getattr(obj, name, *default):
    _check_dynamic_attr(name)
    return getattr(obj, name, *default)


def _guarded_setattr(obj, name, value):
    _check_dynamic_attr(name)
    setattr(obj, name, value)


def _guarded_hasattr(obj, name):
    _check_dynamic_attr(name)
    return hasattr(obj, name)


def safe_builtins():
    allowed = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if not name.startswith("_") and name not in BLOCKED_NAMES
    }
    allowed.update(
        __import__=_guarded_import,
        __build_class__=builtins.__build_class__,
        __name__="builtins",
        getattr=_guarded_getattr,
        setattr=_guarded_setattr,
        hasattr=_guarded_hasattr,
    )
    del allowed["delattr"]
    return allowed


MESSAGE = (
    "Blocked by Ref2Blender safe mode: {reason}.\n"
    "Safe mode only allows building geometry: no file, process or network access, no "
    "imports beyond Blender's modelling modules and basic maths. Rewrite the step without "
    "it. (The user can turn safe mode off in the add-on preferences if they really need this.)"
)
