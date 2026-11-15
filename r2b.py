"""Command-line client for the Ref2Blender bridge (standard library only).

    python r2b.py ping
    python r2b.py exec script.py        run a Python file inside Blender ("-" reads stdin)
    python r2b.py render front right three_quarter [--size 768]
    python r2b.py render --camera 35,8,50,1.5   free camera: azimuth,elevation[,lens[,aspect]]
                         (azimuth 0 = front, 90 = right side; lens 0 = orthographic;
                          aspect = width/height; repeat --camera for more; saved as camera_N.png)
    python r2b.py compare photo.jpg --camera 35,8,50   outline of the model over a reference,
                         with a score when its background is plain (or --view right for a
                         drawing; --box L,T,R,B = the subject, as fractions of the image)
    python r2b.py script                print the model script stored in the .blend file
    python r2b.py summary
    python r2b.py clear                 empty the Ref2Blender collection
    python r2b.py check [--budget 2000] game-readiness report (triangles, materials, UVs)
    python r2b.py export Assets/Models/Chair --engine unity|unreal|godot|generic
                         [--lods 2] [--collision] [--palette] [--asset NAME]
                         [--pivot bottom|center|origin] [--separate] [--triangulate]
    python r2b.py export-all Assets/Models  one file per asset sub-collection (same options)

render and check also take --asset NAME.

Renders are saved as PNG files in the "renders" folder next to this script.
"""

import argparse
import base64
import json
import os
import socket
import sys

INFO_PATH = os.path.join(os.path.expanduser("~"), ".ref2blender", "bridge.json")
RENDER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders")
NOT_RUNNING = ('The bridge is not running. In Blender: sidebar (N) > Ref2Blender > '
               '"Connect".')


