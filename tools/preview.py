#!/usr/bin/env python3
"""Build a spec into its own preview folder and photograph it in the viewer.

  python3 tools/preview.py <spec.json> <name> [--views front rear east west plan] [--mode photo]
                           [--shots-dir DIR] [--port 8765] [--size 1400 900]

Writes preview/<name>/ (models + manifest; site layers are shared with assets/) and, if a
local web server is serving the repository root on --port, PNG screenshots of each view.
Start a server with:  python3 -m http.server 8765   (from the repository root)
Extra views can be given in the spec under site.views; "photo_match" is added automatically
if the spec defines it.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHARED = ["terrain.bin", "trees.bin", "points.bin", "ortho.jpg", "site_layers.json"]


def default_site(spec):
    z0 = spec["z0"]
    return {
        "title": "Preview", "subtitle": "draft", "eyebrow": "Preview",
        "location": {"lat": 41.1791121, "lon": -85.1948761, "grid_convergence_deg": 1.19},
        "focus": [0, 13, z0 + 2],
        "views": [
            {"id": "front", "label": "Front", "pos": [4, -34, z0 + 9], "target": [0, 10, z0 + 2.5]},
            {"id": "rear", "label": "Rear", "pos": [-4, 62, z0 + 11], "target": [0, 14, z0 + 2]},
            {"id": "east", "label": "East", "pos": [50, 12, z0 + 9], "target": [0, 12, z0 + 2.5]},
            {"id": "west", "label": "West", "pos": [-54, 8, z0 + 10], "target": [0, 12, z0 + 2.5]},
            {"id": "plan", "label": "Plan", "pos": [0, 12.9, z0 + 62], "target": [0, 13, z0]},
            {"id": "ne", "label": "NE", "pos": [40, 50, z0 + 16], "target": [0, 13, z0 + 2]},
            {"id": "sw", "label": "SW", "pos": [-40, -24, z0 + 14], "target": [0, 12, z0 + 2]},
        ],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("name")
    ap.add_argument("--views", nargs="*", default=["front", "rear", "east", "west", "plan", "ne", "sw"])
    ap.add_argument("--mode", default="photo")
    ap.add_argument("--layers", default='{"trees": false, "labels": false}')
    ap.add_argument("--shots-dir")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--size", nargs=2, type=int, default=[1400, 900])
    ap.add_argument("--minutes", default="")
    args = ap.parse_args()
    if not args.name.replace("_", "").replace("-", "").isalnum():
        sys.exit("name must be alphanumeric (plus _ or -)")
    out = os.path.join(ROOT, "preview", args.name)
    os.makedirs(out, exist_ok=True)
    import glob
    extra = [os.path.basename(g) for g in glob.glob(os.path.join(ROOT, "assets", "surfaces*")) + glob.glob(os.path.join(ROOT, "assets", "ortho_*.jpg"))]
    for f in SHARED + extra:
        src = os.path.join(ROOT, "assets", f)
        dst = os.path.join(out, f)
        if os.path.exists(src) and not os.path.exists(dst):
            os.symlink(src, dst)
    spec = json.load(open(args.spec))
    if "site" not in spec:
        spec["site"] = default_site(spec)
    else:
        have = {v["id"] for v in spec["site"].get("views", [])}
        spec["site"].setdefault("views", [])
        spec["site"]["views"] += [v for v in default_site(spec)["views"] if v["id"] not in have]
        for k, v in default_site(spec).items():
            spec["site"].setdefault(k, v)
    tmp = os.path.join(out, "_spec.json")
    json.dump(spec, open(tmp, "w"))
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_model.py"), tmp, out], check=True)
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_manifest.py"), tmp, out], check=True)
    shots = args.shots_dir or out
    os.makedirs(shots, exist_ok=True)
    url = f"http://localhost:{args.port}/?assets=preview/{args.name}&ui=0"
    ids = {v["id"] for v in spec["site"]["views"]}
    for view in args.views:
        if view not in ids:
            print(f"skip unknown view {view}")
            continue
        png = os.path.join(shots, f"{args.name}_{view}_{args.mode}.png")
        r = subprocess.run(["node", os.path.join(ROOT, "tools", "screenshot.js"), url, png, view,
                            str(args.size[0]), str(args.size[1]), args.mode, args.layers, args.minutes],
                           capture_output=True, text=True)
        print((r.stdout or r.stderr).strip()[:400])


if __name__ == "__main__":
    main()
