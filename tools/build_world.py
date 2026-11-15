#!/usr/bin/env python3
"""The world beyond the lot for the Play house game: the drive to Smith Field Airport and the airfield.

  python3 tools/build_world.py <overture dir> assets/

Reads Overture Maps extracts (GeoParquet, made with an Overture query over the area between the house and
Smith Field, 902 W Ludwig Rd):

  segments.parquet            transportation / segment (roads)
  infra.parquet               base / infrastructure (runways, taxiways, aprons, the aerodrome)
  buildings.parquet           buildings / building on and around the airfield
  buildings_corridor.parquet  buildings / building between the house and the airfield
  places.parquet              places / place (to find the airport's front door)

and writes <assets>/world.json in the site frame (EPSG:26916 metres minus the site origin):

  route      the car's way from the attached garage, down the driveway, along the real roads (shortest
             drive by road class) to the airport's parking lot; `slow` marks the driveway part
  roads      streets near the route and the main roads around it, as centre lines with a width
  airport    runways, taxiways, aprons, the airfield boundary, its buildings, and where to park, where
             the plane waits and where to stand to watch
  buildings  footprints and heights of buildings along the route, for context when zoomed out
  ground_z   the height of the flat world outside the surveyed lot

Map data © OpenStreetMap contributors and the Overture Maps Foundation (ODbL / CDLA-Permissive-2.0).
"""
from __future__ import annotations

import json
import math
import os
import sys

import networkx as nx
import numpy as np
import pyarrow.parquet as pq
from pyproj import Transformer
from shapely import wkb
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import substring, unary_union

E0, N0 = 651405.348, 4560211.139            # site origin
TO = Transformer.from_crs(4326, 26916, always_xy=True)

# the car's way out: inside the attached garage, out of its door, across the court and down the driveway
DRIVEWAY = [(-7.5, 6.0), (-11.5, 6.0), (-16.5, 5.0), (-17.5, 0.5), (-14.0, -4.5), (-6.0, -5.2), (4.0, -5.2), (10.0, -6.5),
            (11.6, -12.0), (10.5, -25.0), (9.9, -33.0), (12.0, -40.5), (16.5, -48.0), (21.0, -60.0), (23.2, -72.0),
            (22.9, -84.0), (21.4, -96.0), (19.0, -110.0), (16.6, -123.0), (13.3, -137.0), (10.4, -146.5), (6.6, -152.0),
            (1.7, -157.5), (-1.4, -163.0), (-2.6, -168.5), (-3.5, -173.5)]
AIRPORT_PLACE = (-85.15158, 41.14033)        # Overture place "Smith Field Airport"
SPEED = {"motorway": 30, "trunk": 25, "primary": 20, "secondary": 18, "tertiary": 15, "unclassified": 12,
         "residential": 11, "living_street": 7, "service": 6, "unknown": 9}
WIDTH = {"motorway": 15, "trunk": 13, "primary": 11, "secondary": 10, "tertiary": 9, "unclassified": 7,
         "residential": 7, "living_street": 6, "service": 4.5, "unknown": 6}


def xy(lon, lat):
    e, n = TO.transform(lon, lat)
    return e - E0, n - N0


def geom_xy(g):
    if g.geom_type == "LineString":
        return LineString([xy(*c) for c in g.coords])
    if g.geom_type == "Polygon":
        return Polygon([xy(*c) for c in g.exterior.coords], [[xy(*c) for c in r.coords] for r in g.interiors])
    if g.geom_type == "MultiPolygon":
        return max((geom_xy(p) for p in g.geoms), key=lambda p: p.area)
    if g.geom_type == "Point":
        return Point(xy(g.x, g.y))
    return None


def rnd(pts, nd=1):
    return [[round(x, nd), round(y, nd)] for x, y in pts]


