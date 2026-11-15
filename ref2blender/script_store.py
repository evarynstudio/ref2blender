"""Keep the script that builds the model inside the .blend file.

Every piece of generated code that ran without error is kept in a text datablock (one for
the Ref2Blender collection, one per asset), so the model can be rebuilt later, by hand or by
an assistant in a new conversation. The plain numbers assigned to UPPER_CASE names at the top
level of a script are its measures: the panel lists them, and Rebuild writes the edited
values back into the script and runs it again.
"""

import ast

import bpy

from . import scene_tools

TEXT_PREFIX = "Ref2Blender_Model"
STEP_MARK = "\n\n# ---- next step ----\n"
MAX_PARAMETERS = 60
SCRIPTED = "r2b_scripted"   # custom property on the objects a stored script made


def text_name(asset=None):
    return f"{TEXT_PREFIX}.{asset}.py" if asset else f"{TEXT_PREFIX}.py"


def asset_of(text):
    """None for the script of the whole collection, the asset name otherwise."""
    return text.name[len(TEXT_PREFIX) + 1:-3] or None


def stored():
    """The stored scripts: the collection's own first, then each asset's."""
    texts = [t for t in bpy.data.texts
             if t.name == text_name() or (t.name.startswith(TEXT_PREFIX + ".") and t.name.endswith(".py"))]
    return sorted(texts, key=lambda t: (t.name != text_name(), t.name))


def forget():
    for text in stored():
        bpy.data.texts.remove(text)
    root = _root()
    for obj in (root.all_objects if root else ()):
        if SCRIPTED in obj:
            del obj[SCRIPTED]
    sync_parameters()


def _rebuilds(code):
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False
    return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "clear_model"
               for n in ast.walk(tree))


def _root():
    return bpy.data.collections.get(scene_tools.COLLECTION_NAME)


def snapshot():
    """Names of the objects in the collection; take it before running code that record() will keep."""
    root = _root()
    return {o.name for o in root.all_objects} if root else set()


def unscripted():
    """Objects in the collection that no stored script made (added by hand, or older than
    the script): a rebuild deletes them."""
    root = _root()
    return [o for o in root.all_objects if SCRIPTED not in o] if root else []


def record(code, before=()):
    """Keep code that has just run successfully; `before` is the snapshot() taken before it ran.
    A script that calls clear_model() rebuilds the model, so it replaces what was stored;
    anything else is a further step, appended. Returns the text, or None when the code only
    adds to objects no script accounts for (a rebuild from it alone would lose them)."""
    root = _root()
    if root is None:
        return None
    active = bpy.context.view_layer.active_layer_collection.collection
    is_asset = active != root and active in set(root.children_recursive)
    asset = active.name if is_asset else None
    name = text_name(asset)
    text = bpy.data.texts.get(name)
    code = code.strip("\n") + "\n"
    if _rebuilds(code) or text is None:
        own = active.all_objects if is_asset else root.objects
        if not _rebuilds(code) and any(o.name in before and SCRIPTED not in o for o in own):
            return None
        if text is None:
            text = bpy.data.texts.new(name)
            text.use_fake_user = True
        text.from_string(code)
    else:
        text.from_string(text.as_string().rstrip("\n") + STEP_MARK + code)
    for obj in root.all_objects:
        if obj.name not in before:
            obj[SCRIPTED] = True
    # Scripts of assets that no longer exist would bring them back on a rebuild.
    for other in stored():
        other_asset = asset_of(other)
        if other_asset and other_asset != asset:
            col = scene_tools.find_asset(other_asset)
            if col is None or not col.all_objects:
                bpy.data.texts.remove(other)
    sync_parameters()
    return text


# ----------------------------------------------------------------- parameters

def _number(node):
    """(value, is_int) for a plain number literal, else None."""
    sign = 1
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        sign = -1 if isinstance(node.op, ast.USub) else 1
        node = node.operand
    if (isinstance(node, ast.Constant) and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)):
        return sign * node.value, isinstance(node.value, int)
    return None


def parameters(source):
    """Top-level NAME = number assignments (also NAME_A, NAME_B = 1, 2), in order:
    a list of (name, value, is_int, (line, start, end)), positions in UTF-8 bytes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    found, seen = [], set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        if isinstance(target, ast.Name):
            pairs = [(target, value)]
        elif (isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple)
              and len(target.elts) == len(value.elts)):
            pairs = list(zip(target.elts, value.elts))
        else:
            continue
        for name_node, value_node in pairs:
            number = _number(value_node)
            if (number is None or not isinstance(name_node, ast.Name)
                    or value_node.lineno != value_node.end_lineno):
                continue
            name = name_node.id
            if name != name.upper() or not any(ch.isalpha() for ch in name) or name in seen:
                continue
            seen.add(name)
            found.append((name, number[0], number[1],
                          (value_node.lineno, value_node.col_offset, value_node.end_col_offset)))
    return found[:MAX_PARAMETERS]


def _literal(value, was_int):
    if was_int and abs(value - round(value)) < 1e-6:
        return str(int(round(value)))
    return repr(round(float(value), 4))


def apply_parameters(source, values):
    """Return `source` with the numbers of the named parameters replaced."""
    lines = source.split("\n")
    edits = [(pos, _literal(values[name], is_int))
             for name, _value, is_int, pos in parameters(source) if name in values]
    for (line, start, end), text in sorted(edits, reverse=True):
        raw = lines[line - 1].encode("utf-8")
        lines[line - 1] = (raw[:start] + text.encode("utf-8") + raw[end:]).decode("utf-8")
    return "\n".join(lines)


def sync_parameters(scene=None):
    """Refill the panel's list of measures from the stored scripts."""
    scene = scene or bpy.context.scene
    settings = getattr(scene, "r2b", None)
    if settings is None:
        return
    settings.params.clear()
    for text in stored():
        for name, value, is_int, _pos in parameters(text.as_string()):
            item = settings.params.add()
            item.name = name
            item.script = text.name
            item.is_int = is_int
            item.value = item.original = value


def _write_edits(scene):
    settings = getattr(scene, "r2b", None)
    if settings is None:
        return
    for text in stored():
        changed = {p.name: p.value for p in settings.params
                   if p.script == text.name and p.value != p.original}
        if changed:
            text.from_string(apply_parameters(text.as_string(), changed))


def rebuild(scene=None, safe=None):
    """Write the measures edited in the panel into the scripts, empty the collection and run
    every stored script again. Returns (ok, output)."""
    scene = scene or bpy.context.scene
    texts = stored()
    if not texts:
        return False, "No model script is stored in this file."
    _write_edits(scene)
    scene_tools.clear_collection(scene)
    namespace = scene_tools.new_namespace()
    ok, outputs = True, []
    for text in texts:
        ok, output = scene_tools.run_code(text.as_string(), namespace, safe)
        outputs.append(output)
        if not ok:
            break
    root = _root()
    for obj in (root.all_objects if root else ()):
        obj[SCRIPTED] = True
    sync_parameters(scene)
    return ok, "\n".join(o for o in outputs if o)
