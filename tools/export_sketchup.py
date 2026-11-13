#!/usr/bin/env python3
"""Package the built model for SketchUp.

  python3 tools/export_sketchup.py assets/ [--spec spec/site_spec.json] [--name 3910-w-dupont]

Reads the .glb files the builders wrote into <assets>/ (buildings and site works), the part
catalogues beside them (labels) and the terrain grid, and writes <assets>/<name>-sketchup.zip:

  <name>.dae         COLLADA 1.4.1, metres, Z up - File > Import in SketchUp
  textures/*.jpg     the material textures the .dae refers to
  README.txt         how to import, what is in the file

The zip holds exactly the model and its texture folder (plus the note), which is the layout
SketchUp for Web wants when a .dae with textures is dropped onto it; on the desktop it is unzipped
and the .dae imported. SketchUp Free (web) imports neither COLLADA nor STL - only .skp and images.

The .dae is in the site frame: x east, y north (grid north), z up, metres; the origin is the address
point and z = 0 is the ground at the front door. Each building is one node, each named part a node
inside it; site works (paving, lawn, fence) and the ground are nodes of their own. Closed shells are
re-wound to face outward (SketchUp draws the back of a face in another colour), sliver and duplicate
triangles are dropped, and every material is opaque. Trees are not exported.

Run it after build_site.py / build_model.py and before build_manifest.py, which then points the
page's download button at the package.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import io
import json
import os
import re
import struct
import zipfile
from xml.sax.saxutils import escape, quoteattr

import numpy as np

COMPONENT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
WIDTH = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
GLASS = (0.29, 0.345, 0.388)            # the slate the page draws window panes in (sRGB 0..1)
GROUND = (0.42, 0.47, 0.33)
ORDER = ["house", "garage_detached", "pole_barn", "greenhouse", "garden"]


def read_glb(path):
    raw = open(path, "rb").read()
    magic, _, _ = struct.unpack("<III", raw[:12])
    if magic != 0x46546C67:
        raise ValueError(f"{path}: not a binary glTF file")
    n, kind = struct.unpack("<II", raw[12:20])
    if kind != 0x4E4F534A:
        raise ValueError(f"{path}: first chunk is not JSON")
    gltf = json.loads(raw[20:20 + n])
    off = 20 + n
    m, kind = struct.unpack("<II", raw[off:off + 8])
    if kind != 0x004E4942:
        raise ValueError(f"{path}: second chunk is not BIN")
    return gltf, raw[off + 8:off + 8 + m]


def accessor(gltf, blob, index):
    a = gltf["accessors"][index]
    v = gltf["bufferViews"][a["bufferView"]]
    dtype = np.dtype(COMPONENT[a["componentType"]])
    width = WIDTH[a["type"]]
    start = v.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = v.get("byteStride") or dtype.itemsize * width
    if stride == dtype.itemsize * width:
        arr = np.frombuffer(blob, dtype=dtype, count=a["count"] * width, offset=start)
    else:                                                    # interleaved view
        rows = np.lib.stride_tricks.as_strided(np.frombuffer(blob, dtype=np.uint8, offset=start),
                                               shape=(a["count"], dtype.itemsize * width), strides=(stride, 1))
        arr = np.ascontiguousarray(rows).view(dtype)
    return arr.reshape(a["count"], width) if width > 1 else arr.reshape(a["count"])


def srgb(c):
    """linear 0..1 -> sRGB 0..1 (glTF base colours are linear; SketchUp colours are display values)."""
    c = np.clip(np.asarray(c, dtype=float), 0, 1)
    return np.where(c <= 0.0031308, 12.92 * c, 1.055 * np.power(c, 1 / 2.4) - 0.055)


def ncname(text, used=None, prefix="n"):
    """An XML NCName (COLLADA ids and names): letters, digits, '_', '-', '.'; unique if `used` is given."""
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", text.replace("—", "-").replace("–", "-")).strip("_.-") or prefix
    s = re.sub(r"_+", "_", s)
    if not re.match(r"[A-Za-z_]", s):
        s = f"{prefix}_{s}"
    if used is not None:
        base, k = s, 2
        while s in used:
            s, k = f"{base}_{k}", k + 1
        used.add(s)
    return s


def orient_outward(pos, tri):
    """Flip shells that are wound inside-out: SketchUp shows the back of a face in its own colour. A shell
    counts if it is closed or nearly so (under a tenth of its edges open); sheets and shells of mixed
    winding are left as built."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    key, inv = np.unique(np.round(pos, 4), axis=0, return_inverse=True)
    t = inv.reshape(-1)[tri]
    live = (t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])
    n = len(key)
    _, lab = connected_components(coo_matrix((np.ones(t.size), (t.ravel(), np.roll(t, -1, axis=1).ravel())), shape=(n, n)), directed=False)
    tl = lab[t[:, 0]]
    out = tri.copy()
    for c in np.unique(tl[live]):
        sel = np.flatnonzero((tl == c) & live)
        tt = t[sel]
        e = np.vstack([tt[:, [0, 1]], tt[:, [1, 2]], tt[:, [2, 0]]])
        fwd, rev = e[:, 0] * n + e[:, 1], e[:, 1] * n + e[:, 0]
        if len(np.unique(fwd)) != len(fwd):
            continue                                         # an edge used twice the same way: winding is mixed
        open_edges = len(fwd) - int(np.isin(fwd, rev).sum())
        if open_edges > 0.1 * len(fwd):
            continue                                         # a sheet, not a (nearly) closed shell: leave as built
        a, b, d = key[tt[:, 0]], key[tt[:, 1]], key[tt[:, 2]]
        o = a.mean(0)
        if np.einsum("ij,ij->i", a - o, np.cross(b - o, d - o)).sum() < 0:
            out[sel] = out[sel][:, ::-1]
    return out


