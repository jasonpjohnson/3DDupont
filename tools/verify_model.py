#!/usr/bin/env python3
"""Check a building spec against the survey data.

  python3 tools/verify_model.py spec.json --lidar in2017.npz --out outdir \
        [--building house] [--ortho house.png --ortho-bounds xmin ymin xmax ymax]

--lidar is an .npz with x, y, z (site frame, metres) and classification (2 = ground).

Writes <out>/verify_<building>.json and PNGs:
  residual map  - every LiDAR return above the roof outline, coloured by (survey z - model z)
  plan overlay  - wall lines, eaves and roof facet edges drawn on the aerial photo
The numbers that matter: the share of roof returns within 0.15 m / 0.30 m of the model
surface, the per-wing median offset, and the "uncovered" / "phantom" areas.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
from shapely.prepared import prep

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from roofsolve import Wing, visible_facets  # noqa: E402


def load_wings(spec, building_id):
    b = next(x for x in spec["buildings"] if x["id"] == building_id)
    wings = []
    for w in b["wings"]:
        if w.get("no_roof"):
            continue
        fp = Polygon(w["footprint"]).buffer(0) if w.get("footprint") else None
        eave = Polygon(w["eave"]).buffer(0) if w.get("eave") else None
        wings.append(Wing(w["id"], w["planes"], footprint=fp, eave=eave, overhang=w.get("overhang", 0.4),
                          solid=w.get("solid", fp is not None), base_z=w.get("base_z", spec["z0"])))
    return b, wings


def top_surface(wings, x, y):
    """(z, wing index) of the highest model roof at each point; NaN / -1 where none."""
    z = np.full(len(x), np.nan)
    who = np.full(len(x), -1)
    for k, w in enumerate(wings):
        minx, miny, maxx, maxy = w.eave.bounds
        cand = np.nonzero((x >= minx) & (x <= maxx) & (y >= miny) & (y <= maxy))[0]
        if not len(cand):
            continue
        pe = prep(w.eave)
        inside = cand[[pe.contains(Point(x[i], y[i])) for i in cand]]
        if not len(inside):
            continue
        zw = w.z(x[inside], y[inside])
        better = np.isnan(z[inside]) | (zw > z[inside])
        z[inside[better]] = zw[better]
        who[inside[better]] = k
    return z, who


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--lidar", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--building", default="house")
    ap.add_argument("--ortho")
    ap.add_argument("--ortho-bounds", nargs=4, type=float, metavar=("XMIN", "YMIN", "XMAX", "YMAX"))
    ap.add_argument("--margin", type=float, default=6.0)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    spec = json.load(open(args.spec))
    b, wings = load_wings(spec, args.building)
    d = np.load(args.lidar)
    x, y, z, cls = d["x"], d["y"], d["z"], d["classification"]
    outline = unary_union([w.eave for w in wings])
    minx, miny, maxx, maxy = outline.buffer(args.margin).bounds
    box = (x >= minx) & (x <= maxx) & (y >= miny) & (y <= maxy)
    x, y, z, cls = x[box], y[box], z[box], cls[box]
    ground = np.median(z[cls == 2]) if (cls == 2).any() else float(np.min(z))

    zm, who = top_surface(wings, x, y)
    on_model = ~np.isnan(zm)
    res = z - zm
    # roof returns: inside the outline and not far below the model (returns far below are wall / ground hits at the edge)
    roof = on_model & (res > -1.0) & (res < 1.5)
    r = res[roof]
    summary = {
        "building": args.building,
        "lidar_returns_in_window": int(len(x)),
        "returns_over_model_roof": int(on_model.sum()),
        "roof_returns_used": int(roof.sum()),
        "median_offset_m": round(float(np.median(r)), 3) if len(r) else None,
        "rms_m": round(float(np.sqrt(np.mean(r ** 2))), 3) if len(r) else None,
        "within_0.15m": round(float(np.mean(np.abs(r) <= 0.15)), 3) if len(r) else None,
        "within_0.30m": round(float(np.mean(np.abs(r) <= 0.30)), 3) if len(r) else None,
        "p05_p95_m": [round(float(v), 3) for v in np.percentile(r, [5, 95])] if len(r) else None,
        "wings": {},
    }
    for k, w in enumerate(wings):
        m = roof & (who == k)
        if m.sum() < 5:
            summary["wings"][w.id] = {"returns": int(m.sum())}
            continue
        rk = res[m]
        summary["wings"][w.id] = {
            "returns": int(m.sum()), "median_offset_m": round(float(np.median(rk)), 3),
            "rms_m": round(float(np.sqrt(np.mean(rk ** 2))), 3),
            "within_0.15m": round(float(np.mean(np.abs(rk) <= 0.15)), 3),
            "roof_z_range_m": [round(float(zm[m].min()), 2), round(float(zm[m].max()), 2)],
        }
    # phantom roof: model says roof, survey sees ground through it (grid cells with only low returns)
    cell = 0.5
    gx = np.arange(minx, maxx, cell)
    gy = np.arange(miny, maxy, cell)
    ix = np.clip(((x - minx) / cell).astype(int), 0, len(gx) - 1)
    iy = np.clip(((y - miny) / cell).astype(int), 0, len(gy) - 1)
    zmax = np.full((len(gy), len(gx)), np.nan)
    np.fmax.at(zmax, (iy, ix), z)
    cx, cy = np.meshgrid(gx + cell / 2, gy + cell / 2)
    zmod, _ = top_surface(wings, cx.ravel(), cy.ravel())
    zmod = zmod.reshape(cx.shape)
    have = ~np.isnan(zmax)
    phantom = have & ~np.isnan(zmod) & (zmax < zmod - 1.0)
    missing = have & np.isnan(zmod) & (zmax > ground + 2.2)
    summary["phantom_roof_m2"] = round(float(phantom.sum() * cell * cell), 1)
    summary["elevated_returns_outside_model_m2"] = round(float(missing.sum() * cell * cell), 1)
    summary["note"] = ("phantom = model roof where the survey only sees ground; elevated_returns_outside_model includes "
                       "trees and decks as well as any roof the model is missing - check the residual map")
    json.dump(summary, open(os.path.join(args.out, f"verify_{args.building}.json"), "w"), indent=1)
    print(json.dumps(summary, indent=1))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def draw_outlines(ax, lw=1.0, color="#111"):
        for w in wings:
            if w.footprint is not None:
                ax.plot(*np.asarray(w.footprint.exterior.coords).T, color=color, lw=lw)
            ax.plot(*np.asarray(w.eave.exterior.coords).T, color=color, lw=lw * 0.6, ls="--")
        vis = visible_facets(wings)
        for wid, facets in vis.items():
            for _, poly in facets:
                ax.plot(*np.asarray(poly.exterior.coords).T, color="#d6336c", lw=lw * 0.55)

    fig, ax = plt.subplots(figsize=(11, 10), dpi=130)
    m = on_model | (z > ground + 1.5)
    val = np.where(on_model, res, np.nan)
    sc = ax.scatter(x[on_model], y[on_model], c=np.clip(val[on_model], -0.6, 0.6), cmap="RdBu_r", s=9, vmin=-0.6, vmax=0.6)
    out = ~on_model & (z > ground + 2.2)
    ax.scatter(x[out], y[out], c="#f59f00", s=5, label="elevated return outside model roof")
    low = on_model & (res < -1.0)
    ax.scatter(x[low], y[low], c="#2b8a3e", s=7, marker="x", label="survey > 1 m below model roof")
    draw_outlines(ax)
    for w in wings:
        c = w.eave.centroid
        ax.text(c.x, c.y, w.id, fontsize=7, ha="center", color="#111", bbox=dict(fc="white", ec="none", alpha=0.6, pad=1))
    plt.colorbar(sc, ax=ax, label="survey z - model z (m), clipped at +-0.6", shrink=0.8)
    ax.set_aspect("equal")
    ax.set_xlabel("site x (m, east)")
    ax.set_ylabel("site y (m, north)")
    ax.grid(alpha=0.3)
    ax.legend(loc="lower left", fontsize=7)
    ax.set_title(f"{args.building}: LiDAR vs model roof - median {summary['median_offset_m']} m, "
                 f"{summary['within_0.15m']} within 0.15 m, {summary['within_0.30m']} within 0.30 m")
    fig.savefig(os.path.join(args.out, f"verify_{args.building}_residuals.png"), bbox_inches="tight")
    plt.close(fig)

    if args.ortho and args.ortho_bounds:
        img = plt.imread(args.ortho)
        ox0, oy0, ox1, oy1 = args.ortho_bounds
        fig, ax = plt.subplots(figsize=(12, 12), dpi=130)
        ax.imshow(img, extent=(ox0, ox1, oy0, oy1))
        draw_outlines(ax, lw=1.3, color="#00e5ff")
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_aspect("equal")
        ax.grid(alpha=0.25, color="w")
        ax.set_title(f"{args.building}: walls (solid), eaves (dashed), roof facets (pink) on the aerial")
        fig.savefig(os.path.join(args.out, f"verify_{args.building}_plan.png"), bbox_inches="tight")
        plt.close(fig)


if __name__ == "__main__":
    main()
