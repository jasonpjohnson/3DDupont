#!/usr/bin/env python3
"""A made-up main floor for the Play house game.

  python3 tools/build_interior.py spec/site_spec.json assets/

Writes <assets>/interior.json: rooms, partition walls, furniture and the places people use. The
layout is INVENTED. It fits inside the real exterior walls and uses the real exterior doors, but
the rooms are not the house's actual floor plan, which is private. The interior style follows the
house in general terms (white shaker cabinets with black pulls, wood-look tile, greige walls).

Everything is laid out in the house's own grid (its walls run 0.8 degrees off grid north) and then
turned into site coordinates. tools/build_nav.py reads the result to make the floor walkable.
"""
from __future__ import annotations

import json
import math
import os
import sys

ROT = 0.8                      # degrees: house grid -> site grid
ANCHOR = (0.0, 15.0)
FLOOR = 262.3                  # main floor (spec: floor_z of the main wings)
LINK = 262.0                   # the lower link toward the garage
CUT = 1.15                     # walls are cut this far above the floor in cut-away view

c, s = math.cos(math.radians(ROT)), math.sin(math.radians(ROT))


def site(x, y):
    dx, dy = x - ANCHOR[0], y - ANCHOR[1]
    return [round(ANCHOR[0] + dx * c - dy * s, 3), round(ANCHOR[1] + dx * s + dy * c, 3)]


def rect(x0, y0, x1, y1):
    return [site(x0, y0), site(x1, y0), site(x1, y1), site(x0, y1)]


# ---------------------------------------------------------------- rooms ----
# id, label, rectangles (house grid), floor colour, floor z
WOOD, TILE, LIGHT = "#6e5443", "#cfc8bc", "#8a6b52"
ROOMS = [
    ("foyer", "Entry", [(-0.8, 10.38, 4.38, 12.82)], "#5f574f", FLOOR),
    ("living", "Living room", [(-0.5, 12.82, 8.34, 18.0), (4.38, 10.38, 8.34, 12.82)], WOOD, FLOOR),
    ("kitchen", "Kitchen", [(8.34, 9.66, 12.3, 18.0)], WOOD, FLOOR),
    ("kids", "Kids' room", [(-4.04, 12.82, -0.5, 16.2)], LIGHT, FLOOR),
    ("corridor", "Corridor", [(-4.04, 16.2, -0.5, 18.0)], WOOD, FLOOR),
    ("office", "Office", [(-8.41, 12.82, -4.04, 18.0)], WOOD, FLOOR),
    ("mud", "Mud entry", [(-7.82, 10.38, -3.85, 12.82)], "#5f574f", LINK),
    ("bedroom", "Bedroom", [(-8.41, 18.0, -1.0, 23.48)], LIGHT, FLOOR),
    ("bath", "Bathroom", [(-1.0, 18.0, 2.0, 20.6)], TILE, FLOOR),
    ("backhall", "Back hall", [(-1.0, 20.6, 2.0, 23.48)], WOOD, FLOOR),
    ("den", "Den", [(2.0, 18.0, 8.34, 23.48), (2.27, 23.48, 7.67, 24.15)], WOOD, FLOOR),
]

# ------------------------------------------------------- partition walls ----
# (x0, y0, x1, y1, [gap (from, to) along the wall, measured from its start])
WALLS = [
    (8.34, 9.66, 8.34, 18.0, [(0.9, 6.4)]),                 # living | kitchen: wide opening
    (-0.5, 12.82, -0.5, 16.2, []),                           # kids | living
    (-0.8, 12.82, -0.5, 12.82, []),
    (-4.04, 16.2, -0.5, 16.2, [(1.44, 2.34)]),               # kids | corridor (door)
    (-4.04, 12.82, -4.04, 18.0, [(3.7, 4.8)]),               # office | kids + corridor (door)
    (-8.41, 18.0, 2.0, 18.0, []),                            # bedroom, bath | office, corridor, living
    (2.0, 18.0, 8.34, 18.0, [(1.0, 5.2)]),                   # living | den: arch
    (-1.0, 18.0, -1.0, 23.48, [(3.0, 3.9)]),                 # bedroom | bath, back hall (door)
    (-1.0, 20.6, 2.0, 20.6, [(1.1, 1.9)]),                   # bath | back hall (door)
    (2.0, 18.0, 2.0, 23.48, [(3.3, 4.4)]),                   # bath, back hall | den (door)
]