def clean(rows, tri):
    """Weld equal rows, then drop triangles SketchUp would discard or cannot hold: collapsed ones, slivers
    of no area, and a second triangle on the same three corners."""
    uniq, inverse = np.unique(rows, axis=0, return_inverse=True)
    t = inverse.reshape(-1)[tri]
    t = t[(t[:, 0] != t[:, 1]) & (t[:, 1] != t[:, 2]) & (t[:, 0] != t[:, 2])]
    if len(t):
        p = uniq[:, :3]
        e = np.stack([p[t[:, 1]] - p[t[:, 0]], p[t[:, 2]] - p[t[:, 1]], p[t[:, 0]] - p[t[:, 2]]])
        area = 0.5 * np.linalg.norm(np.cross(e[0], -e[2]), axis=1)
        longest = np.linalg.norm(e, axis=2).max(0)
        t = t[(area > 1e-8) & (2 * area > 2e-4 * longest)]     # no area, or a needle under 0.2 mm high
    if len(t):
        _, first = np.unique(np.sort(t, axis=1), axis=0, return_index=True)
        t = t[np.sort(first)]
    return uniq, t


def fmt(arr, digits):
    return " ".join(np.char.rstrip(np.char.rstrip(np.char.mod(f"%.{digits}f", arr.ravel()), "0"), ".").tolist())


