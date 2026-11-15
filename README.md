# 3910 W Dupont Rd — 3D site model

An interactive 3D model of the house and grounds at 3910 W Dupont Rd, Fort Wayne, Indiana,
as they stand today: the house after its 2023 renovation, the attached and detached garages,
the pole barn, the greenhouse and garden, drives, fence, lawn and the surrounding woods on
the 11.9-acre parcel.

**View it:** https://jasonpjohnson.github.io/3DDupont/

Drag to orbit, scroll or pinch to zoom, right-drag (two fingers) to pan. Click any part of
the model for what it is and how it is known; click the ground for its surveyed elevation.

## What is in the viewer

- **Photo / Clay / Evidence** display. *Evidence* colours every part by its basis: measured
  from the laser survey, taken from the county record, read from a photograph, or inferred.
- **Layers** for each building, the site works (paving, fence, lawn), the trees located from
  the LiDAR canopy, the aerial photograph, 1 ft contours, the parcel line, the county sketch
  dimensions, and the raw LiDAR returns themselves.
- **Sun study** for any date and time at this latitude, with leaf-on and leaf-off woods.
- **Measure** between any two points, and a **SketchUp download**: a COLLADA (`.dae`) package of every
  building, the site works and the ground, at true scale in metres, with textures.

## Play house

**▶ Play house** in the footer turns the model into a small life-simulation game. Up to four
people move in, each made in a character editor with a live 3D preview: age, body, skin tone,
hairstyle and colour, facial hair, glasses, top, bottoms, shoes and their colours, name and
personality. **✏️ Edit** on a person's card changes their look mid-game; **New game** deletes
the household and starts over. People live on the real grounds: they walk the lot on a grid built from the model
(`tools/build_nav.py` → `assets/nav.json`, so decks are reached by their stairs and buildings by
their doors). They also look after six needs and pick up skills, using what the property actually has:

| Place | Things to do |
|---|---|
| Main floor (invented layout, see below) | cook, eat, wash, sleep, nap, TV, read, call a friend, pool, video games, freelance on the computer, treadmill |
| Front porch chairs | sit and watch the road |
| Rear deck | grill out |
| Hot tub | soak (the cover comes off) |
| Walk-out patio fire pit | sit by the fire, toast marshmallows |
| Vegetable cage, berry arbors, greenhouse | tend, pick, start seedlings (produce feeds salads) |
| Pole barn | build furniture to sell, work out |
| Detached garage | tinker with the car |
| Attached garage | drive to work (weekdays) |
| Back lawn | watch clouds, stargaze, jog, play catch |

Click a person or their portrait to control them, click a place, room or piece of furniture (or
its marker) for its actions, and click the floor or ground to walk there; shift-click queues. Left
alone, people choose for themselves. The camera is free: drag to turn, right-drag or WASD/arrows
to move, Q/E to turn, scroll to zoom; F makes it follow the selected person. C (or *Walls*) cuts the
house's walls away just above the main floor so you can see in, or puts them back. Space pauses,
1–3 set the speed, Tab cycles people. The game clock drives the sun, nights get dark, and the
windows light up when someone is home. The household is saved in the browser (localStorage).

**The interior is made up.** `tools/build_interior.py` writes `assets/interior.json`: an invented
main floor of furnished rooms, fitted inside the real exterior
walls and using the real exterior doors, furnished in general terms in the house's style. It is
not the house's actual floor plan. The upper floor and basement are not modelled.

## How it was made

| Source | Used for |
|---|---|
| The owner's own photographs and main-floor plan (private, not reproduced here) | everything about how the place looks today: roof forms rebuilt in 2023, cladding and colours, every exterior door and window, porches, decks, patio, outbuildings, garden, paving |
| USGS 3DEP LiDAR, flown March 2017 (public domain), about 4 returns/m² | terrain, tree positions and heights, and the roof planes and wall heights the renovation left unchanged |
| Indiana Geographic Information Office orthoimagery, March 2024 (CC0) | the ground photograph and plan positions of roofs, drives and decks |
| Allen County Assessor record card | dimensioned footprint sketch, floor areas, year built, porches, decks, outbuilding sizes |
| Allen County iMap and Indiana statewide GIS | parcel boundary, building outlines, contours |