# ------------------------------------------------------------ furniture ----
# type, x, y, w (along local x), d (along local y), yaw (deg), extra
ITEMS = [
    # kitchen: white shaker cabinets, quartz tops, a steel fridge, an island
    ("counter", 12.0, 13.5, 0.62, 5.0, 0, {}),
    ("stove", 12.0, 12.7, 0.64, 0.76, 0, {}),
    ("sink", 12.0, 14.7, 0.5, 0.8, 0, {}),
    ("fridge", 11.95, 16.85, 0.72, 0.92, 0, {}),
    ("tall_cabinet", 11.95, 17.6, 0.62, 0.6, 0, {}),
    ("island", 10.0, 14.3, 1.0, 2.6, 0, {}),
    ("stool", 9.25, 13.6, 0.4, 0.4, 0, {}), ("stool", 9.25, 14.3, 0.4, 0.4, 0, {}), ("stool", 9.25, 15.0, 0.4, 0.4, 0, {}),
    ("table", 10.3, 11.2, 1.0, 1.8, 0, {}),
    ("chair", 9.55, 10.75, 0.45, 0.45, 0, {}), ("chair", 9.55, 11.65, 0.45, 0.45, 0, {}),
    ("chair", 11.05, 10.75, 0.45, 0.45, 180, {}), ("chair", 11.05, 11.65, 0.45, 0.45, 180, {}),
    ("plant", 8.75, 17.5, 0.5, 0.5, 0, {}),
    # living room
    ("rug", 1.5, 14.5, 3.0, 3.4, 0, {"color": "#b9c0c4"}),
    ("tv_console", -0.27, 14.5, 0.42, 1.9, 0, {}),
    ("tv", -0.4, 14.5, 0.06, 1.45, 0, {}),
    ("sofa", 2.75, 14.5, 0.95, 2.5, 180, {"color": "#d8d4cb"}),
    ("coffee_table", 1.35, 14.5, 0.65, 1.2, 0, {}),
    ("armchair", 1.5, 16.95, 0.85, 0.85, -90, {"color": "#d8d4cb"}),
    ("lamp", 4.0, 13.2, 0.35, 0.35, 0, {}),
    ("plant", 4.0, 17.5, 0.5, 0.5, 0, {}),
    # reading nook under the tall gable
    ("bookshelf", 8.12, 11.6, 0.36, 1.8, 0, {}),
    ("armchair", 7.1, 11.3, 0.8, 0.8, 0, {"color": "#3f5a57"}),
    ("lamp", 7.2, 10.75, 0.3, 0.3, 0, {}),
    ("rug", 6.5, 11.5, 2.0, 1.8, 0, {"color": "#a99a86"}),
    # entry
    ("bench", 3.6, 10.62, 1.3, 0.4, 0, {}),
    # den: pool table, games, bean bags
    ("pooltable", 5.0, 21.2, 1.35, 2.45, 0, {}),
    ("tv", 2.12, 19.3, 0.06, 1.3, 0, {}),
    ("beanbag", 3.55, 18.85, 0.85, 0.85, 0, {"color": "#2e86ab"}),
    ("beanbag", 3.55, 19.8, 0.85, 0.85, 0, {"color": "#e4572e"}),
    ("bookshelf", 8.12, 22.6, 0.36, 1.6, 0, {}),
    ("plant", 7.3, 23.75, 0.5, 0.5, 0, {}),
    ("plant", 2.75, 23.75, 0.5, 0.5, 0, {}),
    # bathroom
    ("vanity", 0.35, 18.3, 1.2, 0.5, 0, {}),
    ("toilet", 1.6, 18.42, 0.42, 0.65, 0, {}),
    ("tub", -0.55, 19.72, 0.75, 1.55, 0, {}),
    ("shower", 1.5, 20.05, 0.9, 0.9, 0, {}),
    # bedroom
    ("bed", -6.9, 21.0, 2.1, 1.65, 0, {"color": "#e7e3da", "head": "w"}),
    ("nightstand", -7.85, 19.8, 0.45, 0.45, 0, {}),
    ("nightstand", -7.85, 22.2, 0.45, 0.45, 0, {}),
    ("dresser", -3.2, 18.32, 1.5, 0.5, 0, {}),
    ("rug", -5.6, 21.0, 2.2, 2.6, 0, {"color": "#c9c3b6"}),
    # kids' room: two single beds
    ("bed", -3.3, 13.92, 0.95, 2.0, 0, {"color": "#7fb0d6", "head": "s"}),
    ("bed", -1.3, 13.92, 0.95, 2.0, 0, {"color": "#f2c14e", "head": "s"}),
    ("nightstand", -2.3, 13.1, 0.45, 0.4, 0, {}),
    ("toybox", -3.4, 15.75, 0.9, 0.45, 0, {}),
    # office and mud entry
    ("desk", -8.0, 14.5, 0.7, 1.5, 0, {}),
    ("chair", -7.3, 14.5, 0.5, 0.5, 180, {"color": "#1c2630"}),
    ("treadmill", -6.3, 17.45, 1.8, 0.8, 0, {}),
    ("lockers", -4.3, 14.6, 0.45, 2.4, 0, {}),
    ("bench", -5.8, 12.55, 1.6, 0.4, 0, {}),
]

