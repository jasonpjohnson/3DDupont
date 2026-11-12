#!/usr/bin/env python3
"""Assemble assets/manifest.json (everything the viewer needs besides the binaries)
from the spec, the site layer metadata and the parts catalogue.

Usage: python3 tools/build_manifest.py spec/site_spec.json assets/
"""
from __future__ import annotations

import json
import os
import sys

EVIDENCE = {
    "lidar": {"label": "Measured", "color": "#2f7f6f", "detail": "fitted to the 2017 LiDAR survey"},
    "record": {"label": "County record", "color": "#3b6ea8", "detail": "dimension from the assessor's sketch or card"},
    "photo": {"label": "Photographed", "color": "#d9a441", "detail": "read from an aerial or the assessor's photo"},
    "inferred": {"label": "Inferred", "color": "#b9b4a8", "detail": "typical construction, not observed"},
}


def main():
    spec_path, out_dir = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path))
    layers = json.load(open(os.path.join(out_dir, "site_layers.json")))
    parts = json.load(open(os.path.join(out_dir, "parts.json")))
    site = spec["site"]
    z0 = spec["z0"]

    models = []
    layer_list = []
    for b in spec["buildings"]:
        path = f"{b['id']}.glb"
        if not os.path.exists(os.path.join(out_dir, path)):
            continue
        models.append({"id": b["id"], "file": path, "label": b.get("label", b["id"]), "since": b.get("since", 0)})
        layer_list.append({"id": b["id"], "label": b.get("label", b["id"]), "note": b.get("layer_note", ""), "on": True})

    surf_path = os.path.join(out_dir, "surfaces.json")
    if os.path.exists(surf_path):
        surf = json.load(open(surf_path))
        present = [m for m in surf["models"] if os.path.exists(os.path.join(out_dir, m["file"]))]
        if present:
            models += present
            parts = {**parts, **surf["parts"]}
            layer_list.append({"id": "sitework", "label": site.get("sitework_label", "Drive, yard and fence"),
                               "note": site.get("sitework_note", ""), "on": True})

    trees = layers["trees"]
    layer_list += [
        {"id": "trees", "label": "Trees", "note": f"{trees['count']} from LiDAR", "on": True},
        {"id": "aerial", "label": "Aerial photo", "note": site.get("ortho_note", "2024"), "on": True},
        {"id": "contours", "label": "Contours", "note": "1 ft", "on": False},
        {"id": "parcel", "label": "Parcel boundary", "note": site.get("parcel_note", ""), "on": True},
        {"id": "labels", "label": "Labels", "on": True},
        {"id": "dimensions", "label": "Dimensions", "note": site.get("dimensions_note", "county sketch"), "on": False},
        {"id": "points", "label": "LiDAR points", "note": f"{layers['points']['count'] // 1000}k, 2017", "on": False},
    ]

    used_evidence = {p.get("evidence") for p in parts.values()}
    evidence = {k: v for k, v in {**EVIDENCE, **spec.get("evidence", {})}.items() if k in used_evidence}
    unknown = sorted(e for e in used_evidence if e and e not in evidence)
    if unknown:
        raise SystemExit(f"parts use undefined evidence classes: {unknown}")

    manifest = {
        "eyebrow": site.get("eyebrow", "Site model"),
        "title": site["title"],
        "subtitle": site["subtitle"],
        "facts": site.get("facts", []),
        "foot_note": site.get("foot_note", ""),
        "location": site["location"],
        "z0": z0,
        "focus": site.get("focus", [0, 0, z0]),
        "terrain": layers["terrain"],
        "ortho": layers.get("ortho"),
        "ortho_epochs": layers.get("ortho_epochs", []),
        "trees": trees,
        "points": layers["points"],
        "models": models,
        "download": f"assets/{spec['buildings'][0]['id']}.glb" if spec["buildings"] else None,
        "parts": parts,
        "parcel": site.get("parcel"),
        "parcel_corners": site.get("parcel_corners", []),
        "layers": layer_list,
        "views": site["views"],
        "labels": site.get("labels", []),
        "dimensions": site.get("dimensions", []),
        "evidence": evidence,
        "about": site.get("about", {}),
    }
    out = os.path.join(out_dir, "manifest.json")
    json.dump(manifest, open(out, "w"), indent=1)
    print(f"wrote {out}: {len(models)} models, {len(parts)} parts, {len(layer_list)} layers")


if __name__ == "__main__":
    main()
