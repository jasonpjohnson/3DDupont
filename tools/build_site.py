#!/usr/bin/env python3
"""Build the site assets (terrain, trees, LiDAR display points) from the USGS 3DEP
point cloud.

Input : raw EPT extract written by tools/fetch_lidar.py (EPSG:3857 X/Y, Z in metres)
Output: assets/terrain.bin   uint16 heights, row 0 = southern-most row
        assets/trees.bin     float32 [x, y, ground_z, height, crown_radius, kind] per tree
        assets/points.bin    quantised display point cloud (see POINT_DTYPE)
        assets/ortho.jpg     aerial photo cropped to the terrain extent, buildings painted out
        assets/site_layers.json  metadata for the files above

Optional inputs: --spec (building footprints: kept out of the tree layer, painted out of the
photo, and used to level the terrain under slabs), --ortho / --cir (north-up images on the
site grid covering ORTHO_EXTENT).

Site frame: EPSG:26916 metres, x = E - E0, y = N - N0, z = NAVD88 metres.
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np
from pyproj import Transformer
from scipy import ndimage
from scipy.interpolate import LinearNDInterpolator, NearestNDInterpolator
from scipy.spatial import cKDTree
from shapely.geometry import Point, Polygon
from shapely.prepared import prep

E0, N0 = 651405.348, 4560211.139
EXTENT = (-200.0, -230.0, 110.0, 170.0)          # xmin, ymin, xmax, ymax of the terrain
CELL = 1.0

POINT_DTYPE = np.dtype([("x", "<u2"), ("y", "<u2"), ("z", "<u2"), ("cls", "u1"), ("inten", "u1")])


def load_local(raw_npz):
    d = np.load(raw_npz)
    t = Transformer.from_crs(3857, 26916, always_xy=True)
    e, n = t.transform(d["X"], d["Y"])
    out = {k: d[k] for k in d.files if k not in ("X", "Y", "Z")}
    out["x"] = e - E0
    out["y"] = n - N0
    out["z"] = d["Z"].astype(np.float64)
    keep = (out["classification"] != 7) & (out.get("withheld", np.zeros(len(e), np.uint8)) == 0)
    return {k: v[keep] for k, v in out.items()}


def grid_ground(p, extent=EXTENT, cell=CELL):
    xmin, ymin, xmax, ymax = extent
    nx = int(round((xmax - xmin) / cell)) + 1
    ny = int(round((ymax - ymin) / cell)) + 1
    g = p["classification"] == 2
    gx, gy, gz = p["x"][g], p["y"][g], p["z"][g]
    m = (gx > xmin - 5) & (gx < xmax + 5) & (gy > ymin - 5) & (gy < ymax + 5)
    gx, gy, gz = gx[m], gy[m], gz[m]
    # node-centred grid: per-node median of ground returns within half a cell
    ix = np.round((gx - xmin) / cell).astype(int)
    iy = np.round((gy - ymin) / cell).astype(int)
    ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
    flat = iy[ok] * nx + ix[ok]
    order = np.argsort(flat, kind="stable")
    fs, zs = flat[order], gz[ok][order]
    uniq, start = np.unique(fs, return_index=True)
    med = np.array([np.median(c) for c in np.split(zs, start[1:])])
    dem = np.full(nx * ny, np.nan)
    dem[uniq] = med
    dem = dem.reshape(ny, nx)
    yy, xx = np.nonzero(~np.isnan(dem))
    pts = np.c_[xmin + xx * cell, ymin + yy * cell]
    vals = dem[yy, xx]
    X, Y = np.meshgrid(xmin + np.arange(nx) * cell, ymin + np.arange(ny) * cell)
    full = LinearNDInterpolator(pts, vals)(X, Y)
    bad = np.isnan(full)
    if bad.any():
        full[bad] = NearestNDInterpolator(pts, vals)(X[bad], Y[bad])
    # light smoothing: LiDAR ground noise is ~5 cm, keep real breaks
    full = 0.5 * full + 0.5 * ndimage.gaussian_filter(full, 0.8)
    return full, X, Y


def sample(dem, extent, cell, x, y):
    xmin, ymin, _, _ = extent
    fx = np.clip((np.asarray(x) - xmin) / cell, 0, dem.shape[1] - 1.001)
    fy = np.clip((np.asarray(y) - ymin) / cell, 0, dem.shape[0] - 1.001)
    ix, iy = fx.astype(int), fy.astype(int)
    tx, ty = fx - ix, fy - iy
    return (dem[iy, ix] * (1 - tx) * (1 - ty) + dem[iy, ix + 1] * tx * (1 - ty)
            + dem[iy + 1, ix] * (1 - tx) * ty + dem[iy + 1, ix + 1] * tx * ty)


def find_trees(p, dem, extent=EXTENT, cell=CELL, exclude=(), chm_cell=0.5, min_h=4.0):
    """Tree tops = local maxima of a smoothed canopy-height model built from
    non-ground returns, outside building polygons."""
    xmin, ymin, xmax, ymax = extent
    veg = p["classification"] != 2
    x, y, z = p["x"][veg], p["y"][veg], p["z"][veg]
    inb = (x > xmin) & (x < xmax) & (y > ymin) & (y < ymax)
    x, y, z = x[inb], y[inb], z[inb]
    hag = z - sample(dem, extent, cell, x, y)
    keep = hag > 2.0
    if exclude:
        prepared = [prep(poly.buffer(1.0)) for poly in exclude]
        boxes = [poly.buffer(1.0).bounds for poly in exclude]
        mask = np.zeros(len(x), dtype=bool)
        for pr, (bx0, by0, bx1, by1) in zip(prepared, boxes):
            cand = np.nonzero((x >= bx0) & (x <= bx1) & (y >= by0) & (y <= by1))[0]
            hit = [i for i in cand if pr.contains(Point(x[i], y[i]))]
            mask[hit] = True
        keep &= ~mask
    x, y, hag = x[keep], y[keep], hag[keep]
    nx = int((xmax - xmin) / chm_cell) + 1
    ny = int((ymax - ymin) / chm_cell) + 1
    chm = np.zeros((ny, nx), dtype=np.float32)
    ix = ((x - xmin) / chm_cell).astype(int)
    iy = ((y - ymin) / chm_cell).astype(int)
    np.maximum.at(chm, (iy, ix), hag.astype(np.float32))
    filled = ndimage.grey_closing(chm, size=(3, 3))
    sm = ndimage.gaussian_filter(filled, 1.6)
    # variable window: taller trees have wider crowns
    peaks = []
    mx5 = ndimage.maximum_filter(sm, size=9)       # 4.5 m window
    mx9 = ndimage.maximum_filter(sm, size=15)      # 7.5 m window
    cand = np.argwhere((sm >= min_h) & (sm == mx5))
    for j, i in cand:
        h = float(filled[max(j - 2, 0):j + 3, max(i - 2, 0):i + 3].max())
        if h >= 14.0 and sm[j, i] < mx9[j, i]:
            continue                                  # not the dominant top of a big crown
        peaks.append((xmin + (i + 0.5) * chm_cell, ymin + (j + 0.5) * chm_cell, h))
    peaks = np.array(peaks, dtype=float) if peaks else np.zeros((0, 3))
    if len(peaks) == 0:
        return peaks, chm
    # crown radius from local canopy extent: distance to nearest other top, capped by height
    tree = cKDTree(peaks[:, :2])
    dd, _ = tree.query(peaks[:, :2], k=2)
    radius = np.clip(dd[:, 1] * 0.55, 1.2, None)
    radius = np.minimum(radius, np.clip(peaks[:, 2] * 0.33, 1.5, 7.0))
    gz = sample(dem, extent, cell, peaks[:, 0], peaks[:, 1])
    out = np.c_[peaks[:, 0], peaks[:, 1], gz, peaks[:, 2], radius, np.zeros(len(peaks))]
    return out, chm


def pack_points(p, extent=EXTENT, near=(0.0, 12.0), near_r=70.0, far_keep=0.4, seed=7):
    xmin, ymin, xmax, ymax = extent
    x, y, z = p["x"], p["y"], p["z"]
    inb = (x >= xmin) & (x <= xmax) & (y >= ymin) & (y <= ymax)
    r = np.hypot(x - near[0], y - near[1])
    rng = np.random.default_rng(seed)
    keep = inb & ((r < near_r) | (rng.random(len(x)) < far_keep))
    x, y, z = x[keep], y[keep], z[keep]
    zmin = float(np.floor(z.min()))
    rec = np.zeros(len(x), dtype=POINT_DTYPE)
    rec["x"] = np.clip(np.round((x - xmin) / 0.01), 0, 65535)
    rec["y"] = np.clip(np.round((y - ymin) / 0.01), 0, 65535)
    rec["z"] = np.clip(np.round((z - zmin) / 0.001), 0, 65535)
    rec["cls"] = p["classification"][keep]
    inten = p["intensity"][keep].astype(np.float64)
    hi = np.percentile(inten, 99) or 1.0
    rec["inten"] = np.clip(inten / hi * 255, 0, 255)
    meta = {"count": int(len(rec)), "origin": [xmin, ymin, zmin], "scale": [0.01, 0.01, 0.001],
            "record": "x:u16 y:u16 z:u16 cls:u8 intensity:u8 (little endian, 8 bytes)"}
    return rec, meta


def spec_footprints(spec):
    """(roof outlines, pads) from the building spec.
    roofs = [(polygon, since_year)] union of eaves per building; pads = [(polygon, z)] areas
    where the terrain must not rise above a slab or floor."""
    from shapely.ops import unary_union
    roofs, pads = [], []
    for b in spec.get("buildings", []):
        polys = []
        for w in b.get("wings", []):
            if w.get("footprint"):
                fp = Polygon(w["footprint"]).buffer(0)
                polys.append(fp.buffer(w.get("overhang", 0.4), join_style=2))
                pads.append((fp, float(w.get("base_z", spec["z0"]))))
            if w.get("eave"):
                polys.append(Polygon(w["eave"]).buffer(0))
        for el in b.get("elements", []):
            if el.get("kind") == "prism" and el.get("pad", True) and el["z1"] - el["z0"] < 1.5:
                pads.append((Polygon(el["polygon"]).buffer(0), float(el["z1"]) - 0.03))
        if polys:
            u = unary_union(polys)
            for g in (u.geoms if hasattr(u, "geoms") else [u]):
                roofs.append((g, int(b.get("since", 0))))
    return roofs, pads


def rasterize(polys, extent, mpp, shape):
    """Boolean mask (rows north to south) of polygons on a north-up grid."""
    import cv2
    xmin, _, _, ymax = extent
    mask = np.zeros(shape, dtype=np.uint8)
    for poly in polys:
        for g in (poly.geoms if hasattr(poly, "geoms") else [poly]):
            ring = np.asarray(g.exterior.coords)
            px = np.c_[(ring[:, 0] - xmin) / mpp, (ymax - ring[:, 1]) / mpp].round().astype(np.int32)
            cv2.fillPoly(mask, [px], 1)
    return mask.astype(bool)


def apply_pads(dem, pads, extent=EXTENT, cell=CELL):
    """Lower the terrain to at most ``z`` inside each pad polygon (never raises it)."""
    xmin, ymin, _, _ = extent
    ny, nx = dem.shape
    out = dem.copy()
    for poly, z in pads:
        if poly.is_empty:
            continue
        grown = poly.buffer(0.6)
        bx0, by0, bx1, by1 = grown.bounds
        i0, i1 = max(int((bx0 - xmin) / cell), 0), min(int((bx1 - xmin) / cell) + 2, nx)
        j0, j1 = max(int((by0 - ymin) / cell), 0), min(int((by1 - ymin) / cell) + 2, ny)
        pr = prep(grown)
        for j in range(j0, j1):
            for i in range(i0, i1):
                if out[j, i] > z and pr.contains(Point(xmin + i * cell, ymin + j * cell)):
                    out[j, i] = z
    return out


def apply_grading(dem, gradings, extent=EXTENT, cell=CELL):
    """Level the terrain to ``z`` inside each polygon (cut and fill), blending back to the
    surveyed ground over ``feather`` metres outside it. For ground reshaped after the survey."""
    xmin, ymin, _, _ = extent
    ny, nx = dem.shape
    out = dem.copy()
    for g in gradings:
        poly = Polygon(g["polygon"]).buffer(0)
        z = float(g["z"])
        feather = float(g.get("feather", 3.0))
        bx0, by0, bx1, by1 = poly.buffer(feather).bounds
        i0, i1 = max(int((bx0 - xmin) / cell), 0), min(int((bx1 - xmin) / cell) + 2, nx)
        j0, j1 = max(int((by0 - ymin) / cell), 0), min(int((by1 - ymin) / cell) + 2, ny)
        for j in range(j0, j1):
            for i in range(i0, i1):
                d = poly.distance(Point(xmin + i * cell, ymin + j * cell))
                if d >= feather:
                    continue
                w = 1.0 if d <= 0 else 0.5 * (1 + np.cos(np.pi * d / feather))
                out[j, i] = w * z + (1 - w) * out[j, i]
    return out


def tri_z(dem, extent, cell, x, y):
    """Ground height on the SAME triangulation the viewer draws (cell split a-b-c / b-d-c)."""
    xmin, ymin, _, _ = extent
    fx = min(max((x - xmin) / cell, 0.0), dem.shape[1] - 1.000001)
    fy = min(max((y - ymin) / cell, 0.0), dem.shape[0] - 1.000001)
    i, j = int(fx), int(fy)
    tx, ty = fx - i, fy - j
    za, zb, zc, zd = dem[j, i], dem[j, i + 1], dem[j + 1, i], dem[j + 1, i + 1]
    if tx + ty <= 1.0:
        return za + tx * (zb - za) + ty * (zc - za)
    return zd + (1 - tx) * (zc - zd) + (1 - ty) * (zb - zd)


SURFACE_MATERIALS = {
    "asphalt": dict(color="#2e2f31", texture="granular", texture_args=dict(grain=0.05, blotch=0.03), roughness=0.92, label="Asphalt"),
    "asphalt_old": dict(color="#414244", texture="granular", texture_args=dict(grain=0.06, blotch=0.05), roughness=0.95, label="Asphalt (older)"),
    "gravel": dict(color="#a29f98", texture="granular", texture_args=dict(grain=0.13, blotch=0.04, speck=0.12, tile_m=2.0), roughness=1.0, label="Crushed stone"),
    "concrete": dict(color="#cfcbc4", texture="concrete", roughness=0.9, label="Concrete"),
    "mulch": dict(color="#5a4332", texture="granular", texture_args=dict(grain=0.16, blotch=0.06, tile_m=2.0), roughness=1.0, label="Mulch"),
    "river_rock": dict(color="#9a948a", texture="granular", texture_args=dict(grain=0.16, blotch=0.03, speck=0.15, tile_m=1.5), roughness=1.0, label="River rock"),
    "lawn": dict(color="#4f6a33", texture="granular", texture_args=dict(grain=0.09, blotch=0.07, tile_m=4.0), roughness=1.0, label="Lawn"),
    "fence": dict(color="#9a6a3f", texture="pickets", roughness=0.9, double_sided=True, label="Cedar privacy fence"),
    "fence_post": dict(color="#7d5733", roughness=0.9, label="Fence post"),
    "fence_stain": dict(color="#4a2b20", texture="pickets", roughness=0.9, double_sided=True, label="Stained cedar privacy fence"),
    "fence_stain_post": dict(color="#3a2119", roughness=0.9, label="Fence post"),
    "concrete_old": dict(color="#b3a9a1", texture="concrete", roughness=0.95, label="Concrete (weathered)"),
}


def drape(part, poly, dem, extent, cell, lift, uv_scale):
    """Add ``poly`` to ``part`` cut along the terrain's own triangles, so it lies exactly on the ground."""
    from build_model import plane_polygon
    xmin, ymin, _, _ = extent
    bx0, by0, bx1, by1 = poly.bounds
    i0, i1 = max(int((bx0 - xmin) / cell), 0), min(int((bx1 - xmin) / cell) + 1, dem.shape[1] - 1)
    j0, j1 = max(int((by0 - ymin) / cell), 0), min(int((by1 - ymin) / cell) + 1, dem.shape[0] - 1)
    pp = prep(poly)
    for j in range(j0, j1):
        for i in range(i0, i1):
            x0, y0 = xmin + i * cell, ymin + j * cell
            a, b, c, d = (x0, y0), (x0 + cell, y0), (x0, y0 + cell), (x0 + cell, y0 + cell)
            za, zb, zc, zd = dem[j, i], dem[j, i + 1], dem[j + 1, i], dem[j + 1, i + 1]
            for tri, (p0, z0_), (p1, z1_), (p2, z2_) in ((Polygon([a, b, c]), (a, za), (b, zb), (c, zc)),
                                                         (Polygon([b, d, c]), (b, zb), (d, zd), (c, zc))):
                if not pp.intersects(tri):
                    continue
                piece = tri if pp.contains(tri) else poly.intersection(tri)
                if piece.is_empty or piece.area < 1e-6:
                    continue
                # plane through the three corners: z = A x + B y + D
                m = np.array([[p0[0], p0[1], 1.0], [p1[0], p1[1], 1.0], [p2[0], p2[1], 1.0]])
                A, B, D = np.linalg.solve(m, np.array([z0_, z1_, z2_]))
                plane_polygon(part, piece, (A, B, D), dz=lift, uv_scale=uv_scale, world_uv=True)


