"""Ref2Blender MCP server (stdio).

    ref2blender-mcp                 run the server (what an MCP client launches)
    ref2blender-mcp install-addon   copy the Blender add-on into Blender's add-ons folder
"""

import argparse
import base64
import os
import sys
from typing import Literal, Optional

try:  # mcp 2.x
    from mcp.server.mcpserver import Image
    from mcp.server.mcpserver import MCPServer as FastMCP
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP, Image
    from mcp.server.fastmcp.exceptions import ToolError

from . import __version__
from .client import BridgeError, call

View = Literal["front", "back", "left", "right", "top", "bottom", "three_quarter", "three_quarter_back"]
Engine = Literal["unity", "unreal", "godot", "generic"]
Pivot = Literal["bottom", "center", "origin"]

INSTRUCTIONS = """\
Ref2Blender builds game-ready 3D models in the user's open Blender session, usually from \
reference images the user gives you. You cannot see the viewport: render_views is your eyes.

Workflow
1. Call blender_status. If Blender is not connected, tell the user how to connect and stop.
2. Study the references: which view each shows, the proportions (height : width : depth), the \
main volumes, the details that matter. Agree a style and a triangle budget with the user, or \
assume them and say so (small prop 300-1500 triangles, hero prop or vehicle 2k-8k).
3. Build with execute_blender_code in a few steps: main volumes, then secondary shapes, then \
details and materials. Tracebacks come back to you; fix and rerun.
4. After the main volumes, and again after detailing, call render_views and compare against \
the references: silhouette, proportions, placement of parts, colours. Fix what does not match. \
Two or three review rounds is normal.
5. Run check_model before telling the user it is finished, and fix its warnings.
6. Export with export_model only when the user asks, into the folder they name.

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
- Compare like with like. Render the view closest to each reference, list the differences in \
order of their effect on the outline, and fix the largest first.
- Before you finish: no part hidden inside another, none floating; round parts that stand on \
the ground use a segment count divisible by 4 so the lowest point is exactly Z=0; left and \
right parts come from the same numbers, mirrored.
- Tell the user which sides you had no reference for and therefore inferred.

Conventions inside Blender
- Z is up, units are metres. The model's front faces -Y (the "front" render looks from -Y \
towards +Y). Rest it on Z=0, centred on the origin.
- Create everything in the collection "Ref2Blender". It is the active collection and the \
variable `collection` points to it. Never touch objects outside it: the user's own work may be \
in the scene.
- The Python namespace persists between calls; bpy, bmesh, mathutils, math and Vector are \
preloaded. make_material(name, (r, g, b), metallic=0.0, roughness=0.5) creates or reuses a \
material whose viewport colour matches its base colour (renders use the viewport colour).
- For several separate assets, call new_asset("Name") at the top of each script: it creates a \
sub-collection and makes it active. Space assets apart along X and share materials.
- Game assets: no faces hidden inside other parts, no subdivision beyond what the silhouette \
needs, bevels with one or two segments, few materials reused across parts.
- Prefer the data API and bmesh over bpy.ops. Keep parts as separate, well-named objects.
- Safe mode (on by default) refuses code that touches files, processes or the network, and \
imports beyond Blender's modelling modules and basic maths. Do not try to work around it.
"""

mcp = FastMCP("ref2blender", instructions=INSTRUCTIONS)


def _call(payload):
    """Ask Blender; a failure becomes a ToolError so its message reaches the model
    (other exceptions are reported by the SDK without their text)."""
    try:
        return call(payload)
    except BridgeError as exc:
        raise ToolError(str(exc)) from None


@mcp.tool()
def blender_status() -> str:
    """Check that Blender is open and connected. Call this first."""
    reply = _call({"cmd": "ping"})
    return (f"Blender {reply['blender']} is connected. Collection: {reply['collection']}. "
            f"Available render views: {', '.join(reply['views'])}.")


