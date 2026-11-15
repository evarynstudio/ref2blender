"""End-to-end test of the MCP server against a headless Blender.

    python tests/mcp_test.py "<path to blender executable>"

Needs the package installed in the current environment (pip install -e .).
Starts Blender with the bridge, launches the MCP server over stdio exactly as an
MCP client would, and drives a full model -> render -> check -> export round.
"""

import asyncio
import os
import subprocess
import sys
import tempfile
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INFO = os.path.join(os.path.expanduser("~"), ".ref2blender", "bridge.json")

CODE = """
bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=1.0, vertices=16, location=(0, 0, 0.5))
barrel = bpy.context.object
barrel.name = "Barrel"
barrel.data.materials.append(make_material("Wood", (0.4, 0.25, 0.1)))
print("made", barrel.name)
"""


def failed(result):  # mcp 2.x renamed isError to is_error
    return result.is_error if hasattr(result, "is_error") else result.isError


def mime_of(image):
    return image.mime_type if hasattr(image, "mime_type") else image.mimeType


def text_of(result):
    return "\n".join(c.text for c in result.content if c.type == "text")


async def run(tmp):
    params = StdioServerParameters(command=sys.executable, args=["-m", "ref2blender_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            assert "render_views" in (init.instructions or ""), "server instructions missing"
            tools = {t.name for t in (await session.list_tools()).tools}
            expected = {"blender_status", "execute_blender_code", "render_views", "scene_summary",
                        "check_model", "export_model", "export_all_assets", "clear_model"}
            assert tools == expected, tools ^ expected

            status = await session.call_tool("blender_status", {})
            assert not failed(status) and "is connected" in text_of(status), text_of(status)
            print(text_of(status))

            built = await session.call_tool("execute_blender_code", {"code": CODE})
            assert not failed(built) and "made Barrel" in text_of(built), text_of(built)

            broken = await session.call_tool("execute_blender_code", {"code": "raise ValueError('boom')"})
            assert failed(broken) and "boom" in text_of(broken)

            unsafe = await session.call_tool("execute_blender_code", {"code": "import os\nos.listdir('.')"})
            assert failed(unsafe) and "safe mode" in text_of(unsafe), text_of(unsafe)

            renders = await session.call_tool("render_views", {"views": ["front", "three_quarter"], "size": 256})
            images = [c for c in renders.content if c.type == "image"]
            assert not failed(renders) and len(images) == 2 and mime_of(images[0]) == "image/png"
            print("render_views returned", len(images), "images,", len(images[0].data), "base64 chars")

            bad = await session.call_tool("render_views", {"views": ["sideways"]})
            assert failed(bad)

            report = await session.call_tool("check_model", {"triangle_budget": 10})
            assert "OVER budget" in text_of(report), text_of(report)

            out = os.path.join(tmp, "Barrel")
            exported = await session.call_tool(
                "export_model", {"path": out, "engine": "unity", "lods": 1, "collision": True, "palette": True})
            assert not failed(exported), text_of(exported)
            print(text_of(exported))
            assert os.path.isfile(out + ".fbx") and os.path.isfile(out + "_palette.png")

            cleared = await session.call_tool("clear_model", {})
            assert "Removed 1" in text_of(cleared), text_of(cleared)
            summary = await session.call_tool("scene_summary", {})
            assert "no geometry" in text_of(summary)


async def run_disconnected():
    params = StdioServerParameters(command=sys.executable, args=["-m", "ref2blender_mcp.server"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            status = await session.call_tool("blender_status", {})
            assert failed(status) and "not connected" in text_of(status), text_of(status)
            print("disconnected message:", text_of(status)[:70], "...")


def main():
    blender = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        stop = os.path.join(tmp, "stop")
        if os.path.exists(INFO):
            sys.exit("A Blender bridge is already running; close it before running this test.")
        asyncio.run(run_disconnected())
        host = subprocess.Popen(
            [blender, "--background", "--factory-startup", "--python",
             os.path.join(ROOT, "tests", "bridge_host.py"), "--", stop],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.time() + 90
            while not os.path.exists(INFO):
                if time.time() > deadline or host.poll() is not None:
                    sys.exit("Blender did not start the bridge.")
                time.sleep(0.2)
            asyncio.run(run(tmp))
        finally:
            open(stop, "w").close()
            host.wait(timeout=30)
    print("MCP OK")


if __name__ == "__main__":
    main()
