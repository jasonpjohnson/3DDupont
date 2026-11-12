# `site_spec.json` — the building + site description the model is generated from

Everything is in the **site frame**: EPSG:26916 (NAD83 / UTM 16N) metres with
`x = Easting − 651405.348` (east), `y = Northing − 4560211.139` (north) and
`z` = NAVD88 elevation in metres. The address point is `(0, 0)`. Grid north is 1.19° east of true north.

`tools/build_model.py` turns `buildings` into one `.glb` per building, `tools/build_site.py`
makes terrain / trees / aerial photo, `tools/build_manifest.py` packs `site` for the viewer and
`tools/verify_model.py` scores the roofs against the LiDAR survey.

```jsonc
{
  "z0": 261.75,              // elevation that becomes height 0 in the viewer (grade at the front door)
  "defaults": { "overhang": 0.4, "roof_thickness": 0.16, "fascia": 0.2, "below_grade": 0.6 },
  "materials": { "<name>": { "color": "#rrggbb", "label": "shown to people",
                             "texture": "lap_siding|board_batten|shingles|standing_seam|ledgestone|planks|concrete|ribbed_metal|flat",
                             "texture_args": {}, "roughness": 0.9, "metallic": 0.0, "double_sided": false } },
  "buildings": [ { "id": "house", "label": "House", "wings": [...], "openings": [...], "elements": [...] } ],
  "site": { ... }
}
```

Required material names (used as defaults by the builder): `roof`, `trim`, `siding`, `window_frame`,
`glass`, `door`, `garage_door`, `shadow`, `stone`, `concrete`. Any wing / opening / element may name another.

## Wings — walls and roofs

A building is a set of **wings**. The roof of one wing is the *lower envelope* (minimum) of its
planes over its eave polygon, which reproduces gable, hip, shed and flat roofs exactly:

* gable: two planes sloping away from the ridge; hip: add a plane per hipped end; shed / flat: one plane.
* A plane is `[a, b, d]` meaning `z = a·x + b·y + d` in the site frame (absolute NAVD88 z).
  `[0, 0.5, d]` rises 6-in-12 toward the north (so it is the south-facing slope of an east-west ridge);
  `[0, -0.5, d]` rises toward the south. `d` is the plane's height at the address point `(0, 0)`.

Where wings interpenetrate (cross gables, a taller block rising out of a lower roof) the builder
hides whatever part of a roof or wall lies inside another *solid* wing below that wing's roof.
So a cross gable is simply a second wing whose `footprint` runs far enough into the main wing for
its ridge to bury itself in the main roof; valleys come out automatically. Overhangs that pass over a
lower roof are kept. Wing footprints should otherwise not overlap.

```jsonc
{
  "id": "main",                         // lower-case, a-z0-9_
  "label": "Main block",                // shown in the info card
  "footprint": [[x, y], ...],           // wall line (outside face), any winding, no repeat of first point
  "eave": [[x, y], ...],                // optional explicit roof outline; default = footprint grown by `overhang`
  "overhang": 0.4,                      // metres, horizontal
  "planes": [[a, b, d], ...],
  "solid": true,                        // false for open roofs (porches): no walls, hides nothing
  "no_walls": false, "no_roof": false,
  "base_z": 261.6,                      // lowest finished grade against this wing; walls run to base_z − below_grade
  "floor_z": 262.2,                     // main finished floor; opening `sill` heights are measured from this
  "walls": {
    "material": "siding",
    "bands": [ { "to_z": 262.9, "material": "stone" } ],          // from the ground up to to_z (absolute z)
    "gable_material": "board_batten", "gable_from_z": 265.1        // optional different cladding above this z
  },
  "edge_materials": { "2": "stone" },   // optional: whole footprint edge (index after CCW ordering) in one material
  "roof_material": "roof", "fascia_material": "trim", "soffit_material": "trim",
  "bottom_z": 262.2,                    // optional: a cantilevered volume. Walls start here instead of the ground,
                                        // an underside is added, and walls of other wings stay visible below it
  "evidence": "lidar|record|photo|inferred", "source": "one line", "desc": "one sentence", "dims": "56' x 35'",
  "confidence": "high|medium|low"
}
```

`walls.faces` overrides the cladding for the walls facing one compass side, e.g. a stone wainscot on the
front only: `"faces": { "S": { "bands": [ { "to_z": 262.9, "material": "stone" } ] }, "N": { "material": "siding", "bands": [] } }`
(keys `N`, `E`, `S`, `W`; each may set `material`, `bands`, `gable_material`, `gable_from_z`; unset keys inherit).

`walls.patches` re-clads a rectangle of one wall, e.g. the finished wall of a walk-out basement:
`"patches": [ { "from": [x, y], "to": [x, y], "z0": 259.2, "z1": 262.0, "material": "siding" } ]`
(both points on the same wall of this wing).

## Openings — windows and doors

```jsonc
{ "id": "front_door", "wing": "entry", "at": [x, y],   // point on (or within 1.5 m of) the wall; snapped to the nearest wall of that wing
  "width": 0.91, "height": 2.03,
  "sill": 0.0,            // metres above the wing's floor_z   (or "sill_z": absolute)
  "kind": "window|door|garage_door",
  "style": "double_hung|casement|fixed", "grid": [cols, rows],     // window muntins
  "lite": [0.5, 0.92], "material": "door",                         // door: glazed band as fractions of the height
  "sections": 4, "windows": 4,                                     // garage door
  "frame_material": "window_frame", "casing": true, "casing_material": "trim",
  "label": "...", "evidence": "photo", "source": "...", "desc": "..." }
```