def build_surfaces(spec, dem, out_dir, extent=EXTENT, cell=CELL):
    """Ground surfaces and fences that postdate (or are missing from) the aerial photo.
    One GLB per 'since' year so the viewer can hide them for older photographs."""
    import textures
    from meshkit import Model
    site = spec.get("site", {})
    items = [dict(s, _type="surface") for s in site.get("surfaces", [])] + [dict(f, _type="fence") for f in site.get("fences", [])]
    for stale in [f for f in os.listdir(out_dir) if f.startswith("surfaces_") and f.endswith(".glb")]:
        os.remove(os.path.join(out_dir, stale))
    models, parts_info = [], {}
    for year in sorted({int(it.get("since", 0)) for it in items}):
        model = Model(z0=spec["z0"])
        for name, m in SURFACE_MATERIALS.items():
            tex, size = (textures.make(m["texture"], m["color"], **m.get("texture_args", {})) if m.get("texture") else (None, 1.0))
            model.material(name, color=m["color"], roughness=m.get("roughness", 0.9), texture=tex, tex_size_m=size,
                           double_sided=m.get("double_sided", False))
        for k, it in enumerate(x for x in items if int(x.get("since", 0)) == year):
            base = (it.get("id") or f"{it['_type']}_{year}_{k}").lower()
            info = {key: it.get(key) for key in ("label", "desc", "dims", "evidence", "source", "confidence") if it.get(key)}
            info["building"] = f"surfaces_{year}"
            if it["_type"] == "surface":
                kind = it.get("kind", "asphalt")
                poly = Polygon(it["polygon"], it.get("holes")).buffer(0)
                part = model.part(base, kind)
                # each kind sits at its own height so overlapping surfaces never z-fight
                lift = {"lawn": 0.015, "asphalt_old": 0.02, "asphalt": 0.03, "mulch": 0.035, "gravel": 0.04,
                        "river_rock": 0.045, "concrete": 0.055, "concrete_old": 0.055}.get(kind, 0.03)
                drape(part, poly, dem, extent, cell, float(it.get("lift", lift)), model.uv_scale(kind))
                parts_info[base] = {**info, "material": SURFACE_MATERIALS[kind]["label"]}
            else:
                h = float(it.get("height", 1.8))
                path = np.asarray(it["path"], dtype=float)
                fmat = it.get("material", "fence")                      # "fence" (natural cedar) or "fence_stain"
                part = model.part(base, fmat)
                posts = model.part(base + "_posts", fmat + "_post" if fmat + "_post" in SURFACE_MATERIALS else "fence_post")
                uv = model.uv_scale(fmat)
                run = 0.0
                for a, b in zip(path[:-1], path[1:]):
                    seg = float(np.hypot(*(b - a)))
                    n = max(1, int(np.ceil(seg / 0.6)))
                    for q in range(n):
                        p0, p1 = a + (b - a) * q / n, a + (b - a) * (q + 1) / n
                        g0, g1 = tri_z(dem, extent, cell, *p0), tri_z(dem, extent, cell, *p1)
                        quad = [[p0[0], p0[1], g0 + 0.04], [p1[0], p1[1], g1 + 0.04], [p1[0], p1[1], g1 + h], [p0[0], p0[1], g0 + h]]
                        d = (p1 - p0) / max(np.hypot(*(p1 - p0)), 1e-9)
                        s0, s1 = run + seg * q / n, run + seg * (q + 1) / n
                        part.add(quad, (d[1], -d[0], 0.0), np.array([[s0, 0], [s1, 0], [s1, h], [s0, h]]) * uv, [[0, 1, 2], [0, 2, 3]])
                    for q in range(int(seg // 2.4) + 1):
                        c = a + (b - a) * min(q * 2.4 / seg, 1.0)
                        g = tri_z(dem, extent, cell, *c)
                        posts.box((c[0], c[1], g + (h + 0.1) / 2), (0.1, 0.1, h + 0.1), np.degrees(np.arctan2(b[1] - a[1], b[0] - a[0])))
                    run += seg
                parts_info[base] = {**info, "material": SURFACE_MATERIALS[fmat]["label"]}
                parts_info[base + "_posts"] = {**info, "material": SURFACE_MATERIALS[fmat]["label"]}
        name = f"surfaces_{year}.glb"
        model.write_glb(os.path.join(out_dir, name))
        models.append({"id": f"surfaces_{year}", "file": name, "label": f"Site works ({year})" if year else "Site works",
                       "since": year, "layer": "sitework", "quiet": True})
    json.dump({"models": models, "parts": parts_info}, open(os.path.join(out_dir, "surfaces.json"), "w"), indent=1)
    return models


def build_ortho(ortho_path, out_path, roofs, ortho_extent, extent=EXTENT, quality=84, max_width=None):
    """Crop a north-up site-grid image to the terrain extent and paint out the buildings
    (their roofs would otherwise show on the ground around the 3D model)."""
    import cv2
    img = cv2.imread(ortho_path, cv2.IMREAD_COLOR)
    h, w = img.shape[:2]
    oxmin, oymin, oxmax, oymax = ortho_extent
    mpp = (oxmax - oxmin) / w
    xmin, ymin, xmax, ymax = extent
    c0, c1 = int(round((xmin - oxmin) / mpp)), int(round((xmax - oxmin) / mpp))
    r0, r1 = int(round((oymax - ymax) / mpp)), int(round((oymax - ymin) / mpp))
    img = img[r0:r1, c0:c1].copy()
    if roofs:
        grown = [r.buffer(1.4) for r in roofs]
        mask = rasterize(grown, extent, mpp, img.shape[:2]).astype(np.uint8)
        small = cv2.resize(img, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA)
        msmall = cv2.resize(mask, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST)
        fill = cv2.inpaint(small, msmall * 255, 5, cv2.INPAINT_TELEA)
        fill = cv2.resize(fill, (img.shape[1], img.shape[0]), interpolation=cv2.INTER_CUBIC)
        soft = cv2.GaussianBlur(mask.astype(np.float32), (0, 0), 3)[:, :, None]
        img = (img * (1 - soft) + fill * soft).astype(np.uint8)
    if max_width and img.shape[1] > max_width:
        k = max_width / img.shape[1]
        img = cv2.resize(img, (max_width, int(round(img.shape[0] * k))), interpolation=cv2.INTER_AREA)
        mpp /= k
    cv2.imwrite(out_path, img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return {"file": os.path.basename(out_path), "width": int(img.shape[1]), "height": int(img.shape[0]), "mpp": round(mpp, 4)}


def classify_conifers(trees, cir_path, ortho_extent, ndvi_min=0.27):
    """Leaf-off colour-infrared (NIR, R, G): evergreen crowns stay bright in NIR.
    Sets kind = 1 for trees whose crown is green in a leaf-off image."""
    import cv2
    cir = cv2.imread(cir_path, cv2.IMREAD_COLOR)            # BGR order = (G, R, NIR)
    if cir is None or not len(trees):
        return trees
    nir = cir[:, :, 2].astype(np.float32)
    red = cir[:, :, 1].astype(np.float32)
    ndvi = (nir - red) / np.maximum(nir + red, 1)
    oxmin, _, oxmax, oymax = ortho_extent
    mpp = (oxmax - oxmin) / cir.shape[1]
    out = trees.copy()
    for k, (x, y, gz, hgt, r, _) in enumerate(trees):
        c, rr = int((x - oxmin) / mpp), int((oymax - y) / mpp)
        w = max(2, int(min(r, 2.5) * 0.6 / mpp))
        patch = ndvi[max(rr - w, 0):rr + w + 1, max(c - w, 0):c + w + 1]
        if patch.size and float(np.median(patch)) > ndvi_min:
            out[k, 5] = 1.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw_npz")
    ap.add_argument("out_dir")
    ap.add_argument("--spec", help="site_spec.json (building footprints)")
    ap.add_argument("--ortho", help="north-up RGB image on the site grid")
    ap.add_argument("--cir", help="north-up colour-infrared image (NIR, R, G) on the same grid")
    ap.add_argument("--ortho-extent", nargs=4, type=float, default=[-245.0, -230.0, 155.0, 170.0],
                    metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    ap.add_argument("--ortho-credit", default="")
    ap.add_argument("--ortho-year", type=int, default=2024)
    ap.add_argument("--ortho-date", default="")
    ap.add_argument("--history", help='JSON file: [{"year": 2018, "path": "north-up image on the same extent", "date": "..."}] older photos')
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    p = load_local(args.raw_npz)
    dem, X, Y = grid_ground(p)
    roofs, pads, clearings, spec = [], [], [], None
    if args.spec:
        spec = json.load(open(args.spec))
        roofs, pads = spec_footprints(spec)
        site = spec.get("site", {})
        # ground cleared or rebuilt since the survey (new buildings, yards, paving): no trees there
        clearings = [Polygon(c).buffer(0) for c in site.get("clearings", [])]
        clearings += [Polygon(s_["polygon"]).buffer(0) for s_ in site.get("surfaces", []) if s_.get("kind") != "mulch"]
        clearings += [Polygon(g["polygon"]).buffer(0) for g in site.get("grading", [])]
        # trees known to stand inside a cleared area today (a specimen tree in the lawn): [x, y] or [x, y, radius]
        from shapely.geometry import Point
        for kt in site.get("keep_trees", []):
            disc = Point(kt[0], kt[1]).buffer((kt[2] if len(kt) > 2 else 3.0) + 1.0)     # + the 1 m margin find_trees adds
            clearings = [c.difference(disc) for c in clearings]
        clearings = [c for c in clearings if not c.is_empty]
        dem = apply_grading(dem, site.get("grading", []))
    trees, _ = find_trees(p, dem, exclude=[r for r, _ in roofs] + clearings)
    if args.cir:
        trees = classify_conifers(trees, args.cir, args.ortho_extent)
    dem = apply_pads(dem, pads)
    surface_models = build_surfaces(spec, dem, args.out_dir) if spec else []
    zmin = float(np.floor(dem.min() * 10) / 10)
    q = np.clip(np.round((dem - zmin) / 0.001), 0, 65535).astype("<u2")
    q.tofile(os.path.join(args.out_dir, "terrain.bin"))
    trees.astype("<f4").tofile(os.path.join(args.out_dir, "trees.bin"))
    rec, pmeta = pack_points(p)
    rec.tofile(os.path.join(args.out_dir, "points.bin"))
    ortho = None
    epochs = []
    prev_path = os.path.join(args.out_dir, "site_layers.json")
    if not args.ortho and os.path.exists(prev_path):          # keep the committed photographs
        prev = json.load(open(prev_path))
        ortho, epochs = prev.get("ortho"), prev.get("ortho_epochs", [])
    if args.ortho:
        standing = [r for r, since in roofs if since <= args.ortho_year]
        ortho = build_ortho(args.ortho, os.path.join(args.out_dir, "ortho.jpg"), standing, args.ortho_extent)
        ortho.update(credit=args.ortho_credit, year=args.ortho_year, date=args.ortho_date)
        epochs.append({"year": args.ortho_year, "file": "ortho.jpg", "date": args.ortho_date})
    if args.history:
        for h in json.load(open(args.history)):
            standing = [r for r, since in roofs if since <= h["year"]]
            name = f"ortho_{h['year']}.jpg"
            build_ortho(h["path"], os.path.join(args.out_dir, name), standing, h.get("extent", args.ortho_extent),
                        quality=80, max_width=h.get("max_width", 1240))
            epochs.append({"year": h["year"], "file": name, "date": h.get("date", "")})
    epochs.sort(key=lambda e: e["year"])
    meta = {
        "frame": {"epsg": 26916, "E0": E0, "N0": N0, "note": "x east, y north, z NAVD88 metres"},
        "terrain": {"file": "terrain.bin", "nx": int(dem.shape[1]), "ny": int(dem.shape[0]),
                    "x0": EXTENT[0], "y0": EXTENT[1], "cell": CELL, "zmin": zmin, "zscale": 0.001,
                    "dtype": "uint16", "rows": "south to north"},
        "trees": {"file": "trees.bin", "count": int(len(trees)), "conifers": int((trees[:, 5] > 0.5).sum()) if len(trees) else 0,
                  "record": "float32 x, y, ground_z, height, crown_radius, kind (0 deciduous, 1 conifer)"},
        "ortho": ortho,
        "ortho_epochs": epochs,
        "points": {"file": "points.bin", **pmeta},
        "source": "USGS 3DEP IN_Statewide_B1_2017 (flown 2017-03-12 and 2017-03-23), public domain",
    }
    json.dump(meta, open(os.path.join(args.out_dir, "site_layers.json"), "w"), indent=1)
    print(json.dumps({"terrain": meta["terrain"], "trees": meta["trees"], "points": pmeta["count"], "ortho": ortho,
                      "surface_models": [m["file"] for m in surface_models],
                      "z_range": [float(dem.min()), float(dem.max())]}, indent=1))


if __name__ == "__main__":
    main()
