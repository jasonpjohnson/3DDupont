#!/usr/bin/env python3
"""Turn spec/site_spec.json into GLB models (one per building) + a parts catalogue.

Usage: python3 tools/build_model.py spec/site_spec.json assets/

See spec/SCHEMA.md for the spec format. Everything is in the site frame
(EPSG:26916 metres, x = E - E0, y = N - N0, z = NAVD88 metres).
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import textures  # noqa: E402
from meshkit import Model, polygon_parts  # noqa: E402
from roofsolve import Wing, visible_facets, wall_polygons  # noqa: E402

FT = 0.3048
DEFAULTS = dict(roof_thickness=0.16, fascia=0.2, below_grade=0.6, overhang=0.4,
                frame_w=0.055, frame_depth=0.09, glass_inset=0.05, casing_w=0.09)


# --------------------------------------------------------------------------- utils
def plane_basis(plane):
    a, b, d = plane
    g = np.hypot(a, b)
    n = np.array([-a, -b, 1.0])
    n /= np.linalg.norm(n)
    if g < 1e-9:
        return np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), n
    u = np.array([-b, a, 0.0]) / g                 # along the contour (horizontal)
    v = np.cross(n, u)                             # up-slope
    return u, v, n


def plane_polygon(part, poly2d, plane, dz=0.0, flip=False, uv_scale=1.0):
    """Add a site-frame polygon (x, y) lifted onto ``plane`` (optionally shifted by dz)."""
    a, b, d = plane
    u, v, n = plane_basis(plane)
    for poly in polygon_parts(poly2d):
        c = poly.representative_point()
        o = np.array([c.x, c.y, a * c.x + b * c.y + d + dz])

        def to_st(ring):
            r = np.asarray(ring.coords)
            p3 = np.c_[r[:, 0], r[:, 1], a * r[:, 0] + b * r[:, 1] + d + dz] - o
            return np.c_[p3 @ u, p3 @ v]
        st = Polygon(to_st(poly.exterior), [to_st(r) for r in poly.interiors])
        if not st.is_valid:
            st = st.buffer(0)
        part.planar(st, o, u, v, normal=n, uv_scale=uv_scale, flip=flip)


def nearest_edge(footprint: Polygon, pt):
    """(edge index, s along edge, edge length, p, q) of the footprint edge nearest pt."""
    ring = np.asarray(footprint.exterior.coords)
    if not footprint.exterior.is_ccw:
        ring = ring[::-1]
    best = None
    P = np.asarray(pt, dtype=float)
    for k in range(len(ring) - 1):
        p, q = ring[k], ring[k + 1]
        d = q - p
        L = float(np.hypot(*d))
        if L < 1e-6:
            continue
        s = float(np.clip(np.dot(P - p, d) / L, 0, L))
        dist = float(np.hypot(*(p + d / L * s - P)))
        if best is None or dist < best[0]:
            best = (dist, k, s, L, p, q)
    return best[1:], best[0]


def fmt_ft(m):
    total = round(m / FT * 12)
    return f"{total // 12}'{total % 12}\"" if total % 12 else f"{total // 12}'"


# ----------------------------------------------------------------------- materials
def register_materials(model: Model, mats: dict):
    for name, m in mats.items():
        tex = None
        size = 1.0
        if m.get("texture"):
            tex, size = textures.make(m["texture"], m["color"], **m.get("texture_args", {}))
        model.material(name, color=m["color"], roughness=m.get("roughness", 0.9), metallic=m.get("metallic", 0.0),
                       texture=tex, tex_size_m=m.get("tex_size_m", size), double_sided=m.get("double_sided", False),
                       alpha=m.get("alpha", 1.0))


# --------------------------------------------------------------------------- build
class Builder:
    def __init__(self, spec, building, out_dir):
        self.spec = spec
        self.b = building
        self.cfg = {**DEFAULTS, **spec.get("defaults", {}), **building.get("defaults", {})}
        self.model = Model(z0=spec["z0"])
        register_materials(self.model, spec["materials"])
        self.parts_info = {}
        self.out_dir = out_dir
        self.wings = []
        self.wspec = {}
        for w in building["wings"]:
            fp = Polygon(w["footprint"]) if w.get("footprint") else None
            if fp is not None and not fp.is_valid:
                fp = fp.buffer(0)
            eave = Polygon(w["eave"]) if w.get("eave") else None
            wing = Wing(w["id"], w["planes"], footprint=fp, eave=eave,
                        overhang=w.get("overhang", self.cfg["overhang"]),
                        solid=w.get("solid", fp is not None), base_z=w.get("base_z", spec["z0"]))
            self.wings.append(wing)
            self.wspec[w["id"]] = w
        self.solids = unary_union([w.footprint for w in self.wings if w.solid and w.footprint is not None]) \
            if any(w.solid and w.footprint is not None for w in self.wings) else Polygon()

    # -- helpers -------------------------------------------------------------
    def part(self, name, material, info):
        name = name.replace(" ", "_").replace("-", "_").lower()
        p = self.model.part(name, material)
        base = {"building": self.b["id"], "material": self.spec["materials"][material].get("label", material)}
        self.parts_info[name] = {**base, **{k: v for k, v in info.items() if v not in (None, "")}}
        return p

    def uv(self, material):
        return self.model.uv_scale(material)

    @staticmethod
    def info_of(obj, **extra):
        keys = ("label", "desc", "dims", "evidence", "source", "confidence")
        return {**{k: obj.get(k) for k in keys}, **extra}

    # -- roofs ---------------------------------------------------------------
    def build_roofs(self):
        vis = visible_facets(self.wings)
        t = self.cfg["roof_thickness"]
        fh = self.cfg["fascia"]
        for wing in self.wings:
            ws = self.wspec[wing.id]
            if ws.get("no_roof"):
                continue
            mat = ws.get("roof_material", "roof")
            trim = ws.get("fascia_material", "trim")
            soffit_mat = ws.get("soffit_material", trim)
            label = ws.get("roof_label") or f"{ws.get('label', wing.id)} — roof"
            info = self.info_of({**ws, **ws.get("roof_info", {})}, label=label)
            roof = self.part(f"{wing.id}_roof", mat, info)
            fascia = self.part(f"{wing.id}_fascia", trim, {**info, "label": f"{ws.get('label', wing.id)} — fascia and soffit"})
            soffit = fascia if soffit_mat == trim else self.part(f"{wing.id}_soffit", soffit_mat, info)
            eave_line = wing.eave.exterior
            for i, poly in vis[wing.id]:
                plane = wing.planes[i]
                plane_polygon(roof, poly, plane, uv_scale=self.uv(mat))
                under = poly if not wing.solid or wing.footprint is None else poly.difference(self.solids)
                if not under.is_empty:
                    plane_polygon(soffit, under, plane, dz=-t, flip=True, uv_scale=self.uv(soffit_mat))
                # fascia / rake boards on true eave edges
                a, b, d = plane
                for g in polygon_parts(poly):
                    ring = np.asarray(g.exterior.coords)
                    if not g.exterior.is_ccw:
                        ring = ring[::-1]
                    for k in range(len(ring) - 1):
                        p, q = ring[k], ring[k + 1]
                        mid = (p + q) / 2
                        if eave_line.distance(Point(mid)) > 1e-4 or np.hypot(*(q - p)) < 1e-4:
                            continue
                        zp, zq = a * p[0] + b * p[1] + d, a * q[0] + b * q[1] + d
                        fascia.quad([p[0], p[1], zp - fh], [q[0], q[1], zq - fh], [q[0], q[1], zq + 0.005], [p[0], p[1], zp + 0.005],
                                    uv_scale=self.uv(trim))
                        # close the underside lip so the board has thickness
                        dvec = (q - p) / np.hypot(*(q - p))
                        inward = np.array([-dvec[1], dvec[0]]) * 0.03
                        fascia.quad([q[0], q[1], zq - fh], [p[0], p[1], zp - fh],
                                    [p[0] + inward[0], p[1] + inward[1], zp - fh], [q[0] + inward[0], q[1] + inward[1], zq - fh],
                                    uv_scale=self.uv(trim))

    # -- walls + openings ----------------------------------------------------
    def opening_rects(self):
        """Resolve every opening to (wing id, edge index, s0, s1, h0, h1, spec)."""
        by_edge = {}
        self.resolved_openings = []
        for o in self.b.get("openings", []):
            wing = next((w for w in self.wings if w.id == o["wing"]), None)
            if wing is None or wing.footprint is None:
                raise ValueError(f"opening {o.get('id')}: unknown or wall-less wing {o['wing']!r}")
            (k, s, L, p, q), dist = nearest_edge(wing.footprint, o["at"])
            if dist > o.get("snap_tolerance", 1.5):
                raise ValueError(f"opening {o.get('id')} is {dist:.2f} m from any wall of wing {wing.id}")
            w = o["width"]
            s0, s1 = s - w / 2, s + w / 2
            if s0 < 0.05:
                s0, s1 = 0.05, 0.05 + w
            if s1 > L - 0.05:
                s0, s1 = L - 0.05 - w, L - 0.05
            floor = self.wspec[wing.id].get("floor_z", wing.base_z)
            h0 = o["sill_z"] if "sill_z" in o else floor + o.get("sill", 0.0)
            h1 = h0 + o["height"]
            rec = dict(wing=wing.id, edge=k, s0=s0, s1=s1, h0=h0, h1=h1, p=p, q=q, L=L, spec=o)
            by_edge.setdefault((wing.id, k), []).append(rec)
            self.resolved_openings.append(rec)
        return by_edge

    def build_walls(self):
        by_edge = self.opening_rects()
        for wing in self.wings:
            ws = self.wspec[wing.id]
            if wing.footprint is None or ws.get("no_walls"):
                continue
            wcfg = ws.get("walls", {})
            main_mat = wcfg.get("material", "siding")
            bottom = wing.base_z - self.cfg["below_grade"]
            bands = []
            lo = bottom - 1.0
            for band in sorted(wcfg.get("bands", []), key=lambda b_: b_["to_z"]):
                bands.append((lo, band["to_z"], band["material"], band.get("label")))
                lo = band["to_z"]
            top_split = wcfg.get("gable_from_z")
            if top_split is not None and wcfg.get("gable_material"):
                bands.append((lo, top_split, main_mat, None))
                bands.append((top_split, 1e4, wcfg["gable_material"], "gable"))
            else:
                bands.append((lo, 1e4, main_mat, None))
            parts = {}
            label = ws.get("label", wing.id)
            for e in wall_polygons(self.wings, wing, bottom=bottom):
                poly = e["poly"]
                if poly.is_empty:
                    continue
                per_edge = ws.get("edge_materials", {}).get(str(e["edge"]))
                for r in by_edge.get((wing.id, e["edge"]), []):
                    poly = poly.difference(box(r["s0"], r["h0"], r["s1"], r["h1"]))
                origin = (e["p"][0], e["p"][1], 0.0)
                u = (e["tangent"][0], e["tangent"][1], 0.0)
                nrm = (e["normal"][0], e["normal"][1], 0.0)
                for zlo, zhi, mat, blabel in bands:
                    mat = per_edge or mat
                    piece = poly.intersection(box(-1.0, zlo, e["length"] + 1.0, zhi))
                    if piece.is_empty:
                        continue
                    key = mat
                    if key not in parts:
                        suffix = "walls" if mat == main_mat else f"walls_{mat}"
                        info = self.info_of({**ws, **ws.get("wall_info", {})}, label=f"{label} — {self.spec['materials'][mat].get('label', mat)}")
                        parts[key] = self.part(f"{wing.id}_{suffix}", mat, info)
                    parts[key].planar(piece, origin, u, (0, 0, 1), normal=nrm, uv_scale=self.uv(mat))

    def build_openings(self):
        cfg = self.cfg
        groups = {}
        for r in self.resolved_openings:
            o = r["spec"]
            kind = o.get("kind", "window")
            t = (r["q"] - r["p"]) / r["L"]
            n = np.array([t[1], -t[0]])                    # outward
            def P(s, h, out=0.0):
                xy = r["p"] + t * s + n * out
                return np.array([xy[0], xy[1], h])
            s0, s1, h0, h1 = r["s0"], r["s1"], r["h0"], r["h1"]
            frame_mat = o.get("frame_material", "window_frame")
            glass_mat = o.get("glass_material", "glass")
            info = self.info_of(o, dims=o.get("dims") or f"{fmt_ft(s1 - s0)} wide x {fmt_ft(h1 - h0)} high")
            own = o.get("own_part") or kind in ("door", "garage_door")
            base_name = o.get("id") if own and o.get("id") else f"{r['wing']}_{kind}s"
            def get(suffix, mat, label):
                key = (base_name, suffix)
                if key not in groups:
                    ws_label = self.wspec[r["wing"]].get("label", r["wing"])
                    lab = info.get("label") if own else f"{ws_label} — {label}"
                    groups[key] = self.part(f"{base_name}_{suffix}", mat, {**info, "label": lab or label})
                return groups[key]
            fw, fd, gi = cfg["frame_w"], cfg["frame_depth"], cfg["glass_inset"]
            frame = get("frame", frame_mat, "window frames" if kind == "window" else "frame")
            # frame: four members around the hole, proud of the wall by 15 mm, running back fd
            for (a0, a1) in (((s0, h0), (s1, h0)), ((s0, h1), (s1, h1)), ((s0, h0), (s0, h1)), ((s1, h0), (s1, h1))):
                horiz = a0[1] == a1[1]
                inset = fw / 2
                if horiz:
                    hh = a0[1] + (inset if a0[1] == h0 else -inset)
                    frame.beam(P(a0[0], hh, 0.015 - fd / 2), P(a1[0], hh, 0.015 - fd / 2), fd, fw)
                else:
                    ss = a0[0] + (inset if a0[0] == s0 else -inset)
                    frame.beam(P(ss, a0[1], 0.015 - fd / 2), P(ss, a1[1], 0.015 - fd / 2), fw, fd)
            if o.get("casing", kind != "garage_door"):
                cw = o.get("casing_w", cfg["casing_w"])
                cmat = o.get("casing_material", "trim")
                casing = get("casing", cmat, "casing")
                ring = Polygon([(s0 - cw, h0 - (cw if kind == "window" else 0)), (s1 + cw, h0 - (cw if kind == "window" else 0)),
                                (s1 + cw, h1 + cw), (s0 - cw, h1 + cw)]).difference(box(s0, h0, s1, h1))
                o3 = P(0, 0, 0.022)
                casing.planar(ring, (o3[0], o3[1], 0.0), (t[0], t[1], 0), (0, 0, 1), normal=(n[0], n[1], 0), uv_scale=self.uv(cmat))
            if kind == "window":
                glass = get("glass", glass_mat, "glazing")
                glass.quad(P(s0, h0, -gi), P(s1, h0, -gi), P(s1, h1, -gi), P(s0, h1, -gi))
                cols, rows = o.get("grid", [1, 1])
                bar = 0.022
                for c in range(1, cols):
                    s = s0 + (s1 - s0) * c / cols
                    frame.beam(P(s, h0, -gi + 0.012), P(s, h1, -gi + 0.012), bar, 0.02)
                for rr in range(1, rows):
                    h = h0 + (h1 - h0) * rr / rows
                    frame.beam(P(s0, h, -gi + 0.012), P(s1, h, -gi + 0.012), 0.02, bar)
                if o.get("style") == "double_hung":            # meeting rail
                    hm = (h0 + h1) / 2
                    frame.beam(P(s0, hm, -gi + 0.015), P(s1, hm, -gi + 0.015), 0.03, 0.045)
            elif kind == "door":
                dmat = o.get("material", "door")
                slab = get("slab", dmat, "door")
                slab.quad(P(s0 + fw, h0, -gi), P(s1 - fw, h0, -gi), P(s1 - fw, h1 - fw, -gi), P(s0 + fw, h1 - fw, -gi), uv_scale=self.uv(dmat))
                lite = o.get("lite")                              # [bottom fraction, top fraction] of glass
                if lite:
                    glass = get("glass", glass_mat, "door glazing")
                    m = 0.14
                    ha, hb = h0 + (h1 - h0) * lite[0], h0 + (h1 - h0) * lite[1]
                    glass.quad(P(s0 + fw + m, ha, -gi + 0.012), P(s1 - fw - m, ha, -gi + 0.012),
                               P(s1 - fw - m, hb, -gi + 0.012), P(s0 + fw + m, hb, -gi + 0.012))
                    cols, rows = o.get("grid", [1, 1])
                    for c in range(1, cols):
                        s = s0 + fw + m + (s1 - s0 - 2 * fw - 2 * m) * c / cols
                        frame.beam(P(s, ha, -gi + 0.02), P(s, hb, -gi + 0.02), 0.02, 0.016)
                    for rr in range(1, rows):
                        h = ha + (hb - ha) * rr / rows
                        frame.beam(P(s0 + fw + m, h, -gi + 0.02), P(s1 - fw - m, h, -gi + 0.02), 0.016, 0.02)
            elif kind == "garage_door":
                dmat = o.get("material", "garage_door")
                slab = get("slab", dmat, "garage door")
                gi2 = 0.1
                slab.quad(P(s0, h0, -gi2), P(s1, h0, -gi2), P(s1, h1, -gi2), P(s0, h1, -gi2), uv_scale=self.uv(dmat))
                sections = o.get("sections", 4)
                groove = get("groove", o.get("groove_material", "shadow"), "garage door panel lines")
                for k in range(1, sections):
                    h = h0 + (h1 - h0) * k / sections
                    groove.quad(P(s0, h - 0.012, -gi2 + 0.004), P(s1, h - 0.012, -gi2 + 0.004), P(s1, h + 0.012, -gi2 + 0.004), P(s0, h + 0.012, -gi2 + 0.004))
                wins = o.get("windows", 0)
                if wins:
                    glass = get("glass", glass_mat, "garage door glazing")
                    sec_h = (h1 - h0) / sections
                    hb = h1 - sec_h * 0.2
                    ha = h1 - sec_h * 0.8
                    bay = (s1 - s0) / wins
                    for k in range(wins):
                        a = s0 + bay * k + bay * 0.14
                        b_ = s0 + bay * (k + 1) - bay * 0.14
                        glass.quad(P(a, ha, -gi2 + 0.008), P(b_, ha, -gi2 + 0.008), P(b_, hb, -gi2 + 0.008), P(a, hb, -gi2 + 0.008))
                # reveal (jamb returns)
                for (sa, sb) in ((s0, s0), (s1, s1)):
                    pass

    # -- free elements -------------------------------------------------------
    def build_elements(self):
        for i, el in enumerate(self.b.get("elements", [])):
            kind = el["kind"]
            name = el.get("id") or f"{kind}_{i}"
            mat = el.get("material", "trim")
            info = self.info_of(el)
            uv = self.uv(mat)
            if kind == "prism":
                p = self.part(name, mat, info)
                poly = Polygon(el["polygon"], el.get("holes"))
                if not poly.is_valid:
                    poly = poly.buffer(0)
                p.prism(poly, el["z0"], el["z1"], uv_scale=uv)
            elif kind == "post":
                p = self.part(name, mat, info)
                sx, sy = el.get("size", [0.2, 0.2])
                for at in el.get("at_list") or [el["at"]]:
                    z_lo = el["z0"]
                    base = el.get("base")
                    if base:
                        bp = self._subpart(name + "_base", base.get("material", "stone"), info)
                        bsx, bsy = base.get("size", [sx * 2.2, sy * 2.2])
                        bp.box((at[0], at[1], z_lo + base["height"] / 2), (bsx, bsy, base["height"]), el.get("yaw", 0), uv_scale=self.uv(base.get("material", "stone")))
                        cap = self._subpart(name + "_base_cap", base.get("cap_material", "concrete"), info)
                        cap.box((at[0], at[1], z_lo + base["height"] + 0.03), (bsx + 0.08, bsy + 0.08, 0.06), el.get("yaw", 0))
                        z_lo += base["height"] + 0.06
                    p.box((at[0], at[1], (z_lo + el["z1"]) / 2), (sx, sy, el["z1"] - z_lo), el.get("yaw", 0), uv_scale=uv)
            elif kind == "beam":
                p = self.part(name, mat, info)
                for a, b_ in el.get("segments") or [[el["a"], el["b"]]]:
                    p.beam(a, b_, el["size"][0], el["size"][1], uv_scale=uv)
            elif kind == "railing":
                self._railing(name, mat, info, el)
            elif kind == "stairs":
                self._stairs(name, mat, info, el)
            elif kind == "cylinder":
                p = self.part(name, mat, info)
                p.cylinder(el["base"], el["radius"], el["height"], segments=el.get("segments", 14), radius_top=el.get("radius_top"))
            elif kind == "box":
                p = self.part(name, mat, info)
                p.box(el["center"], el["size"], el.get("yaw", 0), uv_scale=uv)
            elif kind == "wall_panel":
                self._wall_panel(name, mat, info, el)
            elif kind == "skylight":
                self._skylight(name, mat, info, el)
            else:
                raise ValueError(f"unknown element kind {kind!r}")

    def _subpart(self, name, material, info):
        name = name.lower()
        for p in self.model.parts:
            if p.name == name:
                return p
        return self.part(name, material, info)

    def _wall_panel(self, name, mat, info, el):
        """A vertical slab between plan points a and b with an arbitrary elevation outline
        (s = metres from a, h = absolute z) - gable infill, screens, arched porch fronts."""
        p = self.part(name, mat, info)
        a = np.asarray(el["a"], dtype=float)
        b_ = np.asarray(el["b"], dtype=float)
        L = float(np.hypot(*(b_ - a)))
        t = (b_ - a) / L
        n = np.array([t[1], -t[0]])
        th = el.get("thickness", 0.14)
        geom = Polygon(el["outline"]) if el.get("outline") else box(0, el["z0"], L, el["z1"])
        for h in el.get("holes", []):
            geom = geom.difference(Polygon(h))
        for ar in el.get("arches", []):
            s0, s1, sill, spring, rise = ar["s0"], ar["s1"], ar["sill_z"], ar["spring_z"], ar.get("rise", 0.0)
            pts = [(s0, sill), (s1, sill), (s1, spring)]
            if rise > 0:
                cx, rx = (s0 + s1) / 2, (s1 - s0) / 2
                pts += [(cx + rx * np.cos(k), spring + rise * np.sin(k)) for k in np.linspace(0, np.pi, 18)[1:-1]]
            pts += [(s0, spring)]
            geom = geom.difference(Polygon(pts))
        if not geom.is_valid:
            geom = geom.buffer(0)
        uv = self.uv(mat)
        for sign in (1, -1):
            o = a + n * (th / 2) * sign
            p.planar(geom, (o[0], o[1], 0.0), (t[0], t[1], 0), (0, 0, 1), normal=(n[0] * sign, n[1] * sign, 0), uv_scale=uv)
        from shapely.geometry.polygon import orient
        for poly in polygon_parts(geom):
            poly = orient(poly, 1.0)
            for ring in [poly.exterior, *poly.interiors]:
                c = np.asarray(ring.coords)
                for k in range(len(c) - 1):
                    (sa, ha), (sb, hb) = c[k], c[k + 1]
                    ds, dh = sb - sa, hb - ha
                    ln = np.hypot(ds, dh)
                    if ln < 1e-6:
                        continue
                    out2 = np.array([dh, -ds]) / ln                  # outward for CCW exterior / CW holes
                    facing = np.array([t[0] * out2[0], t[1] * out2[0], out2[1]])
                    def P3(sv, hv, off):
                        xy = a + t * sv + n * off
                        return np.array([xy[0], xy[1], hv])
                    q = [P3(sa, ha, th / 2), P3(sb, hb, th / 2), P3(sb, hb, -th / 2), P3(sa, ha, -th / 2)]
                    nn = np.cross(q[1] - q[0], q[2] - q[0])
                    if np.dot(nn, facing) < 0:
                        q = q[::-1]
                    p.polygon3d(q, uv_scale=uv)

    def _skylight(self, name, mat, info, el):
        """Curb + glass lying on a wing's roof plane at a plan position."""
        wing = next(w for w in self.wings if w.id == el["wing"])
        x, y = el["at"]
        plane = min(wing.planes, key=lambda pl: pl[0] * x + pl[1] * y + pl[2])
        u, v, n = plane_basis(plane)
        w_, l_ = el.get("size", [0.6, 1.2])
        c = np.array([x, y, plane[0] * x + plane[1] * y + plane[2]])
        curb = self.part(name, mat, info)
        h = el.get("curb", 0.1)
        corners = [c + u * sx * w_ / 2 + v * sy * l_ / 2 for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))]
        for k in range(4):
            curb.beam(corners[k] + n * h / 2, corners[(k + 1) % 4] + n * h / 2, 0.07, h)
        glass = self.part(name + "_glass", el.get("glass_material", "glass"), info)
        g = [q + n * (h * 0.85) for q in corners]
        glass.polygon3d(g)

    def _railing(self, name, mat, info, el):
        p = self.part(name, mat, info)
        path = np.asarray(el["path"], dtype=float)
        z = el["z"]
        h = el.get("height", 0.95)
        style = el.get("style", "balusters")
        post = el.get("post", 0.09)
        for i in range(len(path) - 1):
            a, b_ = path[i], path[i + 1]
            L = float(np.hypot(*(b_ - a)))
            if L < 1e-6:
                continue
            p.beam([a[0], a[1], z + h], [b_[0], b_[1], z + h], 0.09, 0.05)
            n_posts = max(1, int(np.ceil(L / el.get("post_spacing", 1.8))))
            for k in range(n_posts + 1):
                c = a + (b_ - a) * k / n_posts
                p.box((c[0], c[1], z + h / 2), (post, post, h))
            if style == "balusters":
                p.beam([a[0], a[1], z + 0.09], [b_[0], b_[1], z + 0.09], 0.05, 0.04)
                nb = max(1, int(L / el.get("baluster_spacing", 0.115)))
                for k in range(1, nb):
                    c = a + (b_ - a) * k / nb
                    p.box((c[0], c[1], z + (h + 0.09) / 2), (0.02, 0.02, h - 0.09))
            else:                                               # horizontal rails / cables
                rails = el.get("rails", 5)
                for k in range(1, rails + 1):
                    zz = z + h * k / (rails + 1)
                    p.beam([a[0], a[1], zz], [b_[0], b_[1], zz], 0.02, 0.02)

    def _stairs(self, name, mat, info, el):
        p = self.part(name, mat, info)
        top = np.asarray(el["top"], dtype=float)            # [x, y] centre of the top nosing
        direction = np.asarray(el["direction"], dtype=float)
        direction /= np.linalg.norm(direction)
        z_top, z_bot = el["z_top"], el["z_bottom"]
        rise_total = z_top - z_bot
        n = max(1, int(round(rise_total / el.get("riser", 0.18))))
        riser = rise_total / n
        tread = el.get("tread", 0.28)
        width = el["width"]
        yaw = np.degrees(np.arctan2(direction[1], direction[0]))
        solid = el.get("solid", True)
        for k in range(1, n):
            c = top + direction * (tread * (k - 0.5))
            zt = z_top - riser * k
            if solid:
                p.box((c[0], c[1], (zt + z_bot - 0.1) / 2), (tread, width, zt - z_bot + 0.1), yaw, uv_scale=self.uv(mat))
            else:
                p.box((c[0], c[1], zt - 0.02), (tread, width, 0.04), yaw, uv_scale=self.uv(mat))

    # -- run -----------------------------------------------------------------
    def run(self):
        self.build_roofs()
        self.build_walls()
        self.build_openings()
        self.build_elements()
        path = os.path.join(self.out_dir, f"{self.b['id']}.glb")
        stats = self.model.write_glb(path)
        used = {p.name for p in self.model.parts if p.tri_count}
        info = {k: v for k, v in self.parts_info.items() if k in used}
        return path, stats, info


def main():
    spec_path, out_dir = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path))
    os.makedirs(out_dir, exist_ok=True)
    all_parts = {}
    report = {}
    for b in spec["buildings"]:
        path, stats, info = Builder(spec, b, out_dir).run()
        dupes = set(all_parts) & set(info)
        if dupes:
            raise ValueError(f"duplicate part names across buildings: {sorted(dupes)[:5]}")
        all_parts.update(info)
        report[b["id"]] = stats
    json.dump(all_parts, open(os.path.join(out_dir, "parts.json"), "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