def call(payload, timeout=630):
    try:
        with open(INFO_PATH, encoding="utf-8") as f:
            info = json.load(f)
    except OSError:
        sys.exit(NOT_RUNNING)
    payload["token"] = info["token"]
    try:
        with socket.create_connection(("127.0.0.1", info["port"]), timeout=5) as sock:
            sock.settimeout(timeout)
            with sock.makefile("rwb") as stream:
                stream.write(json.dumps(payload).encode("utf-8") + b"\n")
                stream.flush()
                line = stream.readline()
    except OSError:
        sys.exit(NOT_RUNNING)
    if not line:
        sys.exit("Blender closed the connection without answering.")
    return json.loads(line.decode("utf-8"))


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ping")
    p_exec = sub.add_parser("exec")
    p_exec.add_argument("file")
    p_render = sub.add_parser("render")
    p_render.add_argument("views", nargs="*")
    p_render.add_argument("--camera", action="append", default=[], metavar="AZ,EL[,LENS[,ASPECT]]")
    p_render.add_argument("--size", type=int, default=768)
    p_compare = sub.add_parser("compare")
    p_compare.add_argument("image")
    p_compare.add_argument("--view")
    p_compare.add_argument("--camera", metavar="AZ,EL[,LENS]")
    p_compare.add_argument("--box", metavar="LEFT,TOP,RIGHT,BOTTOM")
    p_compare.add_argument("--size", type=int, default=768)
    p_compare.add_argument("--asset")
    sub.add_parser("script")
    sub.add_parser("summary")
    sub.add_parser("clear")
    p_render.add_argument("--asset", help="render only this asset sub-collection")
    p_check = sub.add_parser("check")
    p_check.add_argument("--budget", type=int, default=0, help="triangle budget")
    p_check.add_argument("--asset")
    for name in ("export", "export-all"):
        p = sub.add_parser(name)
        p.add_argument("path", help="output file (export) or folder (export-all); the extension is set by the engine")
        p.add_argument("--engine", default="unity", choices=["unity", "unreal", "godot", "generic"])
        p.add_argument("--pivot", default="bottom", choices=["bottom", "center", "origin"])
        p.add_argument("--separate", action="store_true", help="keep the parts as separate objects")
        p.add_argument("--triangulate", action="store_true")
        p.add_argument("--lods", type=int, default=0, help="extra LOD levels (each halves the triangles)")
        p.add_argument("--collision", action="store_true", help="add a convex collision mesh")
        p.add_argument("--palette", action="store_true", help="one material + a colour-swatch texture")
        if name == "export":
            p.add_argument("--asset", help="export only this asset sub-collection")
    args = parser.parse_args()

    if args.cmd == "check":
        reply = call({"cmd": "check", "budget": args.budget, "asset": args.asset})
        if not reply["ok"]:
            sys.exit(reply["error"])
        print(reply["report"])
        return

    if args.cmd in ("export", "export-all"):
        reply = call({
            "cmd": args.cmd.replace("-", "_"), "path": os.path.abspath(args.path),
            "engine": args.engine, "pivot": args.pivot, "join": not args.separate,
            "triangulate": args.triangulate, "lods": args.lods, "collision": args.collision,
            "palette": args.palette, "asset": getattr(args, "asset", None),
        })
        if not reply["ok"]:
            sys.exit(reply["error"])
        print(reply["text"])
        return

    if args.cmd == "exec":
        if args.file == "-":
            code = sys.stdin.read()
        else:
            with open(args.file, encoding="utf-8") as f:
                code = f.read()
        reply = call({"cmd": "exec", "code": code})
        if "error" in reply:
            sys.exit(reply["error"])
        print("OK" if reply["ok"] else "ERROR")
        if reply["output"]:
            print(reply["output"])
        print()
        print(reply["summary"])
        sys.exit(0 if reply["ok"] else 1)

    if args.cmd == "compare":
        payload = {"cmd": "compare", "image": os.path.abspath(args.image), "view": args.view,
                   "size": args.size, "asset": args.asset}
        try:
            if args.camera:
                values = [float(v) for v in args.camera.split(",")]
                if not 2 <= len(values) <= 3:
                    raise ValueError
                payload["camera"] = dict(zip(("azimuth", "elevation", "lens"), values))
            if args.box:
                payload["box"] = [float(v) for v in args.box.split(",")]
        except ValueError:
            sys.exit("--camera expects azimuth,elevation[,lens] and --box left,top,right,bottom")
        reply = call(payload)
        if not reply["ok"]:
            sys.exit(reply["error"])
        print(reply["text"])
        os.makedirs(RENDER_DIR, exist_ok=True)
        for item in reply["renders"]:
            path = os.path.join(RENDER_DIR, f"compare_{item['view']}.png")
            with open(path, "wb") as f:
                f.write(base64.standard_b64decode(item["png"]))
            print(path)
        return

    if args.cmd == "script":
        reply = call({"cmd": "script"})
        if not reply["ok"]:
            sys.exit(reply["error"])
        for item in reply["scripts"]:
            print(f"# ==== {item['name']} ====")
            print(item["code"])
        if not reply["scripts"]:
            print("No model script is stored in this Blender file.")
        return

    if args.cmd == "render":
        cameras = []
        for text in args.camera:
            try:
                values = [float(v) for v in text.split(",")]
            except ValueError:
                sys.exit(f"--camera expects numbers: azimuth,elevation[,lens[,aspect]] (got {text!r})")
            if not 2 <= len(values) <= 4:
                sys.exit("--camera expects azimuth,elevation[,lens[,aspect]]")
            cameras.append(dict(zip(("azimuth", "elevation", "lens", "aspect"), values)))
        reply = call({"cmd": "render", "views": args.views, "size": args.size, "asset": args.asset,
                      "cameras": cameras})
        if not reply["ok"]:
            sys.exit(reply["error"])
        os.makedirs(RENDER_DIR, exist_ok=True)
        for item in reply["renders"]:
            path = os.path.join(RENDER_DIR, f"{item['view']}.png")
            with open(path, "wb") as f:
                f.write(base64.standard_b64decode(item["png"]))
            print(path)
        return

    reply = call({"cmd": args.cmd})
    if not reply["ok"]:
        sys.exit(reply["error"])
    if args.cmd == "ping":
        print(f"Blender {reply['blender']} connected. Views: {', '.join(reply['views'])}")
    elif args.cmd == "summary":
        print(reply["summary"])
    else:
        print(f"Removed {reply['removed']} objects.")


if __name__ == "__main__":
    main()
