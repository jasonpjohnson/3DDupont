"""Roof and wall solver.

A building is a set of *wings*. Each wing has

  footprint  shapely Polygon of its wall line (site frame, metres); may be None for
             open structures such as porch roofs
  eave       shapely Polygon of the roof extent (footprint + overhang by default)
  planes     list of (a, b, d): z = a*x + b*y + d in the site frame
  solid      True if the wing is an enclosed volume that hides other wings' roofs and
             walls where they run inside it

A wing's roof surface is the lower envelope (minimum) of its planes over its eave
polygon - this reproduces gable, hip, shed, gambrel and flat roofs exactly. Where wings
interpenetrate, a roof facet is hidden wherever it lies inside another *solid* wing's
footprint below that wing's roof, which produces valleys and wall/roof junctions without
any hand-drawn geometry. Overhangs that pass above a lower wing's roof are kept (the
model is not a 2.5D heightfield).

Walls are solved per footprint edge in (s, h) space: s = distance along the edge,
h = absolute elevation. The wall polygon is the area under the wing's own roof profile,
minus the area under the roof of any solid wing that touches the outside of that edge.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

BIG = 5.0e3
EPS = 1e-6


@dataclass
class Wing:
    id: str
    planes: list
    footprint: Polygon | None = None
    eave: Polygon | None = None
    overhang: float = 0.4
    solid: bool = True
    base_z: float = 0.0
    bottom_z: float | None = None      # set for cantilevered volumes that stop above the ground
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.planes = [tuple(float(v) for v in p) for p in self.planes]
        if self.eave is None:
            if self.footprint is None:
                raise ValueError(f"wing {self.id}: need a footprint or an eave polygon")
            self.eave = self.footprint.buffer(self.overhang, join_style=2, mitre_limit=5.0)
        self._facets = None

    # -- surface -------------------------------------------------------------
    def z(self, x, y):
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        zs = np.stack([a * x + b * y + d for a, b, d in self.planes])
        return zs.min(axis=0)

    def facets(self):
        """[(plane_index, Polygon)] partition of the eave polygon by lowest plane."""
        if self._facets is None:
            out = []
            for i, pi in enumerate(self.planes):
                region = self.eave
                for j, pj in enumerate(self.planes):
                    if i == j or region.is_empty:
                        continue
                    hp = half_plane(pi, pj, region, lower=True, tie_wins=i < j)
                    region = region.intersection(hp) if hp is not None else region
                for g in _polys(region):
                    out.append((i, g))
            self._facets = out
        return self._facets


def _polys(geom, min_area=1e-4):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > min_area else []
    if hasattr(geom, "geoms"):
        res = []
        for g in geom.geoms:
            res.extend(_polys(g, min_area))
        return res
    return []


def half_plane(pi, pj, near, lower=True, tie_wins=True, margin=0.0):
    """Polygon of the region where plane pi is below (lower=True) or above plane pj.

    Returns None when the planes are parallel and pi wins everywhere, and an empty
    Polygon when pi loses everywhere. ``margin`` shrinks the winning region (metres of
    height) - used for strict comparisons."""
    da, db, dd = pi[0] - pj[0], pi[1] - pj[1], pi[2] - pj[2]
    if not lower:
        da, db, dd = -da, -db, -dd
    dd += margin
    n = np.hypot(da, db)
    if n < 1e-9:                       # parallel planes
        if dd < -1e-9 or (abs(dd) <= 1e-9 and tie_wins):
            return None
        return Polygon()
    # region: da*x + db*y + dd <= 0
    c = near.centroid
    nx, ny = da / n, db / n
    dist = (da * c.x + db * c.y + dd) / n
    p0 = np.array([c.x - nx * dist, c.y - ny * dist])
    t = np.array([-ny, nx])
    nvec = np.array([nx, ny])
    return Polygon([p0 - BIG * t, p0 + BIG * t, p0 + BIG * t - BIG * nvec, p0 - BIG * t - BIG * nvec])


def higher_region(wing_b: Wing, plane_a, margin=1e-4):
    """Region inside wing_b's footprint where wing_b's roof is higher than plane_a."""
    if wing_b.footprint is None or not wing_b.solid:
        return Polygon()
    parts = []
    for j, reg in wing_b.facets():
        reg = reg.intersection(wing_b.footprint)
        if reg.is_empty:
            continue
        hp = half_plane(wing_b.planes[j], plane_a, reg, lower=False, tie_wins=False, margin=margin)
        parts.append(reg if hp is None else reg.intersection(hp))
    parts = [p for p in parts if not p.is_empty]
    return unary_union(parts) if parts else Polygon()


def visible_facets(wings):
    """For every wing: [(plane_index, Polygon)] after hiding parts buried in other wings."""
    result = {}
    for a in wings:
        vis = []
        for i, reg in a.facets():
            for b in wings:
                if b is a or not b.solid or b.footprint is None:
                    continue
                if not reg.intersects(b.footprint):
                    continue
                reg = reg.difference(higher_region(b, a.planes[i]))
                if reg.is_empty:
                    break
            for g in _polys(reg):
                vis.append((i, g))
        result[a.id] = vis
    return result


def lift(poly_ring, plane):
    a, b, d = plane
    r = np.asarray(poly_ring, dtype=float)
    return np.c_[r[:, 0], r[:, 1], a * r[:, 0] + b * r[:, 1] + d]


def envelope_profile(wing: Wing, p, q, extra_s=()):
    """Roof height of ``wing`` along segment p->q as a polyline [(s, h)]."""
    p = np.asarray(p, dtype=float)
    q = np.asarray(q, dtype=float)
    L = float(np.hypot(*(q - p)))
    lines = []                         # h = m*s + c
    for a, b, d in wing.planes:
        h0 = a * p[0] + b * p[1] + d
        h1 = a * q[0] + b * q[1] + d
        lines.append(((h1 - h0) / L if L > 0 else 0.0, h0))
    ss = {0.0, L}
    for i in range(len(lines)):
        for j in range(i + 1, len(lines)):
            dm = lines[i][0] - lines[j][0]
            if abs(dm) > 1e-12:
                s = (lines[j][1] - lines[i][1]) / dm
                if 0 < s < L:
                    ss.add(float(s))
    for s in extra_s:
        if 0 < s < L:
            ss.add(float(s))
    ss = sorted(ss)
    return [(s, min(m * s + c for m, c in lines)) for s in ss]


def wall_polygons(wings, wing: Wing, bottom=None, probe=0.02):
    """Exposed wall area for every footprint edge of ``wing``.

    Returns a list of dicts: {p, q, length, normal (outward unit 2-vector), poly}
    where ``poly`` is a shapely geometry in (s, h) coordinates."""
    if wing.footprint is None:
        return []
    fp = wing.footprint
    if fp.exterior.is_ccw is False:
        fp = Polygon(list(fp.exterior.coords)[::-1])
    bottom = wing.base_z if bottom is None else bottom
    if wing.bottom_z is not None:
        bottom = wing.bottom_z
    ring = np.asarray(fp.exterior.coords)
    others = [b for b in wings if b is not wing and b.solid and b.footprint is not None]
    out = []
    for k in range(len(ring) - 1):
        p, q = ring[k], ring[k + 1]
        d = q - p
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        t = d / L
        n = np.array([t[1], -t[0]])    # outward for a CCW ring
        prof = envelope_profile(wing, p, q)
        poly = Polygon([(0.0, bottom)] + prof + [(L, bottom)]) if max(h for _, h in prof) > bottom else Polygon()
        if not poly.is_valid:
            poly = poly.buffer(0)
        # subtract what is buried in neighbouring solids
        for b in others:
            line = LineString([p + n * probe, q + n * probe])
            inter = line.intersection(b.footprint)
            if inter.is_empty:
                continue
            segs = [inter] if inter.geom_type == "LineString" else [g for g in getattr(inter, "geoms", []) if g.geom_type == "LineString"]
            for seg in segs:
                c = np.asarray(seg.coords)
                s0 = float(np.dot(c[0] - p, t))
                s1 = float(np.dot(c[-1] - p, t))
                s0, s1 = sorted((max(0.0, s0), min(L, s1)))
                if s1 - s0 < 1e-4:
                    continue
                pa = p + t * s0 + n * probe
                pb = p + t * s1 + n * probe
                bprof = envelope_profile(b, pa, pb)
                floor = bottom - 1.0 if b.bottom_z is None else b.bottom_z     # nothing is hidden below a cantilever
                cut = Polygon([(s0, floor)] + [(s0 + s, h) for s, h in bprof] + [(s1, floor)])
                if not cut.is_valid:
                    cut = cut.buffer(0)
                poly = poly.difference(cut)
        out.append(dict(p=p, q=q, length=L, tangent=t, normal=n, poly=poly, edge=k))
    return out


def model_dsm(wings, X, Y, ground=None):
    """Top-most roof elevation of the model at points (X, Y); NaN where no roof."""
    X = np.asarray(X, dtype=float)
    Y = np.asarray(Y, dtype=float)
    top = np.full(X.shape, np.nan)
    pts = None
    for w in wings:
        minx, miny, maxx, maxy = w.eave.bounds
        box = (X >= minx) & (X <= maxx) & (Y >= miny) & (Y <= maxy)
        if not box.any():
            continue
        pe = prep(w.eave)
        inside = np.zeros(X.shape, dtype=bool)
        idx = np.argwhere(box)
        flags = [pe.contains(Point(X[tuple(i)], Y[tuple(i)])) for i in idx]
        inside[tuple(idx[np.array(flags, dtype=bool)].T)] = True
        if not inside.any():
            continue
        z = w.z(X[inside], Y[inside])
        cur = top[inside]
        top[inside] = np.where(np.isnan(cur), z, np.maximum(cur, z))
    return top


if __name__ == "__main__":                                   # tiny self-test
    main = Wing("main", [(0, 0.5, 3.0), (0, -0.5, 8.0)], Polygon([(0, 0), (12, 0), (12, 10), (0, 10)]))
    cross = Wing("cross", [(0.5, 0, 2.0), (-0.5, 0, 8.0)], Polygon([(3, -4), (9, -4), (9, 5), (3, 5)]))
    porch = Wing("porch", [(0, 0.25, 3.2)], footprint=None, eave=Polygon([(9, -3), (12, -3), (12, 0), (9, 0)]), solid=False)
    wings = [main, cross, porch]
    vis = visible_facets(wings)
    for k, v in vis.items():
        print(k, [(i, round(g.area, 2)) for i, g in v])
    for w in (main, cross):
        for e in wall_polygons(wings, w):
            print(w.id, e["edge"], round(e["length"], 2), round(e["poly"].area, 2))
    xs, ys = np.meshgrid(np.linspace(-1, 13, 8), np.linspace(-5, 11, 9))
    print(np.round(model_dsm(wings, xs, ys), 1))
