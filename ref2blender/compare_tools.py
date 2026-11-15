"""Lay the model's outline over a reference image and measure how well they match.

The model is rendered with a transparent background from the given view or camera, scaled and
moved so its outline sits on the subject of the reference, and drawn over it. When the
reference has a plain or transparent background its own outline is extracted too, which
gives a score (overlap of the two outlines) and a picture of the differences.
"""

import contextlib
import math
import os
import tempfile

import bpy
import numpy as np

from . import scene_tools

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".tif", ".tiff"}
MASK_SIDE = 384          # the reference outline is extracted at this size (longer side)
TOLERANCE = 0.10         # how far a pixel may be from the background colour
MODEL_OUTLINE = (1.0, 0.0, 1.0)
REFERENCE_OUTLINE = (0.0, 0.9, 0.3)
ROWS, COLUMNS = ("top", "middle", "bottom"), ("left", "centre", "right")


def check_request(path, view=None, camera=None, box=None, size=scene_tools.RENDER_SIZE):
    """Validate the inputs of compare_reference; returns (path, camera, box) normalised
    or raises ValueError."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("'image' must be the path of a reference image.")
    path = bpy.path.abspath(path)
    if os.path.splitext(path)[1].lower() not in IMAGE_EXTENSIONS:
        raise ValueError(f"'image' must be an image file ({', '.join(sorted(IMAGE_EXTENSIONS))}).")
    if not os.path.isfile(path):
        raise ValueError(f"No such image: {path}")
    if (view is None) == (camera is None):
        raise ValueError("Give either 'view' or 'camera' (one of them).")
    if view is not None and view not in scene_tools.VIEWS:
        raise ValueError(f"'view' must be one of {list(scene_tools.VIEWS)}.")
    if camera is not None:
        camera = scene_tools.check_cameras([camera])[0]
    if box is not None:
        ok = (isinstance(box, (list, tuple)) and len(box) == 4
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box))
        if not ok or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1):
            raise ValueError("'box' must be [left, top, right, bottom] as fractions of the image, "
                             "measured from its top-left corner.")
        box = [float(v) for v in box]
    if isinstance(size, bool) or not isinstance(size, int) or not 128 <= size <= 2048:
        raise ValueError("'size' must be an integer between 128 and 2048.")
    return path, camera, box


# --------------------------------------------------------------------- pixels

def _load(path, long_side=None, exact=None):
    """Read an image as a float array (height, width, 4), top row first."""
    img = bpy.data.images.load(path, check_existing=False)
    try:
        w, h = img.size
        if not w or not h:
            raise ValueError(f"Could not read the image: {path}")
        original = (w, h)
        if exact:
            target = exact
        else:
            scale = long_side / max(w, h)
            target = (max(8, round(w * scale)), max(8, round(h * scale)))
        if target != (w, h):
            img.scale(*target)
        px = np.empty(target[0] * target[1] * 4, dtype=np.float32)
        img.pixels.foreach_get(px)
    finally:
        bpy.data.images.remove(img)
    return px.reshape(target[1], target[0], 4)[::-1].copy(), original


def _png(rgb):
    h, w = rgb.shape[:2]
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[..., :3] = np.clip(rgb, 0, 1)
    img = bpy.data.images.new("R2B_Compare", w, h, alpha=False)
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="ref2blender_")
    os.close(fd)
    try:
        img.pixels.foreach_set(rgba[::-1].ravel())
        img.file_format = "PNG"
        img.filepath_raw = tmp
        img.save()
        with open(tmp, "rb") as f:
            return f.read()
    finally:
        bpy.data.images.remove(img)
        with contextlib.suppress(OSError):
            os.remove(tmp)


def _flood_from_border(similar):
    """The part of `similar` connected to the image border."""
    filled = np.zeros_like(similar)
    filled[0], filled[-1] = similar[0], similar[-1]
    filled[:, 0], filled[:, -1] = similar[:, 0], similar[:, -1]
    count = -1
    while True:
        grown = filled.copy()
        grown[1:] |= filled[:-1]
        grown[:-1] |= filled[1:]
        grown[:, 1:] |= filled[:, :-1]
        grown[:, :-1] |= filled[:, 1:]
        grown &= similar
        total = int(grown.sum())
        if total == count:
            return grown
        filled, count = grown, total


def reference_mask(px):
    """Outline of the subject of a reference: (mask, how) or (None, why not)."""
    step = max(1, math.ceil(max(px.shape[:2]) / MASK_SIDE))
    small = px[::step, ::step]
    if (small[..., 3] < 0.5).mean() > 0.02:
        mask, how = small[..., 3] >= 0.5, "its transparent background"
    else:
        rgb = small[..., :3]
        border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
        background = np.median(border, axis=0)
        if (np.abs(border - background).max(axis=1) < TOLERANCE).mean() < 0.85:
            return None, "its background is not plain"
        mask = ~_flood_from_border(np.abs(rgb - background).max(axis=2) < TOLERANCE)
        how = "its plain background"
    if not 0.01 < mask.mean() < 0.95:
        return None, "no subject stands out from its background"
    mask = np.repeat(np.repeat(mask, step, axis=0), step, axis=1)
    return mask[:px.shape[0], :px.shape[1]], how


def _bbox(mask):
    """(left, top, right, bottom) in pixels, right and bottom exclusive."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if not len(rows):
        return None
    return cols[0], rows[0], cols[-1] + 1, rows[-1] + 1


