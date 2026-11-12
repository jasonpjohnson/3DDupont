# 3910 W Dupont Rd — 3D site model

An interactive 3D model of the house and its 11.9-acre parcel in Fort Wayne, Indiana,
reconstructed from public data: an airborne LiDAR survey, the state's aerial photography,
and the county assessor's record card.

**View it:** https://jasonpjohnson.github.io/3DDupont/

Drag to orbit, scroll or pinch to zoom, right-drag (two fingers) to pan. Click any part of
the model for what it is and how it is known; click the ground for its surveyed elevation.

## What is in the viewer

- **Photo / Clay / Evidence** display. *Evidence* colours every part by its basis: measured
  from LiDAR, taken from the county record, read from a photograph, or inferred.
- **Layers** for the buildings, 580 trees located from the LiDAR canopy, the aerial
  photograph, 1 ft contours, the parcel line, the county sketch dimensions, and the raw
  LiDAR returns themselves.
- **Photo year**: the ground photograph from 1998 to 2024. Buildings newer than the
  photograph are hidden.
- **Sun study** for any date and time at this latitude, with leaf-on and leaf-off woods.
- **Measure** between any two points, and a downloadable `.glb` of the house.

## How it was made

| Source | Used for |
|---|---|
| USGS 3DEP LiDAR, flown March 2017 (public domain), about 4 returns/m² | roof planes, wall heights, terrain, tree positions and heights |
| USGS 3DEP LiDAR, flown April 2011 | cross-check, dating changes |
| Indiana Geographic Information Office orthoimagery 1998–2024 (CC0) | ground photograph, roof layout, site history |
| Allen County Assessor record card | dimensioned footprint sketch, floor areas, porches, outbuildings |
| Allen County Assessor street photograph (reference only) | cladding, colours, front openings |
| Allen County iMap and Indiana statewide GIS | parcel boundary, building outlines, contours |

The roof of each wing is described by planes fitted to the LiDAR returns; the builder takes
the lower envelope of a wing's planes and resolves the junctions between wings, so ridges,
hips and valleys are computed rather than drawn. `tools/verify_model.py` then scores the
result against every return that hit the roof. Wall lines follow the assessor's sketch,
registered to the surveyed eaves. Parts added after the 2017 survey follow the 2024 aerial
photograph and are marked as such.

The page's *Sources & method* panel lists the accuracy checks and everything that is
inferred rather than measured.

## Repository layout

```
index.html, css/, js/     the viewer (three.js, vendored in vendor/ — no CDN, no build step)
assets/                   generated: models (.glb), terrain, trees, point cloud, photos, manifest
spec/site_spec.json       the description the model is generated from; spec/SCHEMA.md documents it
tools/                    the pipeline (Python) and review helpers
```

## Rebuilding

```sh
pip install numpy scipy shapely pyproj mapbox_earcut pillow opencv-python-headless matplotlib "laspy[lazrs]" requests

python3 tools/fetch_lidar.py --out data/lidar                      # LiDAR from the USGS public bucket
python3 tools/build_model.py spec/site_spec.json assets/           # buildings -> .glb + parts catalogue
python3 tools/build_site.py data/lidar/IN_Statewide_B1_2017_raw3857.npz assets/ --spec spec/site_spec.json \
        [--ortho photo.png --cir infrared.png --history history.json]   # terrain, trees, points, photos
python3 tools/build_manifest.py spec/site_spec.json assets/        # what the viewer loads

python3 -m http.server 8765                                        # then open http://localhost:8765/
```

`build_site.py` without `--ortho` leaves the committed photographs untouched. The
photographs are crops of the state's public orthoimagery
([`gisimageryingov`](https://registry.opendata.aws/in-imagery/)) resampled north-up onto the
site grid.

Checks:

```sh
python3 tools/verify_model.py spec/site_spec.json --lidar <site-frame .npz> --out /tmp/verify
python3 tools/preview.py spec/site_spec.json mybuild        # builds preview/mybuild/ and screenshots it
```

## Coordinates

Everything is in a local *site frame*: NAD83 / UTM zone 16N (EPSG:26916) metres with the
origin at the address point (E 651405.348, N 4560211.139), `x` east, `y` north, `z` NAVD88
elevation. Grid north is 1.19° east of true north; the sun study corrects for it.

## Limits

This is a reconstruction for visualisation, not a survey. The parcel line is the county GIS
line and is not a legal boundary. No owner names, sale prices or assessed values are
included.

## Credits and licences

LiDAR: U.S. Geological Survey 3D Elevation Program (public domain). Orthoimagery: State of
Indiana, Indiana Geographic Information Office (CC0). three.js (MIT). Barlow and IBM Plex
Mono typefaces (SIL Open Font License).
