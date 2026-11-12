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
    roofs, pads, clearings = [], [], []
    if args.spec:
        spec = json.load(open(args.spec))
        roofs, pads = spec_footprints(spec)
        # ground cleared since the survey (new buildings, yards, paving): no trees there
        clearings = [Polygon(c).buffer(0) for c in spec.get("site", {}).get("clearings", [])]
    trees, _ = find_trees(p, dem, exclude=[r for r, _ in roofs] + clearings)
    if args.cir:
        trees = classify_conifers(trees, args.cir, args.ortho_extent)
    dem = apply_pads(dem, pads)
    zmin = float(np.floor(dem.min() * 10) / 10)
    q = np.clip(np.round((dem - zmin) / 0.001), 0, 65535).astype("<u2")
    q.tofile(os.path.join(args.out_dir, "terrain.bin"))
    trees.astype("<f4").tofile(os.path.join(args.out_dir, "trees.bin"))
    rec, pmeta = pack_points(p)
    rec.tofile(os.path.join(args.out_dir, "points.bin"))
    ortho = None
    epochs = []
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
                      "z_range": [float(dem.min()), float(dem.max())]}, indent=1))


if __name__ == "__main__":
    main()