def _edge(mask, width):
    inner = mask.copy()
    for _ in range(width):
        shrunk = inner.copy()
        shrunk[1:] &= inner[:-1]
        shrunk[:-1] &= inner[1:]
        shrunk[:, 1:] &= inner[:, :-1]
        shrunk[:, :-1] &= inner[:, 1:]
        shrunk[0] = shrunk[-1] = False
        shrunk[:, 0] = shrunk[:, -1] = False
        inner = shrunk
    return mask & ~inner


def _warp(render, source_box, target_box, shape):
    """Scale (uniformly) and move `render` so source_box lands centred on target_box."""
    sw, sh = source_box[2] - source_box[0], source_box[3] - source_box[1]
    tw, th = target_box[2] - target_box[0], target_box[3] - target_box[1]
    scale = math.sqrt((tw / sw) * (th / sh))
    scx, scy = (source_box[0] + source_box[2]) / 2, (source_box[1] + source_box[3]) / 2
    tcx, tcy = (target_box[0] + target_box[2]) / 2, (target_box[1] + target_box[3]) / 2
    ys, xs = np.mgrid[0:shape[0], 0:shape[1]]
    sx = np.floor((xs + 0.5 - tcx) / scale + scx).astype(np.int64)
    sy = np.floor((ys + 0.5 - tcy) / scale + scy).astype(np.int64)
    valid = (sx >= 0) & (sx < render.shape[1]) & (sy >= 0) & (sy < render.shape[0])
    out = np.zeros((shape[0], shape[1], 4), dtype=np.float32)
    out[valid] = render[sy[valid], sx[valid]]
    return out


# ------------------------------------------------------------------- compare

def _regions(missing, extra, box, area):
    """The parts of a 3x3 grid over `box` where the outlines differ most."""
    left, top, right, bottom = box
    xs = [left + round((right - left) * i / 3) for i in range(4)]
    ys = [top + round((bottom - top) * i / 3) for i in range(4)]
    found = []
    for r, row in enumerate(ROWS):
        for c, col in enumerate(COLUMNS):
            where = (slice(ys[r], ys[r + 1]), slice(xs[c], xs[c + 1]))
            name = "centre" if (row, col) == ("middle", "centre") else f"{row} {col}"
            for kind, mask in (("missing", missing), ("extra", extra)):
                share = 100 * mask[where].sum() / area
                if share >= 0.5:
                    found.append((share, f"{name}: {share:.1f}% {kind}"))
    return [text for _, text in sorted(found, reverse=True)[:4]]