Shaped glass (gable transoms): give `"outline": [[ds, dh], ...]` instead of width/height - a polygon measured from
the opening's lower-left corner, `ds` running left to right as seen from outside. `at` is still the centre of its width.
A trapezoid 1.6 m wide rising from 0.4 m at the left to 1.3 m at the right is `[[0,0],[1.6,0],[1.6,1.3],[0,0.4]]`.

Doors: `"leaves": 2` makes a French or sliding pair; `"lite": [0.06, 0.94]` glazes each leaf; `"stile"` sets the rail width.

Doors and garage doors become their own selectable parts; windows are grouped per wing unless `"own_part": true`.

## Elements — everything else

| kind | fields |
|---|---|
| `prism` | `polygon`, `z0`, `z1`, `material`, optional `holes`, `side_material` (deck boards on top, fascia around), `pad` (default true: terrain is kept below thin slabs) — slabs, decks, patios, retaining walls, chimneys |
| `post` | `at` or `at_list`, `z0`, `z1`, `size: [sx, sy]`, `yaw`, optional `base: {height, size, material, cap_material}` |
| `beam` | `a`, `b` (3D points) or `segments: [[a, b], ...]`, `size: [width, depth]` |
| `railing` | `path: [[x, y], ...]`, `z` (deck surface), `height`, `style: "balusters"|"horizontal"`, `rails` |
| `stairs` | `top: [x, y]` (centre of top edge), `direction: [dx, dy]` (downhill), `z_top`, `z_bottom`, `width`, `solid`, `railing: {material, height, sides: both/left/right}` |
| `box` | `center: [x, y, z]`, `size: [sx, sy, sz]`, `yaw` |
| `cylinder` | `base: [x, y, z]`, `radius`, `height` |
| `wall_panel` | vertical slab from plan point `a` to `b`: `thickness`, and either `z0`/`z1` or `outline: [[s, h], ...]` (s = metres from `a`, h = absolute z); `holes: [[[s, h], ...]]`; `arches: [{s0, s1, sill_z, spring_z, rise}]` cut arched openings — gable infill, screen walls, arched porch fronts |
| `skylight` | `wing`, `at: [x, y]`, `size: [across, up_slope]` — curb and glass laid on that wing's roof plane |

Every element takes `id`, `material`, `label`, `desc`, `dims`, `evidence`, `source`, `confidence`.

## Evidence classes

Each wing, opening and element must carry one — the viewer colours the model by it and the
info card shows `source`:

* `lidar` — geometry fitted to the 2017 (or 2011) LiDAR returns
* `record` — a dimension taken from the county record card sketch or building table
* `photo` — read from an aerial photograph or the assessor's street photo
* `inferred` — typical construction filled in where nothing shows it; say so in `desc`

## `site`

```jsonc
"site": {
  "title": "3910 W Dupont Rd", "subtitle": "Fort Wayne, Indiana", "eyebrow": "Site model",
  "facts": [ { "k": "Built", "v": "1989" }, { "k": "Parcel", "v": "…", "wide": true } ],
  "location": { "lat": 41.1791121, "lon": -85.1948761, "grid_convergence_deg": 1.19 },
  "focus": [x, y, z],                       // what the sun-study shadow camera and views centre on
  "parcel": [[x, y], ...], "parcel_corners": [[x, y], ...], "parcel_note": "11.90 ac",
  "clearings": [ [[x, y], ...] ],           // ground cleared since the 2017 survey: no LiDAR trees are drawn inside
  "grading": [ { "polygon": [[x, y], ...], "z": 261.3, "feather": 3 } ],   // ground levelled since the survey (cut and fill)
  "surfaces": [ { "id": "barn_asphalt", "kind": "asphalt|asphalt_old|gravel|concrete|concrete_old|mulch|river_rock|lawn",
                  "polygon": [[x, y], ...], "since": 2025, "label": "...", "evidence": "photo", "source": "..." } ],
                                            // ground surfaces newer than (or wrong in) the aerial photo; draped exactly on the terrain
  "fences": [ { "id": "yard_fence", "path": [[x, y], ...], "height": 1.8, "material": "fence|fence_stain", "since": 2023, "label": "...", "evidence": "photo" } ],
  "views": [ { "id": "front", "label": "Front", "pos": [x, y, z], "target": [x, y, z] },
             { "id": "p29", "pos": [...], "target": [...], "fov": 51, "hidden": true } ],   // hidden + fov: a photo-matched camera for review (fov = vertical degrees)
  "labels": [ { "text": "House", "note": "1989", "at": [x, y] , "lift": 10 } ],       // lift = metres above ground
  "dimensions": [ { "a": [x, y, z], "b": [x, y, z], "text": "56'" } ],
  "about": { "heading": "...", "intro": ["paragraph"], "accuracy": [ { "check", "result", "basis" } ],
             "sources": [ { "name", "url", "license", "used_for", "date" } ], "limits": ["..."],
             "record": [ { "k", "v" } ], "footer": "..." }
}
```

No owner names, sale prices or assessed values belong anywhere in this file.
