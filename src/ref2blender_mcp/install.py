"""Copies the Blender add-on into Blender's user add-ons folder."""

import os
import re
import shutil
import sys

ADDON_NAME = "ref2blender"
SKIP = shutil.ignore_patterns("__pycache__", "*.pyc", "blender_manifest.toml")


def addon_source():
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (
        os.path.join(here, "addon", ADDON_NAME),                     # installed package
        os.path.join(here, os.pardir, os.pardir, ADDON_NAME),         # source checkout
    ):
        if os.path.isfile(os.path.join(candidate, "__init__.py")):
            return os.path.abspath(candidate)
    return None


def blender_config_root():
    if sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), "Blender Foundation", "Blender")
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/Blender")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "blender")


def installed_versions(root):
    if not os.path.isdir(root):
        return []
    versions = [d for d in os.listdir(root)
                if re.fullmatch(r"\d+\.\d+", d) and os.path.isdir(os.path.join(root, d))]
    return sorted(versions, key=lambda v: tuple(map(int, v.split("."))))


def install_addon(version=None):
    source = addon_source()
    if source is None:
        print("The add-on files were not found in this installation.", file=sys.stderr)
        return 1
    root = blender_config_root()
    versions = [version] if version else [
        v for v in installed_versions(root) if tuple(map(int, v.split("."))) >= (4, 2)
    ]
    if not versions:
        print(f"No Blender 4.2+ settings folder found under {root}.\n"
              "Open Blender once so it creates one, or pass --blender-version X.Y.", file=sys.stderr)
        return 1
    for v in versions:
        target = os.path.join(root, v, "scripts", "addons", ADDON_NAME)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copytree(source, target, ignore=SKIP, dirs_exist_ok=True)
        print(f"Installed for Blender {v}: {target}")
    print('\nNow open Blender, go to Edit > Preferences > Add-ons, enable "Ref2Blender" '
          "(restart Blender if it was open), then press N in the 3D viewport, open the "
          '"Ref2Blender" tab and press "Connect".')
    return 0