def compare_reference(path, view=None, camera=None, box=None, size=scene_tools.RENDER_SIZE, asset=None):
    """Returns {"text", "iou" (None without a score), "images": [(name, png_bytes), ...]}."""
    path, camera, box = check_request(path, view, camera, box, size)
    photo, (photo_w, photo_h) = _load(path, long_side=size)
    height, width = photo.shape[:2]

    if camera is not None:
        camera = dict(camera, aspect=min(2.5, max(0.4, width / height)))
        name, png = scene_tools.render_views([], size, asset, [camera], transparent=True)[0]
    else:
        name, png = scene_tools.render_views([view], size, asset, transparent=True)[0]
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="ref2blender_")
    os.close(fd)
    try:
        with open(tmp, "wb") as f:
            f.write(png)
        render, _ = _load(tmp, long_side=size)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp)
    source_box = _bbox(render[..., 3] > 0.5)
    if source_box is None:
        raise RuntimeError("The model does not show in the render.")

    mask, how = reference_mask(photo)
    lines = [f"Reference: {os.path.basename(path)} ({photo_w}x{photo_h}), model seen from "
             + (f"the {view} view." if view else
                f"azimuth {camera['azimuth']:g}, elevation {camera['elevation']:g}, "
                + (f"lens {camera['lens']:g} mm." if camera["lens"] else "orthographic."))]
    if box is not None:
        target_box = (box[0] * width, box[1] * height, box[2] * width, box[3] * height)
    elif mask is not None:
        target_box = _bbox(mask)
    else:
        raise ValueError(
            f"The subject's outline cannot be found in this image ({how}). Pass 'box' = "
            "[left, top, right, bottom] of the subject, as fractions of the image measured "
            "from its top-left corner, to get the overlay without a score.")

    warped = _warp(render, source_box, target_box, (height, width))
    model = warped[..., 3] > 0.5
    thickness = max(1, round(size / 400))

    overlay = photo[..., :3].copy()
    overlay[model] = overlay[model] * 0.6 + warped[..., :3][model] * 0.4
    if mask is not None:
        overlay[_edge(mask, thickness)] = REFERENCE_OUTLINE
    overlay[_edge(model, thickness)] = MODEL_OUTLINE
    images = [("overlay", _png(overlay))]

    iou = None
    if mask is None:
        lines.append(f"No score: {how}, so only the overlay is returned (magenta = the model's "
                     "outline, placed on the box you gave). Judge the match by eye.")
    else:
        area = int(mask.sum())
        missing, extra = mask & ~model, model & ~mask
        iou = float((mask & model).sum() / max(1, (mask | model).sum()))
        lines.append(f"Outline overlap: {iou * 100:.1f}% (the reference outline comes from {how}). "
                     f"Missing from the model: {100 * missing.sum() / area:.1f}% of the reference "
                     f"outline; extra: {100 * extra.sum() / area:.1f}%.")
        rb, mb = _bbox(mask), _bbox(model)
        ratio = ((mb[2] - mb[0]) / (mb[3] - mb[1])) / ((rb[2] - rb[0]) / (rb[3] - rb[1]))
        if abs(ratio - 1) < 0.02:
            lines.append("Proportion: the width-to-height ratio of the two outlines matches.")
        elif ratio > 1:
            lines.append(f"Proportion: the model's outline is {(ratio - 1) * 100:.0f}% too wide for "
                         "its height (or too low for its width).")
        else:
            lines.append(f"Proportion: the model's outline is {(1 / ratio - 1) * 100:.0f}% too tall "
                         "for its width (or too short for its height).")
        union = (min(rb[0], mb[0]), min(rb[1], mb[1]), max(rb[2], mb[2]), max(rb[3], mb[3]))
        regions = _regions(missing, extra, union, area)
        if regions:
            lines.append("Largest differences, by part of the picture: " + "; ".join(regions) + ".")
        difference = np.full((height, width, 3), 0.13, dtype=np.float32)
        difference[mask & model] = (0.82, 0.82, 0.82)
        difference[missing] = (0.92, 0.2, 0.2)
        difference[extra] = (0.2, 0.45, 1.0)
        images.append(("difference", _png(difference)))
        lines.append("Images: overlay (magenta = model outline, green = reference outline) and "
                     "difference (red = in the reference only, blue = in the model only). The "
                     "score assumes the camera matches the picture; shadows and reflections "
                     "joined to the subject count as part of the reference outline.")
    return {"text": "\n".join(lines), "iou": iou, "images": images}