class Package:
    def __init__(self):
        self.materials = {}          # signature -> dict(id, name, rgb, alpha, image)
        self.images = {}             # sha1 -> (file name, bytes)
        self.groups = []             # (group name, [(part name, positions, uvs or None, triangles, material id)])
        self.used_ids = set()
        self.used_files = set()
        self.used_names = set()

    def material(self, name, label, rgba, image_bytes, mime="image/jpeg"):
        if "glass" in name.lower() and image_bytes is None:
            rgb, alpha = GLASS, 1.0                       # opaque slate: the buildings are shells, with nothing to see inside
        else:
            # opaque throughout: importers disagree on whether COLLADA's <transparency> means opacity, and a
            # panel read the wrong way round arrives invisible
            rgb, alpha = tuple(float(v) for v in srgb(rgba[:3])), 1.0
        digest = hashlib.sha1(image_bytes).hexdigest() if image_bytes else ""
        sig = (label, tuple(round(v, 4) for v in rgb), round(alpha, 3), digest)
        if sig in self.materials:
            return self.materials[sig]["id"]
        image = None
        if image_bytes:
            if digest not in self.images:
                ext = ".png" if mime == "image/png" else ".jpg"
                fname = ncname(name, self.used_files, "tex") + ext
                self.images[digest] = (fname, image_bytes)
            image = self.images[digest][0]
        mid = ncname("mat_" + name, self.used_ids, "mat")
        self.materials[sig] = dict(id=mid, name=ncname(label, self.used_names, "material"), rgb=rgb, alpha=alpha, image=image)
        return mid

    def add_glb(self, path, group_name, labels):
        gltf, blob = read_glb(path)
        mats = gltf.get("materials", [])
        parts = []
        for ni in gltf["scenes"][gltf.get("scene", 0)]["nodes"]:
            node = gltf["nodes"][ni]
            if any(k in node for k in ("matrix", "translation", "rotation", "scale", "children")):
                raise NotImplementedError(f"{path}: node {node.get('name')} carries a transform or children")
            if "mesh" not in node:
                continue
            info = labels.get(node.get("name"), {})
            for prim in gltf["meshes"][node["mesh"]]["primitives"]:
                if prim.get("mode", 4) != 4:
                    continue
                pos = accessor(gltf, blob, prim["attributes"]["POSITION"]).astype(np.float64)
                uv = accessor(gltf, blob, prim["attributes"]["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in prim["attributes"] else None
                idx = accessor(gltf, blob, prim["indices"]).astype(np.int64) if "indices" in prim else np.arange(len(pos))
                tri = idx.reshape(-1, 3)
                # glTF is Y up with -Z north; the site frame (and SketchUp) is Z up with +Y north
                site = np.c_[pos[:, 0], -pos[:, 2], pos[:, 1]]
                tri = orient_outward(site, tri)
                m = mats[prim["material"]] if "material" in prim else {"name": "default"}
                pbr = m.get("pbrMetallicRoughness", {})
                rgba = pbr.get("baseColorFactor", [1, 1, 1, 1])
                img = None
                mime = "image/jpeg"
                if "baseColorTexture" in pbr:
                    source = gltf["images"][gltf["textures"][pbr["baseColorTexture"]["index"]]["source"]]
                    view = gltf["bufferViews"][source["bufferView"]]
                    img = bytes(blob[view.get("byteOffset", 0):view.get("byteOffset", 0) + view["byteLength"]])
                    mime = source.get("mimeType", mime)
                else:
                    uv = None
                mid = self.material(m.get("name", "material"), info.get("material") or m.get("name", "material"), rgba, img, mime)
                if uv is not None:
                    uv = np.c_[uv[:, 0], 1.0 - uv[:, 1]]                  # glTF v runs down the image, COLLADA v up
                if m.get("doubleSided"):
                    # a board fence is one sheet on the page, drawn from both sides; SketchUp paints only the
                    # front of an imported face, so give the sheet a second skin 2 cm behind, facing the other way
                    fn = np.cross(site[tri[:, 1]] - site[tri[:, 0]], site[tri[:, 2]] - site[tri[:, 0]])
                    vn = np.zeros_like(site)
                    for k in range(3):
                        np.add.at(vn, tri[:, k], fn)
                    ln = np.linalg.norm(vn, axis=1, keepdims=True)
                    vn = np.divide(vn, ln, out=np.zeros_like(vn), where=ln > 0)
                    n0 = len(site)
                    site = np.vstack([site, site - 0.02 * vn])
                    tri = np.vstack([tri, tri[:, ::-1] + n0])
                    if uv is not None:
                        uv = np.vstack([uv, uv])
                parts.append((info.get("label") or node.get("name", "part"), site, uv, tri, mid))
        self.groups.append((group_name, parts))

    def add_ground(self, layers_path, assets, z0, margin=12.0):
        t = json.load(open(layers_path))["terrain"]
        raw = np.fromfile(os.path.join(assets, t["file"]), dtype="<u2").reshape(t["ny"], t["nx"])
        z = t["zmin"] + raw.astype(np.float64) * t["zscale"] - z0
        pts = np.vstack([p[1][:, :2] for _, parts in self.groups for p in parts])
        x0, y0 = pts.min(0) - margin
        x1, y1 = pts.max(0) + margin
        i0 = max(int(np.floor((x0 - t["x0"]) / t["cell"])), 0)
        i1 = min(int(np.ceil((x1 - t["x0"]) / t["cell"])), t["nx"] - 1)
        j0 = max(int(np.floor((y0 - t["y0"]) / t["cell"])), 0)
        j1 = min(int(np.ceil((y1 - t["y0"]) / t["cell"])), t["ny"] - 1)
        xs = t["x0"] + np.arange(i0, i1 + 1) * t["cell"]
        ys = t["y0"] + np.arange(j0, j1 + 1) * t["cell"]
        X, Y = np.meshgrid(xs, ys)
        pos = np.c_[X.ravel(), Y.ravel(), z[j0:j1 + 1, i0:i1 + 1].ravel()]
        w = len(xs)
        # only the ground near something built: the drive runs 170 m to the road, and a full rectangle
        # round it would be mostly empty field
        from scipy import ndimage
        near = np.zeros((len(ys) - 1, w - 1), dtype=bool)
        ci = np.clip(((pts[:, 0] - xs[0]) / t["cell"]).astype(int), 0, w - 2)
        cj = np.clip(((pts[:, 1] - ys[0]) / t["cell"]).astype(int), 0, len(ys) - 2)
        near[cj, ci] = True
        r = int(round(margin / t["cell"]))
        near = ndimage.binary_dilation(near, structure=np.ones((3, 3), dtype=bool), iterations=r)
        a = (np.arange(len(ys) - 1)[:, None] * w + np.arange(w - 1)[None, :])[near]
        b, c, d = a + 1, a + w, a + w + 1
        tri = np.vstack([np.c_[a, b, c], np.c_[b, d, c]])          # the diagonal the viewer and the draped paving use
        used, tri = np.unique(tri, return_inverse=True)
        pos, tri = pos[used], tri.reshape(-1, 3)
        sig = ("Ground", GROUND, 1.0, "")
        mid = ncname("mat_ground", self.used_ids, "mat")
        self.materials[sig] = dict(id=mid, name=ncname("Ground", self.used_names), rgb=GROUND, alpha=1.0, image=None)
        self.groups.append(("Ground", [("Ground surface (2017 survey, regraded where rebuilt)", pos, None, tri, mid)]))
        return (float(xs[0]), float(ys[0]), float(xs[-1]), float(ys[-1]))

    # ------------------------------------------------------------------ COLLADA
    def dae(self, title):
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        out = ['<?xml version="1.0" encoding="utf-8"?>',
               '<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">',
               f'<asset><contributor><authoring_tool>3DDupont tools/export_sketchup.py</authoring_tool></contributor>'
               f'<created>{now}</created><modified>{now}</modified><unit name="meter" meter="1"/><up_axis>Z_UP</up_axis></asset>']
        mats = list(self.materials.values())
        out.append("<library_images>")
        for m in mats:
            if m["image"]:
                out.append(f'<image id="{m["id"]}-image" name="{m["id"]}-image"><init_from>textures/{escape(m["image"])}</init_from></image>')
        out.append("</library_images>")
        out.append("<library_effects>")
        for m in mats:
            r, g, b = m["rgb"]
            if m["image"]:
                params = (f'<newparam sid="{m["id"]}-surface"><surface type="2D"><init_from>{m["id"]}-image</init_from></surface></newparam>'
                          f'<newparam sid="{m["id"]}-sampler"><sampler2D><source>{m["id"]}-surface</source></sampler2D></newparam>')
                diffuse = f'<diffuse><texture texture="{m["id"]}-sampler" texcoord="UVSET0"/></diffuse>'
            else:
                params = ""
                diffuse = f"<diffuse><color>{r:.4f} {g:.4f} {b:.4f} 1</color></diffuse>"
            alpha = ""
            if m["alpha"] < 0.999:
                alpha = f'<transparent opaque="A_ONE"><color>1 1 1 1</color></transparent><transparency><float>{m["alpha"]:.3f}</float></transparency>'
            out.append(f'<effect id="{m["id"]}-effect"><profile_COMMON>{params}<technique sid="COMMON"><lambert>{diffuse}{alpha}</lambert></technique>'
                       '<extra><technique profile="GOOGLEEARTH"><double_sided>1</double_sided></technique></extra></profile_COMMON></effect>')
        out.append("</library_effects>")
        out.append("<library_materials>")
        for m in mats:
            out.append(f'<material id="{m["id"]}" name={quoteattr(m["name"])}><instance_effect url="#{m["id"]}-effect"/></material>')
        out.append("</library_materials>")
        textured = {m["id"] for m in mats if m["image"]}

        geoms, scene, stats = [], [], {"groups": 0, "parts": 0, "triangles": 0, "vertices": 0}
        names = set()
        for gname, parts in self.groups:
            gid = ncname("grp_" + gname, self.used_ids, "grp")
            scene.append(f'<node id="{gid}" name={quoteattr(ncname(gname))} type="NODE">')
            stats["groups"] += 1
            for label, pos, uv, tri, mid in parts:
                has_uv = uv is not None and mid in textured
                rows = np.round(np.c_[pos, uv] if has_uv else pos, 5)                              # as written to the file
                uniq, t = clean(rows, tri)
                if not len(t):
                    continue
                pid = ncname("geo_" + label, self.used_ids, "geo")
                pname = ncname(label, names, "part")
                n = len(uniq)
                g = [f'<geometry id="{pid}" name={quoteattr(pname)}><mesh>',
                     f'<source id="{pid}-pos"><float_array id="{pid}-pos-array" count="{n * 3}">{fmt(uniq[:, :3], 5)}</float_array>'
                     f'<technique_common><accessor source="#{pid}-pos-array" count="{n}" stride="3"><param name="X" type="float"/>'
                     '<param name="Y" type="float"/><param name="Z" type="float"/></accessor></technique_common></source>']
                inputs = f'<input semantic="VERTEX" source="#{pid}-vtx" offset="0"/>'
                if has_uv:
                    g.append(f'<source id="{pid}-uv"><float_array id="{pid}-uv-array" count="{n * 2}">{fmt(uniq[:, 3:5], 5)}</float_array>'
                             f'<technique_common><accessor source="#{pid}-uv-array" count="{n}" stride="2"><param name="S" type="float"/>'
                             '<param name="T" type="float"/></accessor></technique_common></source>')
                    inputs += f'<input semantic="TEXCOORD" source="#{pid}-uv" offset="0" set="0"/>'
                g.append(f'<vertices id="{pid}-vtx"><input semantic="POSITION" source="#{pid}-pos"/></vertices>')
                g.append(f'<triangles material="{mid}" count="{len(t)}">{inputs}<p>{" ".join(map(str, t.ravel().tolist()))}</p></triangles>')
                g.append("</mesh></geometry>")
                geoms.append("".join(g))
                bind = '<bind_vertex_input semantic="UVSET0" input_semantic="TEXCOORD" input_set="0"/>' if has_uv else ""
                scene.append(f'<node id="{ncname("node_" + label, self.used_ids, "node")}" name={quoteattr(pname)} type="NODE">'
                             f'<instance_geometry url="#{pid}"><bind_material><technique_common>'
                             f'<instance_material symbol="{mid}" target="#{mid}">{bind}</instance_material>'
                             '</technique_common></bind_material></instance_geometry></node>')
                stats["parts"] += 1
                stats["triangles"] += len(t)
                stats["vertices"] += n
            scene.append("</node>")
        out.append("<library_geometries>")
        out.extend(geoms)
        out.append("</library_geometries>")
        root = ncname(title, self.used_ids, "site")
        out.append(f'<library_visual_scenes><visual_scene id="scene" name={quoteattr(root)}>')
        out.extend(scene)
        out.append("</visual_scene></library_visual_scenes>")
        out.append('<scene><instance_visual_scene url="#scene"/></scene>')
        out.append("</COLLADA>")
        return "\n".join(out) + "\n", stats


README = """{title} - SketchUp package
{rule}

{dae:<{w}}  the model with colours and textures (COLLADA)
{tex:<{w}}  the images the model's materials use - keep this folder beside the .dae

IMPORTING

SketchUp for desktop (Pro or Studio; also Make 2017):
  1. Unzip this package; keep {name}.dae and the textures folder together.
  2. File > Import..., set the file type to "COLLADA File (*.dae)" and pick {name}.dae.
     Under Options, leave "Merge coplanar faces" on.
  3. Click to place it. It arrives as one component; right-click > Explode once to get the
     buildings, the site works and the ground as separate pieces.
  4. The ground, lawn and paving follow the terrain, so they arrive as triangles with every edge
     drawn. Open those pieces, select all, right-click > Soften/Smooth Edges - or switch edges off
     under Styles.

SketchUp for Web (needs a Go, Pro or Studio plan):
  Do not unzip. Drag this .zip onto the SketchUp for Web window, or use Import > My device (switch
  the file picker to "All files" if the .zip is greyed out). The web importer wants the .dae and
  its texture folder zipped together, which is how this package is laid out.

SketchUp Free (web) cannot import this package: the free plan only imports .skp and image files.

If the model arrives white, the textures were not found: the textures folder must sit beside the
.dae, with its name unchanged.

WHAT IS IN IT

  {groups}

  {parts} parts, {triangles:,} triangles. Units are metres, Z is up.
  The origin is the address point; z = 0 is the ground at the front door.
  +Y (SketchUp's green axis) is grid north, {convergence} of true north.
  In the file every building is one group and every part a named piece inside it (roof, walls,
  each window ...); SketchUp's importer may replace those names with numbered component names.
  "Site works" is the paving, lawn, beds and fence; "Ground" is the terrain within about 12 m of
  anything built, including a strip under the drive out to the road, in plain green. Either can
  be hidden or deleted without touching the buildings. Trees, the aerial photograph and the parcel
  line are not included.

The buildings are shells - exterior surfaces only, nothing inside - and every material is opaque;
window glass is drawn as slate. This is a reconstruction for visualisation, not a survey.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("assets")
    ap.add_argument("--spec", default=None)
    ap.add_argument("--name", default="3910-w-dupont")
    ap.add_argument("--title", default="3910 W Dupont Rd")
    args = ap.parse_args()
    assets = args.assets.rstrip("/")
    spec_path = args.spec or os.path.join(os.path.dirname(os.path.abspath(assets)), "spec", "site_spec.json")
    spec = json.load(open(spec_path))
    z0 = float(spec["z0"])
    labels = json.load(open(os.path.join(assets, "parts.json")))
    surf = json.load(open(os.path.join(assets, "surfaces.json"))) if os.path.exists(os.path.join(assets, "surfaces.json")) else {"models": [], "parts": {}}
    building_label = {b["id"]: b.get("label", b["id"]) for b in spec["buildings"]}

    pkg = Package()
    ids = [b for b in ORDER if os.path.exists(os.path.join(assets, b + ".glb"))]
    ids += [b["id"] for b in spec["buildings"] if b["id"] not in ids and os.path.exists(os.path.join(assets, b["id"] + ".glb"))]
    for bid in ids:
        pkg.add_glb(os.path.join(assets, bid + ".glb"), building_label.get(bid, bid), labels)
    works = []
    for m in surf["models"]:
        before = len(pkg.groups)
        pkg.add_glb(os.path.join(assets, m["file"]), "Site works", surf["parts"])
        works += pkg.groups.pop(before)[1]
    if works:
        pkg.groups.append(("Site works", works))
    extent = pkg.add_ground(os.path.join(assets, "site_layers.json"), assets, z0)

    dae, stats = pkg.dae(args.title)
    conv = spec.get("site", {}).get("location", {}).get("grid_convergence_deg")
    files = (f"{args.name}.dae", "textures/")
    readme = README.format(title=args.title, rule="=" * (len(args.title) + 19), name=args.name,
                           dae=files[0], tex=files[1], w=max(map(len, files)),
                           groups="\n  ".join(f"{g}: {len(p)} part{'s' if len(p) != 1 else ''}" for g, p in pkg.groups),
                           parts=stats["parts"], triangles=stats["triangles"],
                           convergence=f"{abs(conv):.2f} degrees {'east' if conv >= 0 else 'west'}" if conv is not None else "close to",
                           )
    out = os.path.join(assets, f"{args.name}-sketchup.zip")
    stamp = (2026, 1, 1, 0, 0, 0)                                  # fixed, so the same model gives the same file

    def put(z, name, data):
        info = zipfile.ZipInfo(name, date_time=stamp)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        z.writestr(info, data)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        put(z, f"{args.name}.dae", re.sub(r"<created>[^<]*</created><modified>[^<]*</modified>",
                                         "<created>2026-01-01T00:00:00Z</created><modified>2026-01-01T00:00:00Z</modified>", dae))
        for fname, data in sorted(pkg.images.values()):
            put(z, f"textures/{fname}", data)
        put(z, "README.txt", readme)
    open(out, "wb").write(buf.getvalue())
    print(json.dumps({"package": out, "bytes": len(buf.getvalue()), "dae_bytes": len(dae), "textures": len(pkg.images),
                      "materials": len(pkg.materials), **stats, "ground_extent": extent}))


if __name__ == "__main__":
    main()
