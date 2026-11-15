"""Local bridge so Claude Code can drive this Blender session.

A TCP server on 127.0.0.1 takes one JSON request per connection. Requests are
queued and executed on Blender's main thread by a timer. A random token,
written to ~/.ref2blender/bridge.json, keeps other local programs out.
"""

import base64
import hmac
import json
import os
import queue
import secrets
import socket
import threading
import traceback

import bpy

from . import export_tools, scene_tools

DEFAULT_PORT = 9876
INFO_PATH = os.path.join(os.path.expanduser("~"), ".ref2blender", "bridge.json")
JOB_TIMEOUT = 600

_jobs = queue.Queue()
_state = {"sock": None, "token": "", "port": 0, "namespace": None, "requests": 0}


def is_running():
    return _state["sock"] is not None


def port():
    return _state["port"]


def request_count():
    return _state["requests"]


def start(port_number=DEFAULT_PORT):
    if is_running():
        return
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", port_number))
    sock.listen(4)
    _state.update(sock=sock, token=secrets.token_hex(16), port=port_number,
                  namespace=scene_tools.new_namespace(), requests=0)
    os.makedirs(os.path.dirname(INFO_PATH), exist_ok=True)
    with open(INFO_PATH, "w", encoding="utf-8") as f:
        json.dump({"port": port_number, "token": _state["token"]}, f)
    threading.Thread(target=_accept_loop, args=(sock,), daemon=True).start()
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.1, persistent=True)


def stop():
    sock, _state["sock"] = _state["sock"], None
    if sock is not None:
        try:
            sock.close()
        except OSError:
            pass
    try:
        os.remove(INFO_PATH)
    except OSError:
        pass


# --------------------------------------------------------------- socket thread

def _accept_loop(sock):
    while True:
        try:
            conn, _addr = sock.accept()
        except OSError:
            return  # socket closed by stop()
        threading.Thread(target=_serve, args=(conn,), daemon=True).start()


def _serve(conn):
    with conn:
        try:
            conn.settimeout(30)
            with conn.makefile("rwb") as stream:
                line = stream.readline()
                try:
                    request = json.loads(line.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError
                except ValueError:
                    response = {"ok": False, "error": "Malformed request."}
                else:
                    if not hmac.compare_digest(str(request.get("token", "")), _state["token"]):
                        response = {"ok": False, "error": "Bad token."}
                    else:
                        done = threading.Event()
                        holder = {}
                        _jobs.put((request, holder, done))
                        if done.wait(JOB_TIMEOUT):
                            response = holder["response"]
                        else:
                            response = {"ok": False, "error": "Blender did not answer in time."}
                conn.settimeout(None)
                stream.write(json.dumps(response).encode("utf-8") + b"\n")
                stream.flush()
        except OSError:
            pass


# ----------------------------------------------------------------- main thread

def _pump():
    while True:
        try:
            request, holder, done = _jobs.get_nowait()
        except queue.Empty:
            break
        try:
            holder["response"] = _handle(request)
        except Exception as exc:
            traceback.print_exc()
            holder["response"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        _state["requests"] += 1
        done.set()
    return 0.1 if is_running() else None


def _asset(request):
    asset = request.get("asset")
    return asset if isinstance(asset, str) and asset.strip() else None


def _handle(request):
    cmd = request.get("cmd")
    scene = bpy.context.scene

    if cmd == "ping":
        return {"ok": True, "blender": bpy.app.version_string,
                "collection": scene_tools.COLLECTION_NAME, "views": list(scene_tools.VIEWS)}

    if cmd == "exec":
        code = request.get("code")
        if not isinstance(code, str) or not code.strip():
            return {"ok": False, "error": "'code' must be a non-empty string."}
        ok, output = scene_tools.run_code(code, _state["namespace"])
        return {"ok": ok, "output": output, "summary": scene_tools.scene_summary(scene)}

    if cmd == "render":
        views = request.get("views")
        if (not isinstance(views, list) or not views
                or any(v not in scene_tools.VIEWS for v in views)):
            return {"ok": False, "error": f"'views' must be a non-empty list drawn from {list(scene_tools.VIEWS)}."}
        size = request.get("size", scene_tools.RENDER_SIZE)
        if not isinstance(size, int) or not 128 <= size <= 2048:
            return {"ok": False, "error": "'size' must be an integer between 128 and 2048."}
        renders = scene_tools.render_views(list(dict.fromkeys(views)), size, _asset(request))
        return {"ok": True, "renders": [
            {"view": name, "png": base64.standard_b64encode(png).decode("ascii")}
            for name, png in renders
        ]}

    if cmd == "summary":
        return {"ok": True, "summary": scene_tools.scene_summary(scene)}

    if cmd == "check":
        budget = request.get("budget", 0)
        if not isinstance(budget, int) or budget < 0:
            return {"ok": False, "error": "'budget' must be a non-negative integer."}
        return {"ok": True, "report": export_tools.game_report(scene, budget, _asset(request))}

    if cmd in ("export", "export_all"):
        path = request.get("path")
        if not isinstance(path, str) or not path.strip():
            return {"ok": False, "error": "'path' must be a non-empty string."}
        engine = request.get("engine", "unity")
        pivot = request.get("pivot", "bottom")
        lods = request.get("lods", 0)
        if engine not in export_tools.ENGINES:
            return {"ok": False, "error": f"'engine' must be one of {list(export_tools.ENGINES)}."}
        if pivot not in export_tools.PIVOTS:
            return {"ok": False, "error": f"'pivot' must be one of {list(export_tools.PIVOTS)}."}
        if not isinstance(lods, int) or not 0 <= lods <= export_tools.MAX_LODS:
            return {"ok": False, "error": f"'lods' must be an integer from 0 to {export_tools.MAX_LODS}."}
        options = dict(
            engine=engine, join=bool(request.get("join", True)), pivot=pivot,
            triangulate=bool(request.get("triangulate", False)), lods=lods,
            collision=bool(request.get("collision", False)), palette=bool(request.get("palette", False)),
        )
        if cmd == "export_all":
            infos = export_tools.export_all(path, **options)
        else:
            infos = [export_tools.export_model(path, asset=_asset(request), **options)]
        return {"ok": True, "infos": infos, "text": "\n".join(export_tools.describe(i) for i in infos)}

    if cmd == "clear":
        removed = scene_tools.clear_collection(scene)
        _state["namespace"] = scene_tools.new_namespace()
        return {"ok": True, "removed": removed}

    return {"ok": False, "error": f"Unknown command: {cmd!r}"}