@mcp.tool()
def execute_blender_code(code: str) -> str:
    """Run Python code in the user's Blender session (main thread, Object Mode).

    Returns what the code printed, the traceback if it raised, and a summary of the
    objects in the Ref2Blender collection with dimensions and triangle counts.
    The namespace persists between calls.
    """
    reply = _call({"cmd": "exec", "code": code})
    text = (reply["output"] or "(no output)") + "\n\n" + reply["summary"]
    if not reply["ok"]:
        raise ToolError(text)
    return text


@mcp.tool()
def render_views(views: list[View], size: int = 768, asset: Optional[str] = None) -> list:
    """Render the model, auto-framed, and return one image per view.

    front/back/left/right/top/bottom are orthographic (front = camera on -Y looking
    towards +Y; right = camera on +X). three_quarter is a perspective view from the
    front-right and above; three_quarter_back from the back-left and above. Only the
    Ref2Blender collection is shown. Pass `asset` to render a single asset sub-collection.
    """
    reply = _call({"cmd": "render", "views": list(views), "size": size, "asset": asset})
    content = []
    for item in reply["renders"]:
        content.append(f"Render: {item['view']}")
        content.append(Image(data=base64.standard_b64decode(item["png"]), format="png"))
    return content


@mcp.tool()
def scene_summary() -> str:
    """List the objects in the Ref2Blender collection with dimensions and triangle counts."""
    return _call({"cmd": "summary"})["summary"]


@mcp.tool()
def check_model(triangle_budget: int = 0, asset: Optional[str] = None) -> str:
    """Game-readiness report: triangles (against the budget, if given), materials, missing
    UVs, n-gons, and warnings to fix before the model is finished."""
    return _call({"cmd": "check", "budget": triangle_budget, "asset": asset})["report"]


def _export_options(engine, lods, collision, palette, pivot, separate):
    return {"engine": engine, "lods": lods, "collision": collision, "palette": palette,
            "pivot": pivot, "join": not separate}


@mcp.tool()
def export_model(path: str, engine: Engine = "unity", lods: int = 0, collision: bool = False,
                 palette: bool = False, asset: Optional[str] = None, pivot: Pivot = "bottom",
                 separate: bool = False) -> str:
    """Export the model for a game engine. Writes a file on the user's disk: only call it
    when the user asked for an export, with an absolute path they chose.

    The scene is not modified (the export works on copies): modifiers are applied, parts
    without UVs are unwrapped, everything is joined into one mesh named after the file
    (`separate` keeps the parts), and the pivot goes to the bottom centre. The extension is
    set by the engine: FBX for unity and unreal, GLB for godot and generic.

    lods: extra detail levels, each with half the triangles (0-4).
    collision: add a convex collision mesh named the way the engine expects.
    palette: replace all materials with one material and a small colour-swatch PNG
        written next to the model (colour only).
    """
    reply = _call({"cmd": "export", "path": os.path.abspath(path), "asset": asset,
                  **_export_options(engine, lods, collision, palette, pivot, separate)})
    return reply["text"]


@mcp.tool()
def export_all_assets(folder: str, engine: Engine = "unity", lods: int = 0,
                      collision: bool = False, palette: bool = False,
                      pivot: Pivot = "bottom") -> str:
    """Export every asset sub-collection to its own file in `folder`, each pivoted on its
    own base. Same options and same caution as export_model."""
    reply = _call({"cmd": "export_all", "path": os.path.abspath(folder),
                  **_export_options(engine, lods, collision, palette, pivot, False)})
    return reply["text"]


@mcp.tool()
def clear_model() -> str:
    """Delete everything in the Ref2Blender collection and reset the Python namespace.
    Destructive: use it only to start a new model, when the user agrees to drop the current one."""
    reply = _call({"cmd": "clear"})
    return f"Removed {reply['removed']} objects."


def main(argv=None):
    parser = argparse.ArgumentParser(prog="ref2blender-mcp", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")
    install = sub.add_parser("install-addon", help="copy the add-on into Blender's add-ons folder")
    install.add_argument("--blender-version", help="for example 4.5 (default: every version found)")
    args = parser.parse_args(argv)

    if args.command == "install-addon":
        from .install import install_addon

        sys.exit(install_addon(args.blender_version))
    mcp.run()


if __name__ == "__main__":
    main()