The model is generated from one description file, `spec/site_spec.json`. Each wing's roof is
a set of planes; the builder takes the lower envelope of a wing's planes and resolves the
junctions between wings, so ridges, hips and valleys are computed rather than drawn. Roofs
that predate the renovation are planes fitted to the LiDAR returns, and
`tools/verify_model.py` scores them against every return that hit the roof.

Everything newer was fitted to photographs. For each photograph a camera was solved at the
spot it was taken from, the model was rendered through that camera, and the two were compared
side by side — silhouette and roofline first, then openings, materials and the ground — until
corners, roof peaks, doors and windows landed within a few pixels. Reviewers independent of
the modelling then repeated the comparison photograph by photograph. The photographs
themselves are private and are not in this repository.

The page's *Sources & method* panel lists the accuracy checks and what is inferred rather
than measured.

## Repository layout

```
index.html, css/, js/     the viewer (three.js, vendored in vendor/ — no CDN, no build step); js/life.js is the game
assets/                   generated: models (.glb), terrain, trees, point cloud, aerial photo, manifest
spec/site_spec.json       the description the model is generated from; spec/SCHEMA.md documents it
tools/                    the pipeline (Python) and review helpers
```

## Rebuilding

```sh
pip install numpy scipy shapely pyproj mapbox_earcut pillow opencv-python-headless matplotlib "laspy[lazrs]" requests

python3 tools/fetch_lidar.py --out data/lidar                      # LiDAR from the USGS public bucket
python3 tools/build_site.py data/lidar/IN_Statewide_B1_2017_raw3857.npz assets/ --spec spec/site_spec.json \
        [--ortho photo.png --cir infrared.png]                     # terrain, trees, points, paving, fence
python3 tools/build_model.py spec/site_spec.json assets/           # buildings -> .glb + parts catalogue
python3 tools/export_sketchup.py assets/                           # the SketchUp package behind the download button
python3 tools/build_interior.py spec/site_spec.json assets/        # the game's invented main floor
python3 tools/build_nav.py spec/site_spec.json assets/             # walkable-ground grid and doors for the game
python3 tools/build_manifest.py spec/site_spec.json assets/        # what the viewer loads (stamps a build id)
python3 tools/stamp_page.py                                        # copy the code to content-named files and point the page at them

python3 -m http.server 8765                                        # then open http://localhost:8765/
```

`build_site.py` without `--ortho` leaves the committed aerial photograph untouched. It is a
crop of the state's public orthoimagery
([`gisimageryingov`](https://registry.opendata.aws/in-imagery/)) resampled north-up onto the
site grid.

Checks:

```sh
python3 tools/verify_model.py spec/site_spec.json --lidar <site-frame .npz> --out /tmp/verify
python3 tools/preview.py spec/site_spec.json mybuild        # builds preview/mybuild/ and screenshots it
node tools/screenshot.js "http://localhost:8765/?ui=0" out.png front   # any view id, or a camera as JSON
```

## Coordinates

Everything is in a local *site frame*: NAD83 / UTM zone 16N (EPSG:26916) metres with the
origin at the address point (E 651405.348, N 4560211.139), `x` east, `y` north, `z` NAVD88
elevation. Grid north is 1.19° east of true north; the sun study corrects for it.

## Limits

This is a reconstruction for visualisation, not a survey. The parcel line is the county GIS
line and is not a legal boundary. The model shows the exterior only. No owner names, sale
prices, assessed values, real interior layout or private photographs are included (the game's
interior is invented).

## Credits and licences

LiDAR: U.S. Geological Survey 3D Elevation Program (public domain). Orthoimagery: State of
Indiana, Indiana Geographic Information Office (CC0). three.js (MIT). Barlow and IBM Plex
Mono typefaces (SIL Open Font License).
