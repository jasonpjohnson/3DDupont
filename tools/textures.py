"""Procedural, tileable material textures (PIL images). Each generator returns
(image, tile_size_m): the image spans tile_size_m metres in both directions.
Colours are passed in as sRGB hex so the spec controls them."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter


def _rgb(hex_color):
    h = hex_color.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def _noise(shape, rng, scale=1.0, blur=0.0):
    n = rng.standard_normal(shape).astype(np.float32)
    if blur:
        img = Image.fromarray(np.clip(n * 40 + 128, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(blur))
        n = (np.asarray(img, dtype=np.float32) - 128) / 40
    return n * scale


def _finish(base_rgb, shade, size):
    """shade: float array (h, w), 1.0 = base colour."""
    arr = np.clip(base_rgb[None, None, :] * shade[:, :, None], 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB").resize((size, size), Image.LANCZOS)


def lap_siding(color="#f1efe9", exposure_m=0.115, courses=8, size=512, seed=1):
    """Horizontal lap siding: each course has a thin shadow under the board above."""
    rng = np.random.default_rng(seed)
    n = 1024
    y = (np.arange(n) / n * courses) % 1.0            # 0 at bottom of a course -> 1 at top
    y = y[::-1]                                        # image row 0 is the top of the tile
    profile = 1.0 - 0.20 * np.exp(-((1.0 - y) / 0.045) ** 2) - 0.05 * (1.0 - y) + 0.035 * np.exp(-(y / 0.05) ** 2)
    shade = np.repeat(profile[:, None], n, axis=1)
    shade += _noise((n, n), rng, 0.012, 1.2) + _noise((n, 1), rng, 0.01) * np.ones((1, n), dtype=np.float32)
    return _finish(_rgb(color), shade, size), exposure_m * courses


def board_batten(color="#f1efe9", spacing_m=0.4, bays=4, size=512, seed=2):
    """Vertical board-and-batten: raised battens with a soft shadow on one side."""
    rng = np.random.default_rng(seed)
    n = 1024
    x = (np.arange(n) / n * bays) % 1.0
    batten = np.exp(-((x - 0.5) / 0.055) ** 8)
    shadow = np.exp(-((x - 0.5 - 0.085) / 0.03) ** 2)
    profile = 1.0 + 0.03 * batten - 0.17 * shadow * (1 - batten)
    shade = np.repeat(profile[None, :], n, axis=0)
    shade += _noise((n, n), rng, 0.012, 1.2)
    return _finish(_rgb(color), shade, size), spacing_m * bays


def shingles(color="#3b3d40", course_m=0.143, courses=14, size=1024, seed=3):
    """Architectural asphalt shingles: staggered tabs with granular colour variation."""
    rng = np.random.default_rng(seed)
    n = 1024
    tile = course_m * courses
    shade = np.ones((n, n), dtype=np.float32)
    ch = n // courses
    for c in range(courses):
        y0 = c * ch
        xs = 0
        offset = int(rng.integers(0, n))
        while xs < n:
            w = int(rng.integers(n // 9, n // 5))
            tone = 1.0 + float(rng.normal(0, 0.075))
            cols = (np.arange(xs, min(xs + w, n)) + offset) % n
            shade[y0:y0 + ch, cols] = tone
            shade[y0:y0 + ch, cols[:2]] *= 0.82                     # tab joint
            xs += w
        shade[y0:y0 + max(2, ch // 9), :] *= 0.74                   # shadow line under the course above
        shade[y0 + ch - max(1, ch // 14):y0 + ch, :] *= 1.06
    shade += _noise((n, n), rng, 0.055)
    return _finish(_rgb(color), shade, size), tile


def standing_seam(color="#8f959a", seam_m=0.41, seams=4, size=512, seed=4):
    """Standing-seam metal: narrow raised ribs running up the slope (image vertical)."""
    rng = np.random.default_rng(seed)
    n = 1024
    x = (np.arange(n) / n * seams) % 1.0
    rib = np.exp(-((x - 0.5) / 0.022) ** 2)
    shadow = np.exp(-((x - 0.5 - 0.04) / 0.02) ** 2)
    profile = 1.0 + 0.16 * rib - 0.22 * shadow
    shade = np.repeat(profile[None, :], n, axis=0)
    shade += _noise((n, n), rng, 0.01, 2.0)
    return _finish(_rgb(color), shade, size), seam_m * seams


def ledgestone(color="#8a8478", tile_m=1.2, size=512, seed=5):
    """Stacked ledgestone veneer: random courses of long thin stones, dark recessed joints."""
    rng = np.random.default_rng(seed)
    n = 1024
    base = _rgb(color)
    arr = np.zeros((n, n, 3), dtype=np.float32)
    y = 0
    while y < n:
        h = int(rng.integers(n // 22, n // 10))
        x = -int(rng.integers(0, n // 6))
        while x < n:
            w = int(rng.integers(n // 9, n // 3))
            tint = 1.0 + rng.normal(0, 0.2)                      # light grey to charcoal
            hue = base * tint + rng.normal(0, 2.5, 3)
            x0, x1 = max(x, 0), min(x + w, n)
            y1 = min(y + h, n)
            arr[y:y1, x0:x1] = hue
            arr[y:min(y + 3, n), x0:x1] *= 0.5                      # bed joint
            arr[y:y1, x0:min(x0 + 3, n)] *= 0.55                    # head joint
            arr[max(y1 - 2, y):y1, x0:x1] *= 1.08
            x += w
        y += h
    arr += _noise((n, n), rng, 9.0)[:, :, None]
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB").resize((size, size), Image.LANCZOS)
    return img, tile_m


def planks(color="#8c7a66", plank_m=0.14, count=6, size=512, seed=6, gap=0.05):
    """Deck boards running along the image u axis (horizontal lines)."""
    rng = np.random.default_rng(seed)
    n = 1024
    shade = np.ones((n, n), dtype=np.float32)
    ph = n // count
    for c in range(count):
        tone = 1.0 + float(rng.normal(0, 0.06))
        shade[c * ph:(c + 1) * ph, :] = tone
        shade[c * ph:c * ph + max(2, int(ph * gap)), :] *= 0.45
    grain = _noise((n, 1), rng, 0.03) * np.ones((1, n), dtype=np.float32)
    shade += grain + _noise((n, n), rng, 0.02, 0.6)
    return _finish(_rgb(color), shade, size), plank_m * count


def concrete(color="#c9c6be", tile_m=2.4, size=512, seed=7, joints=2):
    rng = np.random.default_rng(seed)
    n = 1024
    shade = np.ones((n, n), dtype=np.float32) + _noise((n, n), rng, 0.035, 6) + _noise((n, n), rng, 0.02)
    if joints:
        step = n // joints
        for k in range(joints):
            shade[k * step:k * step + 4, :] *= 0.78
            shade[:, k * step:k * step + 4] *= 0.78
    return _finish(_rgb(color), shade, size), tile_m


def ribbed_metal(color="#d9d9d4", rib_m=0.23, ribs=6, size=512, seed=8, horizontal=False):
    """Agricultural ribbed steel panel (pole-barn walls and roofs)."""
    rng = np.random.default_rng(seed)
    n = 1024
    x = (np.arange(n) / n * ribs) % 1.0
    rib = np.exp(-((x - 0.5) / 0.07) ** 4)
    shadow = np.exp(-((x - 0.5 - 0.1) / 0.035) ** 2)
    profile = 1.0 + 0.07 * rib - 0.16 * shadow * (1 - rib)
    shade = np.repeat(profile[None, :], n, axis=0) + _noise((n, n), rng, 0.008, 2.0)
    if horizontal:
        shade = shade.T
    return _finish(_rgb(color), shade, size), rib_m * ribs


def granular(color="#3a3b3d", tile_m=3.0, size=512, seed=10, grain=0.06, blotch=0.04, speck=0.0):
    """Asphalt, crushed stone, mulch: fine grain + soft blotches (+ light specks for stone)."""
    rng = np.random.default_rng(seed)
    n = 512
    shade = np.ones((n, n), dtype=np.float32) + _noise((n, n), rng, grain) + _noise((n, n), rng, blotch, 14)
    if speck:
        shade += (rng.random((n, n)) > 0.93).astype(np.float32) * speck
    return _finish(_rgb(color), shade, size), tile_m


def pickets(color="#9a6a3f", board_m=0.14, count=6, size=512, seed=11):
    """Vertical fence boards (planks turned on end)."""
    img, tile = planks(color=color, plank_m=board_m, count=count, size=size, seed=seed, gap=0.07)
    return img.transpose(Image.ROTATE_90), tile


def flat(color="#888888", size=64, grain=0.012, seed=9):
    rng = np.random.default_rng(seed)
    shade = np.ones((64, 64), dtype=np.float32) + _noise((64, 64), rng, grain)
    return _finish(_rgb(color), shade, size), 1.0


GENERATORS = {
    "lap_siding": lap_siding, "board_batten": board_batten, "shingles": shingles,
    "standing_seam": standing_seam, "ledgestone": ledgestone, "planks": planks,
    "concrete": concrete, "ribbed_metal": ribbed_metal, "flat": flat, "granular": granular, "pickets": pickets,
}


def make(kind, color, **kw):
    if kind not in GENERATORS:
        raise KeyError(f"unknown texture {kind!r}; choose from {sorted(GENERATORS)}")
    return GENERATORS[kind](color=color, **kw)