# ------------------------------------------------------------- places ------
# id, label, icon, room, capacity, stands: (x, y, face deg in house grid, z offset above floor)
# (lying down, a person's feet point the way they face: beds face away from the headboard)
PLACES = [
    ("stove", "Stove", "🍳", "kitchen", 1, [(11.35, 12.7, 0, 0)]),
    ("fridge", "Fridge", "🧊", "kitchen", 1, [(11.25, 16.85, 0, 0)]),
    ("counter", "Counter", "🥗", "kitchen", 1, [(11.35, 14.9, 0, 0)]),
    ("table", "Dining table", "🍽️", "kitchen", 4, [(9.55, 10.75, 0, 0), (9.55, 11.65, 0, 0), (11.05, 10.75, 180, 0), (11.05, 11.65, 180, 0)]),
    ("sofa", "Sofa", "📺", "living", 3, [(2.62, 13.8, 180, 0), (2.62, 14.5, 180, 0), (2.62, 15.2, 180, 0)]),
    ("armchair", "Armchair", "☎️", "living", 1, [(1.5, 16.85, -90, 0)]),
    ("readchair", "Reading chair", "📖", "living", 1, [(7.0, 11.3, 0, 0)]),
    ("pool", "Pool table", "🎱", "den", 2, [(5.0, 19.75, 90, 0), (5.0, 22.65, -90, 0)]),
    ("games", "Video games", "🎮", "den", 2, [(3.55, 18.85, 180, -0.2), (3.55, 19.8, 180, -0.2)]),
    ("vanity", "Vanity", "🪞", "bath", 1, [(0.35, 18.95, -90, 0)]),
    ("toilet", "Toilet", "🚽", "bath", 1, [(1.6, 18.55, 90, 0)]),
    ("shower", "Shower", "🚿", "bath", 1, [(1.5, 20.05, -90, 0)]),
    ("tub", "Bathtub", "🛁", "bath", 1, [(-0.55, 19.35, 90, -0.12)]),
    ("beds", "Beds", "🛏️", "bedroom", 4, [(-6.9, 20.6, 0, 0.42), (-6.9, 21.4, 0, 0.42), (-3.3, 13.95, 90, 0.42), (-1.3, 13.95, 90, 0.42)]),
    ("computer", "Computer", "💻", "office", 1, [(-7.35, 14.5, 180, 0)]),
    ("treadmill", "Treadmill", "🏃", "office", 1, [(-6.3, 17.45, 180, 0.12)]),
]

