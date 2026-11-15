"""Talks to the Ref2Blender add-on's local bridge (one JSON request per connection)."""

import json
import os
import socket

INFO_PATH = os.path.join(os.path.expanduser("~"), ".ref2blender", "bridge.json")
NOT_RUNNING = (
    "Blender is not connected. Ask the user to open Blender, press N in the 3D viewport, "
    'open the "Ref2Blender" tab and press "Connect".'
)


class BridgeError(RuntimeError):
    pass


def call(payload, timeout=630):
    """Send one request to Blender and return its reply. Raises BridgeError with a
    message suitable for showing to the model."""
    try:
        with open(INFO_PATH, encoding="utf-8") as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise BridgeError(NOT_RUNNING) from None
    request = dict(payload, token=info.get("token", ""))
    try:
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=5) as sock:
            sock.settimeout(timeout)
            with sock.makefile("rwb") as stream:
                stream.write(json.dumps(request).encode("utf-8") + b"\n")
                stream.flush()
                line = stream.readline()
    except (OSError, KeyError, ValueError):
        raise BridgeError(NOT_RUNNING) from None
    if not line:
        raise BridgeError("Blender closed the connection without answering.")
    reply = json.loads(line.decode("utf-8"))
    if "error" in reply:
        raise BridgeError(reply["error"])
    return reply
