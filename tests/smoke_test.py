"""Run with:  blender --background --factory-startup --python tests/smoke_test.py

Exercises everything except a real Claude request: registration, code
execution, check renders, image encoding, and one full agent turn driven by a
fake API client.
"""

import os
import sys
import time
import types

import bpy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import agent, scene_tools  # noqa: E402

OUT = os.path.join(ROOT, "tests", "out")
os.makedirs(OUT, exist_ok=True)

ref2blender.register()
print("registered; SDK available:", ref2blender.sdk_available())

# --- code execution -----------------------------------------------------------
ns = scene_tools.new_namespace()
ok, out = scene_tools.run_code(
    """
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
body = bpy.context.object
body.name = "Body"
body.data.materials.append(make_material("Red", (0.8, 0.1, 0.1)))
mesh = bpy.data.meshes.new("Roof")
mesh.from_pydata([(-0.6,-0.6,1),(0.6,-0.6,1),(0.6,0.6,1),(-0.6,0.6,1),(0,0,1.6)], [],
                 [(0,1,4),(1,2,4),(2,3,4),(3,0,4),(0,3,2,1)])
roof = bpy.data.objects.new("Roof", mesh)
collection.objects.link(roof)
roof.data.materials.append(make_material("Blue", (0.1, 0.2, 0.8)))
print("built", len(collection.objects), "objects")
""",
    ns,
)
print("run_code ok:", ok, "|", out)
assert ok, out
assert "built 2 objects" in out
print(scene_tools.scene_summary(bpy.context.scene))

ok, out = scene_tools.run_code("raise ValueError('boom')", ns)
assert not ok and "ValueError: boom" in out

# --- renders ------------------------------------------------------------------
engine_before = bpy.context.scene.render.engine
renders = scene_tools.render_views(["front", "top", "three_quarter"])
for name, png in renders:
    assert png[:8] == b"\x89PNG\r\n\x1a\n", name
    with open(os.path.join(OUT, f"{name}.png"), "wb") as f:
        f.write(png)
    print("render", name, len(png), "bytes")
assert bpy.context.scene.render.engine == engine_before
assert "R2B_TempCam" not in bpy.data.objects

# --- image encoding -----------------------------------------------------------
media, data = scene_tools.encode_reference_image(os.path.join(OUT, "front.png"))
assert media == "image/png" and len(data) > 100
big = bpy.data.images.new("big", 2600, 1300)
big.filepath_raw = os.path.join(OUT, "big.bmp")
big.file_format = "BMP"
big.save()
media, data = scene_tools.encode_reference_image(big.filepath_raw)
assert media == "image/jpeg", media
print("image encoding ok")

# --- one agent loop with a fake client -----------------------------------------
def block(**kw):
    return types.SimpleNamespace(**kw)


SCRIPT = [
    [block(type="text", text="Construeixo una esfera."),
     block(type="tool_use", id="t1", name="execute_blender_python",
           input={"code": "bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0,0,1.9))"}),
     block(type="tool_use", id="t2", name="render_views", input={"views": ["front", "nope"]})],
    [block(type="tool_use", id="t3", name="render_views", input={"views": ["front"]})],
    [block(type="text", text="Model acabat.")],
]


class FakeStream:
    def __init__(self, kwargs):
        self.kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def close(self):
        pass

    def get_final_message(self):
        content = SCRIPT.pop(0)
        stop = "tool_use" if any(b.type == "tool_use" for b in content) else "end_turn"
        return block(content=content, stop_reason=stop,
                     usage=block(input_tokens=10, output_tokens=5))


fake = block(beta=block(messages=block(stream=lambda **kw: FakeStream(kw))))
content = agent.build_user_content([(os.path.join(OUT, "front.png"), "FRONT")], "una caseta")
assert [c["type"] for c in content] == ["text", "image", "text"]
agent.start(fake, "claude-opus-5-5", "high", 10, content, new_conversation=True)
deadline = time.time() + 60
while agent.SESSION.running and time.time() < deadline:
    time.sleep(0.05)
    agent._tick()  # timers do not fire in background mode
s = agent.SESSION
print("status:", s.status, "| steps:", s.steps, "| log:", s.log)
assert not s.running and s.status.startswith("Done"), s.status
assert s.steps == 3
results = s.messages[2]["content"]
assert not results[0].get("is_error") and results[1].get("is_error")
assert s.messages[4]["content"][0]["content"][1]["type"] == "image"
assert any(o.name.startswith("Sphere") for o in bpy.data.collections["Ref2Blender"].objects)

ref2blender.unregister()
print("ALL OK")
