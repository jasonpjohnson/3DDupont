"""Small mesh builder and binary glTF (GLB) writer for the house model.

Everything upstream works in the *site frame*: x = east, y = north, z = up, metres.
The writer converts to glTF's right-handed Y-up frame:

    X_gltf = x          Y_gltf = z - z0          Z_gltf = -y

so the model opens the right way up in any glTF viewer, with north along -Z.

Faces are flat shaded: every planar polygon gets its own vertices and one normal.
UVs are in metres divided by the material's ``tex_size_m`` so textures tile at a
physical size regardless of the face they are on.
"""
from __future__ import annotations

import io
import json
import struct

import mapbox_earcut as earcut
import numpy as np
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient


def srgb_hex_to_linear(hex_color: str, alpha: float = 1.0):
    h = hex_color.lstrip("#")
    rgb = [int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return [round(v, 5) for v in lin] + [alpha]


def _unit(v):
    v = np.asarray(v, dtype=float)
    n = np.linalg.norm(v)
    return v / n if n > 0 else v


def triangulate_rings(rings2d):
    """Earcut a polygon given as [outer, hole, hole...] of (n,2) arrays.
    Returns (vertices (m,2), triangles (k,3))."""
    verts = np.vstack(rings2d).astype(np.float64)
    ends = np.cumsum([len(r) for r in rings2d]).astype(np.uint32)
    tris = earcut.triangulate_float64(verts, ends).reshape(-1, 3)
    return verts, tris


def polygon_parts(geom):
    """Yield simple shapely Polygons from Polygon / MultiPolygon / collections."""
    if geom is None or geom.is_empty:
        return
    if isinstance(geom, Polygon):
        if geom.area > 1e-9:
            yield geom
    elif isinstance(geom, MultiPolygon) or hasattr(geom, "geoms"):
        for g in geom.geoms:
            yield from polygon_parts(g)


class Part:
    """One named mesh with a single material; becomes one glTF node."""

    def __init__(self, name: str, material: str, extras: dict | None = None):
        self.name = name
        self.material = material
        self.extras = dict(extras or {})
        self.pos: list[np.ndarray] = []
        self.nrm: list[np.ndarray] = []
        self.uv: list[np.ndarray] = []
        self.idx: list[np.ndarray] = []
        self._n = 0

    # -- low level ---------------------------------------------------------
    def add(self, pos, normal, uv, tris):
        pos = np.asarray(pos, dtype=np.float64).reshape(-1, 3)
        tris = np.asarray(tris, dtype=np.int64).reshape(-1, 3)
        if len(pos) == 0 or len(tris) == 0:
            return
        normal = np.asarray(normal, dtype=np.float64)
        if normal.ndim == 1:
            normal = np.tile(_unit(normal), (len(pos), 1))
        uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        self.pos.append(pos)
        self.nrm.append(normal)
        self.uv.append(uv)
        self.idx.append(tris + self._n)
        self._n += len(pos)

    @property
    def tri_count(self):
        return int(sum(len(i) for i in self.idx))

    # -- planar polygons ---------------------------------------------------
    def planar(self, geom2d, origin, u, v, normal=None, uv_scale=1.0, uv_offset=(0.0, 0.0),
               flip=False):
        """Add a shapely (Multi)Polygon that lives in a plane.

        A 2D point (s, t) maps to ``origin + s*u + t*v`` in the site frame.
        ``normal`` defaults to u x v. UV = ((s, t) + uv_offset) * uv_scale.
        ``flip`` reverses the facing (normal and winding)."""
        origin = np.asarray(origin, dtype=float)
        u = np.asarray(u, dtype=float)
        v = np.asarray(v, dtype=float)
        n = _unit(np.cross(u, v)) if normal is None else _unit(normal)
        if flip:
            n = -n
        for poly in polygon_parts(geom2d):
            poly = orient(poly, 1.0)
            rings = [np.asarray(poly.exterior.coords)[:-1]]
            rings += [np.asarray(r.coords)[:-1] for r in poly.interiors]
            rings = [r for r in rings if len(r) >= 3]
            if not rings:
                continue
            verts, tris = triangulate_rings(rings)
            if len(tris) == 0:
                continue
            p3 = origin + verts[:, :1] * u + verts[:, 1:2] * v
            # make the winding agree with the requested normal
            a, b, c = p3[tris[0]]
            if np.dot(np.cross(b - a, c - a), n) < 0:
                tris = tris[:, ::-1]
            uv = (verts + np.asarray(uv_offset)) * uv_scale
            self.add(p3, n, uv, tris)

    def polygon3d(self, pts, uv_scale=1.0, flip=False, uv_axis=None):
        """Add a planar polygon given by 3D vertices (convex or concave, no holes).
        UV u-axis follows the horizontal direction in the plane (or ``uv_axis``),
        v runs up-slope."""
        pts = np.asarray(pts, dtype=float)
        if len(pts) < 3:
            return
        # Newell normal
        n = np.zeros(3)
        for i in range(len(pts)):
            a, b = pts[i], pts[(i + 1) % len(pts)]
            n += np.array([(a[1] - b[1]) * (a[2] + b[2]), (a[2] - b[2]) * (a[0] + b[0]),
                           (a[0] - b[0]) * (a[1] + b[1])])
        if np.linalg.norm(n) < 1e-12:
            return
        n = _unit(n)
        if uv_axis is not None:
            u = _unit(np.asarray(uv_axis, dtype=float) - np.dot(uv_axis, n) * n)
        else:
            up = np.array([0.0, 0.0, 1.0])
            u = np.cross(up, n)
            u = _unit(u) if np.linalg.norm(u) > 1e-6 else np.array([1.0, 0.0, 0.0])
        v = np.cross(n, u)
        o = pts[0]
        st = np.c_[(pts - o) @ u, (pts - o) @ v]
        poly = Polygon(st)
        if not poly.is_valid:
            poly = poly.buffer(0)
        self.planar(poly, o, u, v, normal=n, uv_scale=uv_scale, flip=flip)

    def quad(self, a, b, c, d, uv_scale=1.0, flip=False):
        self.polygon3d([a, b, c, d], uv_scale=uv_scale, flip=flip)

    # -- solids --------------------------------------------------------------
    def prism(self, footprint, z0, z1, uv_scale=1.0, cap_bottom=True, cap_top=True, sides=True):
        """Vertical extrusion of a shapely polygon (holes respected) from z0 to z1."""
        for poly in polygon_parts(footprint):
            poly = orient(poly, 1.0)
            if cap_top:
                self.planar(poly, (0, 0, z1), (1, 0, 0), (0, 1, 0), normal=(0, 0, 1), uv_scale=uv_scale)
            if cap_bottom:
                self.planar(poly, (0, 0, z0), (1, 0, 0), (0, 1, 0), normal=(0, 0, -1), uv_scale=uv_scale)
            if not sides:
                continue
            rings = [(np.asarray(poly.exterior.coords), False)]
            rings += [(np.asarray(r.coords), True) for r in poly.interiors]
            for ring, _ in rings:
                run = 0.0
                for i in range(len(ring) - 1):
                    p, q = ring[i], ring[i + 1]
                    d = q - p
                    L = float(np.hypot(*d))
                    if L < 1e-9:
                        continue
                    # exterior ring is CCW -> outward normal is to the right of travel;
                    # holes come out CW after orient(), so the same rule faces into the hole.
                    n = np.array([d[1], -d[0], 0.0]) / L
                    pos = [[p[0], p[1], z0], [q[0], q[1], z0], [q[0], q[1], z1], [p[0], p[1], z1]]
                    uv = np.array([[run, z0], [run + L, z0], [run + L, z1], [run, z1]]) * uv_scale
                    self.add(pos, n, uv, [[0, 1, 2], [0, 2, 3]])
                    run += L

    def box(self, center, size, yaw_deg=0.0, uv_scale=1.0):
        """Axis-aligned box of ``size`` (sx, sy, sz) centred at ``center``, rotated about z."""
        cx, cy, cz = center
        sx, sy, sz = size
        c, s = np.cos(np.radians(yaw_deg)), np.sin(np.radians(yaw_deg))
        corners = [(-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2)]
        ring = [(cx + x * c - y * s, cy + x * s + y * c) for x, y in corners]
        self.prism(Polygon(ring), cz - sz / 2, cz + sz / 2, uv_scale=uv_scale)

    def beam(self, p0, p1, width, depth, uv_scale=1.0):
        """Rectangular member from p0 to p1 (3D points on its centre line)."""
        p0 = np.asarray(p0, dtype=float)
        p1 = np.asarray(p1, dtype=float)
        axis = p1 - p0
        L = np.linalg.norm(axis)
        if L < 1e-9:
            return
        a = axis / L
        ref = np.array([0.0, 0.0, 1.0]) if abs(a[2]) < 0.95 else np.array([1.0, 0.0, 0.0])
        side = _unit(np.cross(a, ref))
        up = np.cross(side, a)
        hw, hd = width / 2, depth / 2
        ring = [(-hw, -hd), (hw, -hd), (hw, hd), (-hw, hd)]
        c0 = [p0 + side * x + up * y for x, y in ring]
        c1 = [p1 + side * x + up * y for x, y in ring]
        for i in range(4):
            j = (i + 1) % 4
            self.quad(c0[i], c0[j], c1[j], c1[i], uv_scale=uv_scale)
        self.polygon3d(c0[::-1], uv_scale=uv_scale)
        self.polygon3d(c1, uv_scale=uv_scale)

    def cylinder(self, base, radius, height, segments=12, uv_scale=1.0, radius_top=None):
        bx, by, bz = base
        rt = radius if radius_top is None else radius_top
        ang = np.linspace(0, 2 * np.pi, segments + 1)
        for i in range(segments):
            a0, a1 = ang[i], ang[i + 1]
            p = [[bx + radius * np.cos(a0), by + radius * np.sin(a0), bz],
                 [bx + radius * np.cos(a1), by + radius * np.sin(a1), bz],
                 [bx + rt * np.cos(a1), by + rt * np.sin(a1), bz + height],
                 [bx + rt * np.cos(a0), by + rt * np.sin(a0), bz + height]]
            nrm = np.array([[np.cos(a0), np.sin(a0), 0], [np.cos(a1), np.sin(a1), 0],
                            [np.cos(a1), np.sin(a1), 0], [np.cos(a0), np.sin(a0), 0]])
            uv = np.array([[a0 * radius, 0], [a1 * radius, 0], [a1 * radius, height], [a0 * radius, height]]) * uv_scale
            self.add(p, nrm, uv, [[0, 1, 2], [0, 2, 3]])
        if rt > 1e-6:
            top = Polygon([(bx + rt * np.cos(a), by + rt * np.sin(a)) for a in ang[:-1]])
            self.planar(top, (0, 0, bz + height), (1, 0, 0), (0, 1, 0), normal=(0, 0, 1), uv_scale=uv_scale)

    # -- export helpers ------------------------------------------------------
    def arrays(self):
        if not self.pos:
            return None
        return (np.vstack(self.pos), np.vstack(self.nrm), np.vstack(self.uv), np.vstack(self.idx))


class Model:
    """A collection of parts + materials that can be written as a .glb."""

    def __init__(self, z0: float = 0.0):
        self.z0 = z0
        self.parts: list[Part] = []
        self.materials: dict[str, dict] = {}

    def material(self, name, color="#cccccc", roughness=0.9, metallic=0.0, texture=None,
                 tex_size_m=1.0, double_sided=False, alpha=1.0, emissive=None, extras=None):
        """Register a material. ``texture`` is a PIL image (tiles every tex_size_m metres)."""
        self.materials[name] = dict(color=color, roughness=roughness, metallic=metallic,
                                    texture=texture, tex_size_m=tex_size_m,
                                    double_sided=double_sided, alpha=alpha, emissive=emissive,
                                    extras=extras or {})
        return name

    def uv_scale(self, material):
        return 1.0 / float(self.materials[material]["tex_size_m"])

    def part(self, name, material, **extras) -> Part:
        if material not in self.materials:
            raise KeyError(f"unknown material {material!r}")
        p = Part(name, material, extras)
        self.parts.append(p)
        return p

    def stats(self):
        return {p.name: p.tri_count for p in self.parts if p.tri_count}

    def write_glb(self, path, generator="3DDupont build"):
        buf = io.BytesIO()
        views, accessors, meshes, nodes = [], [], [], []
        images, textures, materials, mat_index = [], [], [], {}

        def add_view(data: bytes, target=None):
            while buf.tell() % 4:
                buf.write(b"\x00")
            off = buf.tell()
            buf.write(data)
            view = {"buffer": 0, "byteOffset": off, "byteLength": len(data)}
            if target:
                view["target"] = target
            views.append(view)
            return len(views) - 1

        def add_accessor(arr, comp, typ, target=None, minmax=False):
            v = add_view(arr.tobytes(), target)
            acc = {"bufferView": v, "componentType": comp, "count": int(len(arr)), "type": typ}
            if minmax:
                acc["min"] = [float(x) for x in arr.min(axis=0)]
                acc["max"] = [float(x) for x in arr.max(axis=0)]
            accessors.append(acc)
            return len(accessors) - 1

        used = [p for p in self.parts if p.tri_count]
        for name in dict.fromkeys(p.material for p in used):
            m = self.materials[name]
            pbr = {"baseColorFactor": srgb_hex_to_linear(m["color"], m["alpha"]),
                   "metallicFactor": float(m["metallic"]), "roughnessFactor": float(m["roughness"])}
            if m["texture"] is not None:
                img = io.BytesIO()
                tex = m["texture"]
                fmt = "JPEG" if tex.mode == "RGB" else "PNG"
                tex.save(img, format=fmt, **({"quality": 88} if fmt == "JPEG" else {"optimize": True}))
                iv = add_view(img.getvalue())
                images.append({"bufferView": iv, "mimeType": "image/jpeg" if fmt == "JPEG" else "image/png"})
                textures.append({"source": len(images) - 1, "sampler": 0})
                pbr["baseColorTexture"] = {"index": len(textures) - 1}
                pbr["baseColorFactor"] = [1.0, 1.0, 1.0, m["alpha"]]
            mat = {"name": name, "pbrMetallicRoughness": pbr, "doubleSided": bool(m["double_sided"])}
            if m["alpha"] < 1.0:
                mat["alphaMode"] = "BLEND"
            if m["emissive"]:
                mat["emissiveFactor"] = srgb_hex_to_linear(m["emissive"])[:3]
            if m["extras"]:
                mat["extras"] = m["extras"]
            mat_index[name] = len(materials)
            materials.append(mat)

        for p in used:
            pos, nrm, uv, idx = p.arrays()
            g = np.c_[pos[:, 0], pos[:, 2] - self.z0, -pos[:, 1]].astype(np.float32)
            gn = np.c_[nrm[:, 0], nrm[:, 2], -nrm[:, 1]].astype(np.float32)
            ln = np.linalg.norm(gn, axis=1, keepdims=True)
            gn = gn / np.where(ln > 0, ln, 1)
            a_pos = add_accessor(g, 5126, "VEC3", 34962, minmax=True)
            a_nrm = add_accessor(gn.astype(np.float32), 5126, "VEC3", 34962)
            a_uv = add_accessor(uv.astype(np.float32), 5126, "VEC2", 34962)
            a_idx = add_accessor(idx.astype(np.uint32).reshape(-1), 5125, "SCALAR", 34963)
            meshes.append({"name": p.name, "primitives": [{
                "attributes": {"POSITION": a_pos, "NORMAL": a_nrm, "TEXCOORD_0": a_uv},
                "indices": a_idx, "material": mat_index[p.material]}]})
            node = {"name": p.name, "mesh": len(meshes) - 1}
            if p.extras:
                node["extras"] = p.extras
            nodes.append(node)

        while buf.tell() % 4:
            buf.write(b"\x00")
        bin_data = buf.getvalue()
        gltf = {"asset": {"version": "2.0", "generator": generator},
                "scene": 0, "scenes": [{"nodes": list(range(len(nodes)))}],
                "nodes": nodes, "meshes": meshes, "materials": materials,
                "accessors": accessors, "bufferViews": views,
                "buffers": [{"byteLength": len(bin_data)}]}
        if textures:
            gltf["textures"] = textures
            gltf["images"] = images
            gltf["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
        js = json.dumps(gltf, separators=(",", ":")).encode()
        js += b" " * ((4 - len(js) % 4) % 4)
        total = 12 + 8 + len(js) + 8 + len(bin_data)
        with open(path, "wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, total))
            f.write(struct.pack("<I4s", len(js), b"JSON"))
            f.write(js)
            f.write(struct.pack("<I4s", len(bin_data), b"BIN\x00"))
            f.write(bin_data)
        return {"bytes": total, "parts": len(nodes), "triangles": int(sum(p.tri_count for p in used))}