# exterior doors that open onto the main floor (by spec opening id), plus the door from the link into the garage
MAIN_DOORS = ["front_door", "sides_west_door", "sides_east_french_doors", "sides_wing_n_french_doors", "rear_french_door"]
GARAGE_LINK = (-5.8, 10.38, 0.9)       # house grid: in the garage's north wall


def main():
    spec_path, out_dir = sys.argv[1], sys.argv[2]
    spec = json.load(open(spec_path))
    house = next(b for b in spec["buildings"] if b["id"] == "house")
    openings = {o["id"]: o for o in house["openings"]}
    rot = math.radians(ROT)

    rooms = [{"id": rid, "label": label, "z": z, "color": col, "polys": [rect(*r) for r in rs]} for rid, label, rs, col, z in ROOMS]
    walls = []
    for x0, y0, x1, y1, gaps in WALLS:
        L = math.hypot(x1 - x0, y1 - y0)
        ux, uy = (x1 - x0) / L, (y1 - y0) / L
        cuts = [0.0] + [v for g in sorted(gaps) for v in g] + [L]
        for i in range(0, len(cuts), 2):
            a, b = cuts[i], cuts[i + 1]
            if b - a > 0.05:
                walls.append({"a": site(x0 + ux * a, y0 + uy * a), "b": site(x0 + ux * b, y0 + uy * b)})
    items = []
    for t, x, y, w, d, yaw, extra in ITEMS:
        it = {"type": t, "x": site(x, y)[0], "y": site(x, y)[1], "w": w, "d": d, "yaw": round(yaw + ROT, 2),
              "z": LINK if (-7.82 <= x <= -3.85 and y < 12.82) else FLOOR}
        it.update(extra)
        items.append(it)
    places = []
    for pid, label, icon, room, cap, stands in PLACES:
        st = [{"x": site(x, y)[0], "y": site(x, y)[1], "face": round(f + ROT, 2), "z": round(FLOOR + dz, 3)} for x, y, f, dz in stands]
        ax = sum(p["x"] for p in st) / len(st)
        ay = sum(p["y"] for p in st) / len(st)
        places.append({"id": pid, "label": label, "icon": icon, "room": room, "cap": cap, "stand": st, "at": [round(ax, 3), round(ay, 3)]})
    doors = []
    for oid in MAIN_DOORS:
        o = openings[oid]
        doors.append({"id": oid, "at": o["at"], "width": o.get("width", 0.9)})
    gx, gy, gw = GARAGE_LINK
    doors.append({"id": "garage_link", "at": site(gx, gy), "width": gw})
    labels = []
    for rid, label, rs, col, z in ROOMS:
        if rid in ("corridor", "backhall", "foyer"):
            continue
        x0, y0, x1, y1 = max(rs, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
        labels.append({"room": rid, "label": label, "at": site((x0 + x1) / 2, (y0 + y1) / 2)})
    out = {
        "note": "Invented layout for the game. It is not this house's real floor plan.",
        "floor_z": FLOOR, "cut": CUT, "wall_t": 0.12, "wall_h": 2.6, "rot": ROT,
        "rooms": rooms, "walls": walls, "items": items, "places": places, "doors": doors, "labels": labels,
        "garage_link": {"inside": site(gx, gy + 0.6), "outside": site(gx, gy - 0.6), "face": [round(-s, 4), round(-c, 4)]},
    }
    json.dump(out, open(os.path.join(out_dir, "interior.json"), "w"), separators=(",", ":"))
    print(f"interior.json: {len(rooms)} rooms, {len(walls)} wall runs, {len(items)} items, {len(places)} places")


if __name__ == "__main__":
    main()
