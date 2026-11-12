#!/usr/bin/env python3
"""Download the LiDAR returns around the site from the USGS 3DEP public bucket.

  python3 tools/fetch_lidar.py [--dataset IN_Statewide_B1_2017] [--radius 260] [--out data/lidar]

The point clouds are published as Entwine Point Tiles (EPT): an octree of LAZ files in
EPSG:3857. Every node at every depth holds distinct points, so all nodes that overlap the
query box are read, not only the leaves. Output: <out>/<dataset>_raw3857.npz with X, Y
(EPSG:3857), Z (metres, NAVD88) and the usual LAS attributes - the input of build_site.py.

Datasets covering 3910 W Dupont Rd:
  IN_Statewide_B1_2017                      flown 2017-03-12 and 2017-03-23, about 4 returns/m2
  USGS_LPC_IN_ET_B5_Allen_2012__LAS_2016    flown 2011-04-02, about 1 return/m2
"""
from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor

import laspy                      # pip install "laspy[lazrs]"
import numpy as np
import requests
from pyproj import Transformer

BUCKET = "https://s3-us-west-2.amazonaws.com/usgs-lidar-public"
SITE_LON, SITE_LAT = -85.1948761, 41.1791121
SESSION = requests.Session()


def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, timeout=120)
            if r.status_code == 200:
                return r.content
            last = f"HTTP {r.status_code}"
            if r.status_code in (403, 404):
                break
        except requests.RequestException as e:
            last = repr(e)
        time.sleep(1.5 * (i + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def node_bounds(b, d, x, y, z):
    n = 2 ** d
    sx, sy, sz = (b[3] - b[0]) / n, (b[4] - b[1]) / n, (b[5] - b[2]) / n
    return (b[0] + x * sx, b[1] + y * sy, b[2] + z * sz, b[0] + (x + 1) * sx, b[1] + (y + 1) * sy, b[2] + (z + 1) * sz)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="IN_Statewide_B1_2017")
    ap.add_argument("--radius", type=float, default=260.0, help="half-size of the square to fetch, true metres")
    ap.add_argument("--out", default="data/lidar")
    args = ap.parse_args()
    root = f"{BUCKET}/{args.dataset}"
    cache = os.path.join(args.out, "cache", args.dataset)
    os.makedirs(cache, exist_ok=True)

    ept = json.loads(get(root + "/ept.json"))
    assert ept["dataType"] == "laszip", ept["dataType"]
    b = ept["bounds"]
    cx, cy = Transformer.from_crs(4326, 3857, always_xy=True).transform(SITE_LON, SITE_LAT)
    k = 1.0 / np.cos(np.radians(SITE_LAT))            # web-mercator stretches distances by this
    half = (args.radius + 15.0) * k
    q = (cx - half, cy - half, cx + half, cy + half)

    nodes, pending = {}, ["0-0-0-0"]
    while pending:
        hk = pending.pop()
        hpath = os.path.join(cache, f"hier_{hk}.json")
        if os.path.exists(hpath):
            h = json.load(open(hpath))
        else:
            h = json.loads(get(f"{root}/ept-hierarchy/{hk}.json"))
            json.dump(h, open(hpath, "w"))
        stack = [hk]
        while stack:
            key = stack.pop()
            if key not in h:
                continue
            d, x, y, z = map(int, key.split("-"))
            nb = node_bounds(b, d, x, y, z)
            if nb[3] < q[0] or nb[0] > q[2] or nb[4] < q[1] or nb[1] > q[3]:
                continue
            if h[key] == -1:                          # subtree continues in another hierarchy file
                if key != hk:
                    pending.append(key)
                continue
            nodes[key] = h[key]
            stack += [f"{d + 1}-{2 * x + dx}-{2 * y + dy}-{2 * z + dz}" for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)]
    print(f"{len(nodes)} octree nodes overlap the query box")

    def fetch(key):
        p = os.path.join(cache, key + ".laz")
        if not os.path.exists(p) or os.path.getsize(p) == 0:
            data = get(f"{root}/ept-data/{key}.laz")
            with open(p + ".part", "wb") as f:
                f.write(data)
            os.replace(p + ".part", p)
        return key, p

    with ThreadPoolExecutor(8) as ex:
        paths = dict(ex.map(fetch, sorted(nodes)))

    fields = {}
    for key in sorted(nodes, key=lambda s: tuple(map(int, s.split("-")))):
        las = laspy.read(paths[key])
        X, Y = np.asarray(las.x, dtype=np.float64), np.asarray(las.y, dtype=np.float64)
        m = (X >= q[0]) & (X <= q[2]) & (Y >= q[1]) & (Y <= q[3])
        if not m.any():
            continue
        rec = {"X": X[m], "Y": Y[m], "Z": np.asarray(las.z, dtype=np.float64)[m]}
        names = set(las.point_format.dimension_names)
        for name, dtype in (("intensity", np.uint16), ("return_number", np.uint8), ("number_of_returns", np.uint8),
                            ("classification", np.uint8), ("point_source_id", np.uint16), ("gps_time", np.float64),
                            ("withheld", np.uint8)):
            if name in names:
                rec[name] = np.asarray(las[name])[m].astype(dtype)
        for kf, v in rec.items():
            fields.setdefault(kf, []).append(v)
    out = {kf: np.concatenate(v) for kf, v in fields.items()}
    path = os.path.join(args.out, f"{args.dataset}_raw3857.npz")
    np.savez_compressed(path, **out)
    cls, cnt = np.unique(out["classification"], return_counts=True)
    print(f"{len(out['X'])} returns -> {path}; classes {dict(zip(cls.tolist(), cnt.tolist()))}; "
          f"Z {out['Z'].min():.2f}..{out['Z'].max():.2f} m")


if __name__ == "__main__":
    main()
