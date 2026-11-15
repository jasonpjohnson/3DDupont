#!/usr/bin/env python3
"""Walkable-ground grid for the life-sim mode.

  python3 tools/build_nav.py spec/site_spec.json assets/

Writes <assets>/nav.json: a grid over the lived-in part of the site giving, for each cell,
the height a person stands at (terrain, or the top of a deck, porch, patio or pad, or a stair
ramp) and whether a person may stand there (not inside a building, a garden bed, a fence, a
railing or the hot tub). The game also refuses steps between neighbouring cells higher than
STEP, which is what keeps people off a deck except by its stairs.

Also lists every door with the point just outside it, the spot a person walks to before going
in, and checks that each of those points can be reached from the drive.
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import re
from collections import deque

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

CELL = 0.4
EXTENT = (-64.0, -46.0, 42.0, 66.0)          # x0, y0, x1, y1 (site metres)
STEP = 0.36                                   # tallest step a person takes between neighbouring cells
START = (0.6, -4.0)                           # on the front walk, where reachability is checked from
FLOOR = re.compile(r"(deck|pad|porch_floor|patio|slab)")
NOT_FLOOR = re.compile(r"(wall|transformer|bed|soffit|ceiling|skirt)")


def terrain(assets):
    t = json.load(open(os.path.join(assets, "site_layers.json")))["terrain"]
    raw = np.fromfile(os.path.join(assets, t["file"]), dtype="<u2").reshape(t["ny"], t["nx"])
    z = t["zmin"] + raw.astype(np.float64) * t["zscale"]

    def at(x, y):
        fx = np.clip((x - t["x0"]) / t["cell"], 0, t["nx"] - 1.001)
        fy = np.clip((y - t["y0"]) / t["cell"], 0, t["ny"] - 1.001)
        i, j = np.floor(fx).astype(int), np.floor(fy).astype(int)
        tx, ty = fx - i, fy - j
        return (z[j, i] * (1 - tx) * (1 - ty) + z[j, i + 1] * tx * (1 - ty)
                + z[j + 1, i] * (1 - tx) * ty + z[j + 1, i + 1] * tx * ty)
    return at


def poly_of(e):
    return Polygon(e["polygon"], e.get("holes")).buffer(0)


def box_poly(e, grow=0.0):
    cx, cy = e["center"][:2]
    sx, sy = e["size"][0] / 2 + grow, e["size"][1] / 2 + grow
    a = math.radians(e.get("yaw", 0))
    c, s = math.cos(a), math.sin(a)
    pts = [(cx + c * x - s * y, cy + s * x + c * y) for x, y in ((-sx, -sy), (sx, -sy), (sx, sy), (-sx, sy))]
    return Polygon(pts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("assets")
    a = ap.parse_args()
    spec = json.load(open(a.spec))
    ground = terrain(a.assets)
    x0, y0, x1, y1 = EXTENT
    nx, ny = int(round((x1 - x0) / CELL)), int(round((y1 - y0) / CELL))
    xs = x0 + (np.arange(nx) + 0.5) * CELL
    ys = y0 + (np.arange(ny) + 0.5) * CELL
    X, Y = np.meshgrid(xs, ys)
    Z = ground(X, Y)
    gy, gx = np.gradient(Z, CELL)
    steep = np.hypot(gx, gy) > 0.85              # bare ground steeper than about 40 degrees
    walk = np.ones((ny, nx), dtype=bool)

    def cells(geom):
        """(j, i) index arrays of cell centres inside geom."""
        if geom.is_empty:
            return np.zeros(0, int), np.zeros(0, int)
        bx0, by0, bx1, by1 = geom.bounds
        i0, i1 = max(int((bx0 - x0) / CELL) - 1, 0), min(int((bx1 - x0) / CELL) + 2, nx)
        j0, j1 = max(int((by0 - y0) / CELL) - 1, 0), min(int((by1 - y0) / CELL) + 2, ny)
        p = prep(geom)
        jj, ii = [], []
        for j in range(j0, j1):
            for i in range(i0, i1):
                if p.contains(Point(xs[i], ys[j])):
                    jj.append(j); ii.append(i)
        return np.array(jj, int), np.array(ii, int)

    # the game's (invented) main floor: its wings are walkable inside, walled at their edge
    inter = None
    ipath = os.path.join(a.assets, "interior.json")
    if os.path.exists(ipath):
        inter = json.load(open(ipath))
    room_polys = [Polygon(p) for r in (inter or {}).get("rooms", []) for p in r["polys"]]
    inside = unary_union(room_polys).buffer(0.02) if room_polys else None
    inside_core = prep(inside.buffer(-0.4)) if inside else None
    MAIN = {"link", "north_range", "north_bay", "core_west", "core_east", "east_wing", "b_gable", "c_gable"} if inside else set()

    def indoors(x, y):
        return inside_core is not None and inside_core.contains(Point(x, y))

    blocked = []
    floors = []
    for b in spec["buildings"]:
        for w in b.get("wings", []):
            if w.get("footprint") and not (b["id"] == "house" and w["id"] in MAIN):
                blocked.append(Polygon(w["footprint"]).buffer(0.25, join_style=2))
        for e in b.get("elements", []):
            k, eid = e["kind"], e["id"]
            ref = None
            for key in ("center", "base", "a"):
                if isinstance(e.get(key), list) and len(e[key]) >= 2 and not isinstance(e[key][0], list):
                    ref = e[key]; break
            if ref is None:
                for key in ("polygon", "path"):
                    if isinstance(e.get(key), list) and e[key] and isinstance(e[key][0], list):
                        ref = e[key][0]; break
            if ref is not None and indoors(ref[0], ref[1]):
                continue
            if k == "prism":
                if FLOOR.search(eid) and not NOT_FLOOR.search(eid):
                    floors.append((poly_of(e), float(e["z1"])))
                elif e["z1"] - e["z0"] > 0.15 and not re.search(r"(soffit|ceiling)", eid):
                    blocked.append(poly_of(e).buffer(0.15))
            elif k == "box":
                if e["size"][2] > 0.3 and e["center"][2] - e["size"][2] / 2 < ground(*e["center"][:2]) + 1.4:
                    blocked.append(box_poly(e, 0.2))
            elif k == "railing":
                blocked.append(LineString(e["path"]).buffer(0.2))
            elif k == "wall_panel" and "a" in e:
                top = e.get("z1") or max(p[1] for p in e.get("outline", [[0, 0]]))
                mid = ((e["a"][0] + e["b"][0]) / 2, (e["a"][1] + e["b"][1]) / 2)
                if top > ground(*mid) + 0.25 and not re.search(r"(gable|header|end_panel|door|foundation|skirt|lattice|lites)", eid):
                    blocked.append(LineString([e["a"], e["b"]]).buffer(max(e.get("thickness", 0.1), 0.1) / 2 + 0.2))
            elif k == "cylinder":
                bz = e["base"][2]
                if bz < ground(*e["base"][:2]) + 0.6:
                    blocked.append(Point(e["base"][:2]).buffer(e["radius"] + 0.25))
    # the garden cage: walls of wire all round (its gate is not modelled as open)
    cage = next((e for b in spec["buildings"] for e in b.get("elements", []) if e["id"] == "site_cage_rails"), None)
    if cage:
        pts = [s[0][:2] for s in cage["segments"]][:4]
        blocked.append(Polygon(pts).buffer(0.25, join_style=2))
    for f in spec.get("site", {}).get("fences", []):
        if "gate" in f["id"]:
            continue
        blocked.append(LineString(f["path"]).buffer(0.2))

    # floors raise the standing height
    # where floors overlap (a deck over an old slab) the upper one is the one stood on
    floor_top = np.full((ny, nx), np.nan)
    for poly, top in floors:
        jj, ii = cells(poly)
        floor_top[jj, ii] = np.fmax(floor_top[jj, ii], top)
    on_floor = ~np.isnan(floor_top)
    Z[on_floor] = floor_top[on_floor]
    steep &= ~on_floor
    # stairs: a ramp from the deck down to the ground
    ramps = []
    for b in spec["buildings"]:
        for e in b.get("elements", []):
            if e["kind"] != "stairs":
                continue
            tx, ty = e["top"]
            dx, dy = e["direction"]
            n = math.hypot(dx, dy); dx, dy = dx / n, dy / n
            steps = max(1, round((e["z_top"] - e["z_bottom"]) / e.get("riser", 0.18)))
            run = steps * e.get("tread", 0.28)
            hw = e["width"] / 2
            px, py = -dy, dx
            quad = Polygon([(tx + px * hw, ty + py * hw), (tx - px * hw, ty - py * hw),
                            (tx - px * hw + dx * run, ty - py * hw + dy * run), (tx + px * hw + dx * run, ty + py * hw + dy * run)])
            jj, ii = cells(quad)
            s = ((xs[ii] - tx) * dx + (ys[jj] - ty) * dy) / run
            Z[jj, ii] = e["z_top"] + (e["z_bottom"] - e["z_top"]) * np.clip(s, 0, 1)
            steep[jj, ii] = False
            ramps.append(quad)
    for g in blocked:
        jj, ii = cells(g)
        walk[jj, ii] = False
    for q in ramps:                       # stairs win over the railing / skirt lines drawn across their head
        jj, ii = cells(q.buffer(-0.15))
        walk[jj, ii] = True
    if inter:
        for r in inter["rooms"]:
            for p in r["polys"]:
                jj, ii = cells(Polygon(p).buffer(0.03))
                Z[jj, ii] = r["z"]; walk[jj, ii] = True; steep[jj, ii] = False
        jj, ii = cells(inside.boundary.buffer(0.22))
        walk[jj, ii] = False
        for w in inter["walls"]:
            jj, ii = cells(LineString([w["a"], w["b"]]).buffer(inter["wall_t"] / 2 + 0.16, cap_style=2))
            walk[jj, ii] = False
        for it in inter["items"]:
            if it["type"] in ("rug", "tv", "lamp", "petbowl", "petbed"):
                continue
            jj, ii = cells(box_poly({"center": [it["x"], it["y"]], "size": [it["w"], it["d"]], "yaw": it["yaw"]}, 0.12))
            walk[jj, ii] = False
        bnd = inside.boundary
        for d in inter["doors"]:                 # door openings through the outer wall
            pt = Point(d["at"])
            pr = bnd.project(pt)
            p0, p1 = bnd.interpolate(max(pr - 0.3, 0)), bnd.interpolate(pr + 0.3)
            ux, uy = p1.x - p0.x, p1.y - p0.y
            L = math.hypot(ux, uy) or 1; ux, uy = ux / L, uy / L
            hw = d["width"] * 0.42
            gap = Polygon([(pt.x + ux * hw - uy * 0.5, pt.y + uy * hw + ux * 0.5), (pt.x - ux * hw - uy * 0.5, pt.y - uy * hw + ux * 0.5),
                           (pt.x - ux * hw + uy * 0.5, pt.y - uy * hw - ux * 0.5), (pt.x + ux * hw + uy * 0.5, pt.y + uy * hw - ux * 0.5)])
            jj, ii = cells(gap)
            if d["id"] == "garage_link":             # the garage itself stays closed: open only the house side
                keep = np.array([inside.contains(Point(xs[i], ys[j])) for j, i in zip(jj, ii)], bool)
                jj, ii = jj[keep], ii[keep]
            walk[jj, ii] = True
    walk &= ~steep

    def reach(start):
        si = int((start[0] - x0) / CELL); sj = int((start[1] - y0) / CELL)
        seen = np.zeros_like(walk)
        if not walk[sj, si]:
            return seen
        q = deque([(sj, si)]); seen[sj, si] = True
        while q:
            j, i = q.popleft()
            for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                v, u = j + dj, i + di
                if 0 <= v < ny and 0 <= u < nx and walk[v, u] and not seen[v, u] and abs(Z[v, u] - Z[j, i]) <= STEP:
                    if dj and di and not (walk[j, u] and walk[v, i]):
                        continue
                    seen[v, u] = True; q.append((v, u))
        return seen

    seen = reach(START)
    # doors: the point just outside each one, snapped to a reachable cell
    doors = []
    wings = {w["id"]: (b, w) for b in spec["buildings"] for w in b.get("wings", [])}
    for b in spec["buildings"]:
        for o in b.get("openings", []):
            if o.get("kind") not in ("door", "garage_door"):
                continue
            bw = wings.get(o.get("wing"))
            if not bw or not bw[1].get("footprint"):
                continue
            fp = Polygon(bw[1]["footprint"])
            ring = list(fp.exterior.coords)
            p = Point(o["at"])
            best = min(range(len(ring) - 1), key=lambda k: LineString([ring[k], ring[k + 1]]).distance(p))
            (ax, ay), (bx, by) = ring[best], ring[best + 1]
            ex, ey = bx - ax, by - ay
            L = math.hypot(ex, ey)
            nxv, nyv = ey / L, -ex / L
            c = fp.centroid
            if (o["at"][0] - c.x) * nxv + (o["at"][1] - c.y) * nyv < 0:
                nxv, nyv = -nxv, -nyv
            want = (o["at"][0] + nxv * 0.75, o["at"][1] + nyv * 0.75)
            best_cell, bd = None, 1e9
            for j in range(max(int((want[1] - y0) / CELL) - 6, 0), min(int((want[1] - y0) / CELL) + 7, ny)):
                for i in range(max(int((want[0] - x0) / CELL) - 6, 0), min(int((want[0] - x0) / CELL) + 7, nx)):
                    if seen[j, i]:
                        d = math.hypot(xs[i] - want[0], ys[j] - want[1])
                        if d < bd:
                            best_cell, bd = (j, i), d
            if best_cell is None or bd > 2.0:
                print("door not reachable:", o["id"], want)
                continue
            j, i = best_cell
            doors.append({"id": o["id"], "building": b["id"], "kind": o["kind"], "label": o.get("label", o["id"]),
                          "x": round(float(xs[i]), 3), "y": round(float(ys[j]), 3),
                          "face": [round(-nxv, 4), round(-nyv, 4)], "at": [round(o["at"][0], 3), round(o["at"][1], 3)]})
    if inter:                                     # the door from the house into the garage: approached from inside
        gl = inter["garage_link"]
        gi, gj = int((gl["inside"][0] - x0) / CELL), int((gl["inside"][1] - y0) / CELL)
        if seen[gj, gi]:
            doors.append({"id": "garage_link", "building": "house", "kind": "door", "label": "Door from the house to the garage",
                          "x": round(float(xs[gi]), 3), "y": round(float(ys[gj]), 3), "face": gl["face"], "at": gl["outside"]})
        else:
            print("door not reachable: garage_link")
        jj, ii = np.nonzero(walk)
        ins = [(j, i) for j, i in zip(jj, ii) if inside.contains(Point(xs[i], ys[j]))]
        print(f"indoor cells: {len(ins)}, reachable {sum(seen[j, i] for j, i in ins)}")
    zb = float(np.floor(Z.min()))
    zcm = np.clip(np.round((Z - zb) * 100), 0, 65535).astype("<u2")
    out = {"x0": x0, "y0": y0, "cell": CELL, "nx": nx, "ny": ny, "zbase": zb, "step": STEP,
           "z": base64.b64encode(zcm.tobytes()).decode(),
           "walk": base64.b64encode(np.packbits(walk.ravel()).tobytes()).decode(),
           "doors": doors, "start": START}
    json.dump(out, open(os.path.join(a.assets, "nav.json"), "w"), separators=(",", ":"))
    print(f"nav.json: {nx} x {ny} cells of {CELL} m, walkable {walk.mean():.0%}, reachable from the walk {seen.sum() / walk.sum():.0%}, "
          f"{len(doors)} doors: {', '.join(d['id'] for d in doors)}")


if __name__ == "__main__":
    main()
