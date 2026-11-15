"""Run with:  blender --background --python tests/install_and_check.py

Enables the installed add-on, installs the Claude library, and makes one real
SDK call with an invalid key to prove the request is accepted by the SDK and
reaches the API (expected outcome: an authentication error).
"""

import time

import bpy

bpy.ops.preferences.addon_enable(module="ref2blender")
import ref2blender
from ref2blender import agent

if not ref2blender.sdk_available():
    print("installing SDK:", bpy.ops.r2b.install_deps())
assert ref2blender.sdk_available(), "SDK missing after install"
bpy.ops.wm.save_userpref()

import anthropic

print("anthropic", anthropic.__version__)
client = anthropic.Anthropic(api_key="sk-ant-invalid-smoke-test", max_retries=0)
agent.start(client, "claude-opus-5-5", "high", 3,
            [{"type": "text", "text": "ping"}], new_conversation=True)
deadline = time.time() + 60
while agent.SESSION.running and time.time() < deadline:
    time.sleep(0.1)
    agent._tick()
print("FINAL STATUS:", agent.SESSION.status)