def terrain_edge_z(assets):
    t = json.load(open(os.path.join(assets, "site_layers.json")))["terrain"]
    raw = np.fromfile(os.path.join(assets, t["file"]), dtype="<u2").reshape(t["ny"], t["nx"])
    z = t["zmin"] + raw.astype(np.float64) * t["zscale"]
    edge = np.concatenate([z[0, :], z[-1, :], z[:, 0], z[:, -1]])
    ext = (t["x0"], t["y0"], t["x0"] + t["nx"] * t["cell"], t["y0"] + t["ny"] * t["cell"])
    # where the road leaves the lot matters most: the drive meets the world there
    j = int((-177 - t["y0"]) / t["cell"])
    road = z[max(j - 2, 0):j + 3, :].mean()
    return float(np.median(edge)), float(road), ext


def main():
    src, out = sys.argv[1], sys.argv[2]
    edge_z, road_z, ext = terrain_edge_z(out)

    # ---- the road graph: one edge per stretch of road between two connectors
    seg = pq.read_table(os.path.join(src, "segments.parquet")).to_pylist()
    G = nx.Graph()
    pieces = []
    for r in seg:
        if r["subtype"] != "road" or r["class"] not in SPEED:
            continue
        line = geom_xy(wkb.loads(r["geometry"]))
        if line is None or line.length < 0.5:
            continue
        cons = sorted(r.get("connectors") or [], key=lambda c: c["at"])
        name = (r.get("names") or {}).get("primary") or ""
        for a, b in zip(cons, cons[1:]):
            part = substring(line, a["at"], b["at"], normalized=True)
            if part.length < 0.2:
                continue
            w = part.length / SPEED[r["class"]]
            G.add_edge(a["connector_id"], b["connector_id"], weight=w, line=part, cls=r["class"], name=name)
            pieces.append((part, r["class"], name, a["connector_id"], b["connector_id"]))

    def tie_in(pt, want=None):
        """Join a point to the graph at the nearest stretch of road (optionally of a named road)."""
        p = Point(pt)
        cand = [c for c in pieces if not want or want.lower() in c[2].lower()] or pieces
        part, cls, name, a, b = min(cand, key=lambda c: c[0].distance(p))
        d = part.project(p)
        node = f"tie:{pt[0]:.1f},{pt[1]:.1f}"
        G.add_node(node)
        G.add_edge(node, a, weight=d / SPEED[cls], line=substring(part, d, 0), cls=cls, name=name)
        G.add_edge(node, b, weight=(part.length - d) / SPEED[cls], line=substring(part, d, part.length), cls=cls, name=name)
        return node, part.interpolate(d), name

    start, start_pt, start_name = tie_in(DRIVEWAY[-1], "Dupont")
    gate = xy(*AIRPORT_PLACE)
    end, end_pt, end_name = tie_in(gate)
    path = nx.shortest_path(G, start, end, weight="weight")
    road = [tuple(start_pt.coords[0])]
    names = []
    for u, v in zip(path, path[1:]):
        e = G.edges[u, v]
        cs = list(e["line"].coords)
        if Point(cs[0]).distance(Point(road[-1])) > Point(cs[-1]).distance(Point(road[-1])):
            cs = cs[::-1]
        road += cs[1:]
        if e["name"] and (not names or names[-1] != e["name"]):
            names.append(e["name"])
    road_line = LineString(road).simplify(0.8)
    route = [{"x": round(x, 1), "y": round(y, 1), "slow": 1} for x, y in DRIVEWAY]
    route += [{"x": round(x, 1), "y": round(y, 1), "slow": 0} for x, y in road_line.coords]
    route.append({"x": round(gate[0], 1), "y": round(gate[1], 1), "slow": 1})
    length = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in zip(route, route[1:]))
    print(f"route: {length / 1000:.2f} km via {' -> '.join(names)}")

    # ---- context roads: everything drivable near the route, and the bigger roads further out
    corridor = LineString([(p["x"], p["y"]) for p in route]).buffer(450)
    roads = []
    seen = set()
    for part, cls, name, a, b in pieces:
        if (a, b) in seen:
            continue
        seen.add((a, b))
        big = cls in ("motorway", "trunk", "primary", "secondary", "tertiary")
        if not (big or part.intersects(corridor)):
            continue
        if part.within(box(*ext)):
            continue                           # the lot draws its own drive
        s = part.simplify(1.5)
        roads.append({"w": WIDTH[cls], "c": cls, "p": rnd(s.coords)})

    # ---- the airfield
    infra = pq.read_table(os.path.join(src, "infra.parquet")).to_pylist()
    air = {"runways": [], "taxiways": [], "aprons": [], "boundary": None}
    for r in infra:
        if r.get("subtype") != "airport":
            continue
        g = geom_xy(wkb.loads(r["geometry"]))
        cls = r.get("class")
        if cls == "runway" and g.length > 300:
            air["runways"].append({"a": rnd([g.coords[0]])[0], "b": rnd([g.coords[-1]])[0], "len": round(g.length)})
        elif cls == "taxiway":
            air["taxiways"].append(rnd(g.simplify(0.5).coords))
        elif cls == "apron" and g.geom_type == "Polygon":
            air["aprons"].append(rnd(g.exterior.simplify(0.3).coords))
        elif cls in ("private_airport", "airport", "aerodrome") and g.geom_type == "Polygon":
            air["boundary"] = rnd(g.exterior.simplify(1).coords)
    air["runways"].sort(key=lambda r: -r["len"])
    for i, rw in enumerate(air["runways"]):
        rw["w"] = 23 if i == 0 else 20
    # where things happen: park by the airport's door, the plane on the apron nearest it
    gp = Point(gate)
    aprons = sorted((Polygon(a) for a in air["aprons"]), key=lambda a: a.distance(gp))
    near = aprons[0] if aprons else gp.buffer(20)
    plane = near.representative_point()
    air["park"] = rnd([gate])[0]
    air["plane"] = rnd([(plane.x, plane.y)])[0]
    watch = LineString([gate, (plane.x, plane.y)]).interpolate(0.5, normalized=True)
    air["watch"] = rnd([(watch.x, watch.y)])[0]

    def blds(path, keep):
        out_ = []
        for r in pq.read_table(path).to_pylist():
            g = geom_xy(wkb.loads(r["geometry"]))
            if g is None or g.geom_type != "Polygon" or g.area < 12 or not keep(g):
                continue
            if g.intersects(box(*ext)):
                continue                       # the lot is modelled in detail
            h = r.get("height") or (r.get("num_floors") or 0) * 3.2 or (7.0 if g.area > 400 else 5.0)
            out_.append({"h": round(min(h, 40), 1), "p": rnd(g.exterior.simplify(0.6).coords)})
        return out_

    boundary = Polygon(air["boundary"]) if air["boundary"] else gp.buffer(600)
    air["buildings"] = blds(os.path.join(src, "buildings.parquet"), lambda g: g.intersects(boundary.buffer(120)))
    near_route = LineString([(p["x"], p["y"]) for p in route]).buffer(140)
    town = blds(os.path.join(src, "buildings_corridor.parquet"), lambda g: g.intersects(near_route) and not g.intersects(boundary.buffer(120)))

    world = {
        "note": "Map data © OpenStreetMap contributors and the Overture Maps Foundation. Heights where unknown are guesses.",
        "ground_z": round(road_z, 2), "edge_z": round(edge_z, 2), "lot": [round(v, 1) for v in ext],
        "route": route, "route_km": round(length / 1000, 2), "route_names": names,
        "roads": roads, "airport": air, "buildings": town,
        "airport_name": "Smith Field Airport", "airport_elev_note": "drawn at the lot's road height; the airfield is a few metres lower in reality",
    }
    json.dump(world, open(os.path.join(out, "world.json"), "w"), separators=(",", ":"))
    print(f"world.json: {len(roads)} roads, {len(town)} buildings along the way, {len(air['buildings'])} at the airport, "
          f"{len(air['runways'])} runways, {len(air['taxiways'])} taxiways, {len(air['aprons'])} aprons; ground {road_z:.2f} m; "
          f"{os.path.getsize(os.path.join(out, 'world.json')) / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
