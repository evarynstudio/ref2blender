# Ref2Blender

Give your AI assistant reference images and get a **game-ready 3D model** built in your own
Blender scene, then exported for Unity, Unreal Engine or Godot.

Ref2Blender is a Blender add-on plus an [MCP](https://modelcontextprotocol.io) server. The
assistant (Claude Code, Claude Desktop, Cursor, or any MCP client) writes Blender Python,
renders the result from fixed views, compares it against your references and corrects it.
When you are happy, it exports the asset with the pivot, scale, LODs, collision and materials
a game engine expects.

It is focused on one job, game assets from references. For a general-purpose Blender
integration with asset libraries and AI mesh generation, see
[blender-mcp](https://github.com/ahujasid/blender-mcp).

## What it does

- **Model from references.** Explicit geometry (vertices, primitives, modifiers), so the result
  is editable, not a dense scanned mesh.
- **Check renders.** Orthographic front/back/left/right/top/bottom and two perspective views,
  auto-framed, showing only the generated model.
- **Triangle budgets.** A game-readiness report: triangles against a budget, materials,
  missing UVs, n-gons, warnings.
- **Engine export** that leaves your scene untouched:
  - modifiers applied, parts joined, pivot at the bottom centre, UVs unwrapped where missing;
  - **LODs**, each level with half the triangles;
  - a **convex collision** mesh named the way each engine expects;
  - a **palette**: every colour collapsed into one material and one small swatch texture.
- **Asset sets.** Several assets in one scene, sharing materials, exported one file each.
- **Safe mode**, on by default: generated code that touches files, processes or the network
  is refused before it runs.
- **No telemetry.** The local connection is bound to `127.0.0.1` and protected by a random
  token created each time you press Connect.

## Requirements

- Blender 4.2 or newer.
- Python 3.10 or newer for the MCP server (it runs outside Blender).
- An MCP client: Claude Code, Claude Desktop, Cursor, VS Code, and so on.

## Install

**1. Install the server and the add-on**

```bash
pip install git+https://github.com/evarynstudio/ref2blender
```

```bash
ref2blender-mcp install-addon
```

The second command copies the add-on into the add-ons folder of every Blender 4.2+ it finds.
Then, in Blender: **Edit > Preferences > Add-ons**, enable **Ref2Blender**.

Prefer not to use the command? Download `ref2blender-<version>.zip` from the Releases page
and install it in Blender with **Edit > Preferences > Get Extensions > Install from Disk**.

**2. Add the server to your MCP client**

Claude Code:

```bash
claude mcp add ref2blender -- ref2blender-mcp
```

Claude Desktop, Cursor and other clients that use a JSON config:

```json
{
  "mcpServers": {
    "ref2blender": { "command": "ref2blender-mcp" }
  }
}
```

With [uv](https://docs.astral.sh/uv/) you can skip the `pip install` and use
`"command": "uvx", "args": ["--from", "git+https://github.com/evarynstudio/ref2blender", "ref2blender-mcp"]`.

## Use

1. In Blender, press `N` in the 3D viewport, open the **Ref2Blender** tab and press **Connect**.
2. Give your assistant the reference images and say what you want, for example:

   > Model this chair from the two images. Low poly, under 800 triangles.

3. Ask for changes in plain words. When it looks right:

   > Export it to `D:/MyGame/Assets/Models/Chair` for Unity, with collision and a palette.

Everything the assistant creates goes into a collection called `Ref2Blender`; the rest of your
scene is left alone.

### Tools the server exposes

| Tool | What it does |
|---|---|
| `blender_status` | Checks that Blender is open and connected |
| `execute_blender_code` | Runs Python in Blender; returns output, errors and a scene summary |
| `render_views` | Returns check renders of the model as images |
| `scene_summary` | Lists objects with dimensions and triangle counts |
| `check_model` | Game-readiness report, optionally against a triangle budget |
| `export_model` | Exports for `unity`, `unreal`, `godot` or `generic` (GLB) |
| `export_all_assets` | One file per asset sub-collection |
| `clear_model` | Empties the `Ref2Blender` collection |

### Engine notes

| Engine | Format | Result |
|---|---|---|
| Unity | FBX | Scale 1, rotation 0, front towards +Z. LODGroup built from the `_LOD0`, `_LOD1`... names |
| Unreal Engine | FBX | Front towards +X. LODs in an FBX LodGroup, collision as `UCX_<Name>_00` |
| Godot | GLB | Collision as `<Name>-convcolonly` |

**Unity:** copy [`unity/Ref2BlenderPostprocessor.cs`](unity/Ref2BlenderPostprocessor.cs) into
`Assets/Editor/` once per project. It turns the `_Collider` child into a convex `MeshCollider`
and imports palette textures unfiltered. LODs work without it.

**Verification status.** The Unity preset is checked inside Unity 6 (scale, rotation, pivot,
orientation, LODGroup, collider, palette). The Unreal and Godot presets follow each engine's
documented conventions and round-trip correctly through Blender, but have not yet been checked
inside those engines. Reports are welcome.

## Without an MCP client

The add-on also has a built-in mode that calls the Claude API directly from the panel with
your own Anthropic API key (**With an API key** section). It installs the `anthropic` package
into a private folder the first time you use it.

There is also a small command-line client, [`r2b.py`](r2b.py), that needs only the Python
standard library: `python r2b.py ping`, `exec`, `render`, `check`, `export`.

## Security

Ref2Blender runs code written by an AI model inside your Blender. Read this before using it.

- **Safe mode is a guard rail, not a sandbox.** It inspects the code before running it and
  restricts the Python builtins, refusing file, process and network access and any import
  beyond Blender's modelling modules and basic maths. Python cannot be fully sandboxed inside
  its own process, so a determined bypass is possible. It protects against a model wandering
  outside "build geometry", not against a deliberately hostile one.
- **Save your work first.** Generated code can still delete or change objects in the scene.
- **Exports write files** where the assistant is told to. Review the path it proposes.
- You can turn safe mode off in the add-on preferences if a task needs it (loading an image
  texture, for example). The panel shows a warning while it is off.
- The bridge listens only on `127.0.0.1` and requires a per-session token stored in
  `~/.ref2blender/bridge.json`. Other programs running as your user could read that file.

## Development

```bash
pip install -e .
```

Tests run in a headless Blender (replace `blender` with the path to your executable):

```bash
blender --background --factory-startup --python tests/smoke_test.py
```

```bash
blender --background --factory-startup --python tests/export_test.py
```

```bash
blender --background --factory-startup --python tests/safe_test.py
```

```bash
python tests/mcp_test.py "path/to/blender"
```

Build the installable extension zip into `dist/` (create the folder first):

```bash
blender --command extension build --source-dir ref2blender --output-dir dist
```

The interface is in English with a Catalan translation; other languages can be added in
[`ref2blender/i18n.py`](ref2blender/i18n.py).

## Licence

GPL-3.0-or-later, the same licence as Blender. See [LICENSE](LICENSE).
