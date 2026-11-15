# Ref2Blender

Blender add-on plus MCP server that builds game-ready 3D models from reference images.
User-facing documentation is in `README.md`.

## Layout

- `ref2blender/` is the Blender add-on (also a Blender extension, see `blender_manifest.toml`).
  - `bridge.py`: local socket server, one JSON request per connection, token-protected.
  - `scene_tools.py`: runs generated code, check renders, scene summary, asset sub-collections.
  - `build_tools.py`: modelling helpers preloaded for generated code (`loft_symmetric`,
    `revolve`, `boolean`, `inset_panel`...). Its `DOC` is repeated as `HELPERS_DOC` in
    `src/ref2blender_mcp/server.py`; change both together.
  - `safety.py`: safe mode (static AST check plus restricted builtins). Must not import `bpy`.
  - `export_tools.py`: game-readiness report and engine export (LODs, collision, palette).
  - `agent.py`: built-in API-key mode that calls the Claude API from the panel.
  - `i18n.py`: interface strings; English is the source language, `tr()` translates.
- `src/ref2blender_mcp/` is the MCP server (`server.py`), the bridge client (`client.py`) and
  `install-addon` (`install.py`). It supports `mcp` 1.x and 2.x; keep both import paths working.
- `r2b.py` is a standard-library command-line client for the same bridge.
- `unity/Ref2BlenderPostprocessor.cs` is the Unity-side import script users copy into their project.

## Modelling through the bridge

When the MCP server is not configured, drive Blender with `r2b.py` using any Python 3
(Blender's bundled Python works):

```bash
python r2b.py ping
python r2b.py exec path/to/step.py
python r2b.py render front right three_quarter
python r2b.py render --camera 35,8,50,1.5     # azimuth,elevation[,lens[,aspect]]: match a photo
python r2b.py check --budget 2000
python r2b.py export path/to/Assets/Models/Chair --engine unity --lods 2 --collision --palette
```

`render` writes PNGs into `renders/`. If `ping` says the bridge is not running, ask the user to
press **Connect** in Blender's Ref2Blender sidebar tab; do not launch Blender unasked.
The modelling workflow and scene conventions are the `INSTRUCTIONS` string in
`src/ref2blender_mcp/server.py`; follow them when modelling by hand too.

Ask before writing into a user's game project (exports, the Unity postprocessor).

## Development

- UI text: write English in the code, wrap it in `tr()`, and add the Catalan line to `i18n.py`.
- Anything that executes generated code must go through `scene_tools.run_code`, so safe mode
  applies. New bridge commands must validate their inputs before touching `bpy`.
- Export must never modify the user's objects: work on copies tracked by `_Temp` and clean up.
- Tests (headless Blender; all must pass before a change is done):

```bash
blender --background --factory-startup --python tests/smoke_test.py
blender --background --factory-startup --python tests/export_test.py
blender --background --factory-startup --python tests/safe_test.py
blender --background --factory-startup --python tests/build_test.py
python tests/mcp_test.py "path/to/blender"
```

- `tests/mcp_test.py` replaces `~/.ref2blender/bridge.json`: never run it (or pass `--force`)
  while the user's Blender is connected, it cuts their session.
- Engine verification: the Unity preset is checked inside Unity with
  `tests/make_engine_samples.py` and `tests/unity/Ref2BlenderVerify.cs` (batch-mode import into
  a throwaway project). The Unreal and Godot presets are only checked by re-importing into
  Blender; if a user reports a wrong rotation, size, LOD or collision there, fix the preset in
  `export_tools.py` and say what was and was not verified.
- Plain (non-palette) material colours reach Unity with the same numbers but are read as sRGB,
  so they look slightly darker than in Blender; the palette texture is colour-correct.
