"""Run with:  blender --background --factory-startup --python tests/bridge_host.py -- <stop_file>

Hosts the bridge in a headless Blender for tests. Timers do not fire in
background mode, so this pumps the bridge by hand until <stop_file> appears.
"""

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import ref2blender  # noqa: E402
from ref2blender import bridge  # noqa: E402

stop_file = sys.argv[sys.argv.index("--") + 1]
ref2blender.register()
bridge.start()
print("BRIDGE READY", flush=True)
deadline = time.time() + 600
while not os.path.exists(stop_file) and time.time() < deadline:
    bridge._pump()
    time.sleep(0.02)
bridge.stop()
ref2blender.unregister()
