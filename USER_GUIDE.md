# BEA Generalisable Feasibility Model – User Guide (version 2)

This guide explains how to set up, prepare data for, run and tune the SAEON BioEnergy Atlas (BEA) feasibility model.
For *what changed* compared with the 2021 scripts, see `bea_feasibility_upgrades.txt`; for the method background see `README.md`.

**What the model does, in one run.** Given a layer of biomass (feedstock) polygons with tonnages, a road network and a choice of conversion technology and plant sizes, it:
1. **sites facilities**: starts at the largest remaining stand, fills a facility of the chosen capacity from it and its nearest neighbours, places the facility at the tonne-weighted centre, and repeats until all biomass is allocated;
2. **decides access per stand**: drive to an existing road directly, or use a pre-computed *virtual road* (an off-road tractor route from the Virtual_roads model);
3. **routes** every stand to its facility over the road network (shortest path) and records the distance per road class;
4. **costs** every facility: capital and operating cost from the technology cost curve, loading/unloading, road haulage, off-road haulage, finance (discounting) and product energy, giving R per tonne of feedstock, R per tonne of product, R/GJ and R/kWh.

---

## Contents
1. [Install](#1-install)
2. [Quick start with the example data](#2-quick-start-with-the-example-data)
3. [Input data: what you need and where to get it](#3-input-data-what-you-need-and-where-to-get-it)
4. [Earth projections (coordinate reference systems)](#4-earth-projections-coordinate-reference-systems)
5. [Preparing your own data, step by step](#5-preparing-your-own-data-step-by-step)
6. [Where to put the files](#6-where-to-put-the-files)
7. [Running the model](#7-running-the-model)
8. [What you can change: all settings](#8-what-you-can-change-all-settings)
9. [Outputs and how to read them](#9-outputs-and-how-to-read-them)
10. [Model assumptions you should know](#10-model-assumptions-you-should-know)
11. [Tests](#11-tests)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Install

You need Python 3.11 or newer with GDAL, geopandas, rasterio, scipy and networkx (version 3 is fine). On Windows, use conda (Anaconda/Miniconda) with the conda-forge channel.

**Option A – new environment from the file in this repository**
```
conda env create -f environment.yml
conda activate bea_feasibility
```
**Option B – an existing environment** (for example `mucp`)
```
conda activate mucp
conda install -c conda-forge gdal geopandas rasterio scipy networkx pyproj pytest
```
Tested with Python 3.12, GDAL 3.11, geopandas 1.1, shapely 2.1, pandas 3.0, networkx 3.4 and rasterio 1.5.

Check the installation:
```
python run_bea.py --print-config
python -m pytest -q
```

---

## 2. Quick start with the example data

The repository includes an example: theoretical alien-invasive biomass for a subset of KwaZulu-Natal, CBP-Pelleting plants of 24 000, 48 000 and 84 000 t/yr. From the repository folder:
```
python run_bea.py
```
After about 30 s the results are in `results/<date_time>/`. Start with `scenario_summary.csv` and `facility_costs.csv`, and open `scenarios/CBP-Pelleting_24000/spatial.gpkg` in QGIS.

---

## 3. Input data: what you need and where to get it

| # | Input | Type | Required? | Setting |
|---|-------|------|-----------|---------|
| 1 | Feedstock polygons with id, area (ha), tonnes | polygons | yes | `paths.feedstock`, `[fields]` |
| 2 | Road network with a road-class field | lines | yes | `paths.roads`, `fields.road_class_field` |
| 3 | Virtual-road start and end vertices | points (2 files) | yes* | `paths.vr_start_vertices`, `paths.vr_end_vertices` |
| 4 | Proximity raster: distance to roads | raster, 100 m | recommended | `paths.road_proximity_raster` |
| 5 | Proximity raster: distance to virtual-road starts | raster, 100 m | recommended | `paths.vr_proximity_raster` |
| 6 | Technology cost curves | CSV | yes | `paths.cost_curves` |
| 7 | Product energy densities | CSV | yes | `paths.product_energy` |

\* Without virtual-road data, every stand is connected straight to the nearest road (see section 5.3).

### 3.1 Feedstock polygons
Any polygon layer of biomass sources. Each record needs three fields (the names are set in `[fields]`):

| Field (example name) | Meaning | Setting |
|----------------------|---------|---------|
| `NbalId` | identifier of the stand/area (text or number; duplicates allowed) | `fields.id_field` |
| `Area` | area in **hectares** | `fields.area_field` |
| `CSIR_T` | available biomass in **tonnes** (treated as tonnes per year, see section 10) | `fields.volume_field` |

The example uses alien-invasive clearing areas (NBAL ids from the Natural Resource Management / Working for Water programme of the Department of Forestry, Fisheries and the Environment, DFFE) with *theoretical* biomass estimates (CSIR). For your own work, possible sources are:
* **alien invasive plants**: DFFE Natural Resource Management programme clearing data (on request from DFFE, <https://www.dffe.gov.za>), and the National Invasive Alien Plant Survey (NIAPS) for Working for Water (availability on request);
* **forestry residues**: plantation compartments from forestry companies or the DFFE forestry data, tonnes = area x residue yield;
* **agricultural residues**: crop field boundaries (for example national field crop boundary data from the Department of Agriculture, on request), tonnes = area x crop yield x residue ratio.

If your data has an area and a yield (t/ha) but no tonnes, compute tonnes in QGIS (section 5.1).

### 3.2 Road network
The example uses the **National Geo-spatial Information (NGI)** 1:50 000 road layer. Its road-class field `FEAT_TYPE` has values such as `Arterial Road`, `Main Road`, `Secondary Road`, `Street` and `Other Road`.
* NGI: <https://www.ngi.gov.za>; vector data through the CD:NGI geospatial portal (<http://www.cdngiportal.co.za>) or on request from NGI.
* Alternative: **OpenStreetMap** from Geofabrik, <https://download.geofabrik.de/africa/south-africa.html> → `south-africa-latest-free.shp.zip`, layer `gis_osm_roads_free` (road class field `fclass`: `motorway`, `primary`, `secondary`, `tertiary`, `residential`, `track`, ...). Set `fields.road_class_field = "fclass"`.

The road class is used to report distance per class, for optional exclusions and for weighting (section 8). Use a road layer that extends 10–20 km beyond your feedstock area, so facilities near the edge have roads to use.

### 3.3 Virtual-road vertices
These come from the **Virtual_roads** model (separate repository). Run it for the same area. It writes:
* `bea_start_vertices.gpkg`: the start of each virtual road (the off-road cell);
* `bea_end_vertices.gpkg`: where that virtual road meets an existing road.

Both have the fields `fid2` (links start to end) and `length` (virtual-road length in metres). The 2021 files in `data/` (`start_vertices.shp`, `end_vertices.shp`) have the same fields and still work; their MultiPoint geometry is handled automatically.

### 3.4 Proximity rasters (two)
These are 100 m rasters with the distance in metres from every cell to (a) the nearest road and (b) the nearest virtual-road start vertex. For each stand the model compares the two values: if the road is at least as close, the stand drives to the road directly; otherwise it uses the virtual road. Build them yourself (section 5.4). If they are missing or do not cover a stand, the model computes the same comparison exactly from the vector data (`access.method = "vector"` does this for every stand).

### 3.5 Technology cost curves (`parameters/Technology_cost_curves.csv`)
One row per technology:

| Column | Meaning |
|--------|---------|
| `ID` | technology name used in `scenario.technologies`, for example `CBP-Pelleting` |
| `Prod_id` | product; must exist in the energy-density table |
| `Efficiency` | % of feedstock mass that becomes product (mass yield) |
| `lifespan_years` | plant life (used when `costs.time_horizon_years = 0`) |
| `capacity1..3` | three plant sizes in **t feedstock per year** |
| `capex1..3` | capital cost in **R per t/yr of capacity** at each size |
| `opex1..3` | operating cost in **R per t of feedstock** at each size |

Technologies currently in the table: APR, Ferm-Hydro, TRANS-1, TRANS-2, AD-1, AD-2, AD-3, CFP-UP, PY-Fast, But-Ligno, But-Starch, BIGCC, COMB-CHP, COMB-EL, COMB-IC, COMB-Tri, TORR-1, TORR-2, Eth-Ligno, Eth-Starch, Eth-Sugar, Sep-Cat, Syn-Cat, Syn-Fer, S2D-Hyd, Small-FT-Road_HT, Syn-FT-Road_HT, Syn-FT-Road_LT, But-to-Jet, Eth-to-Jet, Ferm-Hydro-Jet, FFA-to-Jet, HEFA-1, Lip-to-Jet, Org-acid-to-Jet, Small-FT-Jet_HT, Syn-FT-Jet_HT, Syn-FT-Jet_LT, PY-EL, HPy, HTL, PY-FT-Central, PY-FT-Disperse, CBP-Pelleting, Syn-Prd_1..3, CBP-Chipping.

**Check before use:**
* `But-Molasses` and `Eth-Molasses` have no product or efficiency, so they cannot be run until these are filled in.
* Some capacity columns look like they are in other units (for example AD-1: 2.5 / 4.9 / 9.9). Make sure all capacities are t/yr.

To add a technology, add a row. You can also change prices here (for example to update to current Rand values).

### 3.6 Product energy densities (`parameters/Biofuel energy densities.csv`)
Two columns: `Prod_id` and `Energy_(MJ/kg)`. Matching to `Prod_id` in the cost curves is case-insensitive. **Note:** the table lists `Electricity, 1`. If that is meant as 1 kWh per kg of output it should be 3.6 MJ/kg; check this with the data owner before quoting electricity results.

---

## 4. Earth projections (coordinate reference systems)

| Data | Projection it may be in | Notes |
|------|------------------------|-------|
| Feedstock, roads, VR vertices | any, **but the CRS must be defined** (`.prj` or GeoPackage CRS) | reprojected automatically |
| Proximity rasters | a **projected CRS in metres**. Recommended: **South African Albers equal area** (below) | values must be metres |
| Working CRS (all distances, clustering, routing) | automatic **UTM zone** estimated from the feedstock data, or `data.working_crs` | must be in metres |
| Outputs | `data.output_crs`, default **WGS 84 (EPSG:4326)** | |

**South African Albers equal area** (used by the BSU grid, the proximity rasters and Virtual_roads):
```
+proj=aea +lat_1=-22 +lat_2=-38 +lat_0=-30 +lon_0=25 +x_0=1400000 +y_0=1300000 +datum=WGS84 +units=m +no_defs
```
**Choosing the working CRS**
* One region (a district, a province within one UTM zone): leave `working_crs = ""`. The model picks the UTM zone itself: 33S (EPSG:32733) for the west coast, 34S (32734), 35S (32735) or 36S (32736) for KZN/east. The example uses 36S.
* National or multi-zone runs: use the Albers string above (`working_crs = "+proj=aea +lat_1=-22 ..."`). UTM distances grow too inaccurate far outside their zone.

South African data in **Hartebeesthoek94** (EPSG:4148) or the **Lo** systems (for example EPSG:2048 Lo29) is fine as long as the CRS is defined. It differs from WGS 84 by less than about 1 m.

**Missing CRS?** In QGIS: *Layer Properties → Source → Assigned CRS*. Or: `ogr2ogr -a_srs EPSG:4326 fixed.gpkg input.shp` (`-a_srs` assigns, `-t_srs` transforms).

---

## 5. Preparing your own data, step by step

The GDAL commands below come with the conda GDAL package (*Anaconda Prompt* → `conda activate <env>`). QGIS has the same tools in its menus.

### 5.1 Feedstock layer
1. Load your polygons in QGIS and check the CRS (section 4).
2. If there is no area field in hectares: *Field Calculator* → new field `area_ha` = `$area / 10000`. QGIS computes `$area` in the project's ellipsoid settings; set the project CRS to a metre-based CRS.
3. If there are no tonnes: new field `tonnes` = `area_ha * <yield t/ha>` (or from your own yield field).
4. Remove stands without biomass (they are ignored anyway: `data.min_volume_t`).
5. Set the field names in your settings file (`[fields] id_field / area_field / volume_field`).

Exact duplicate polygons (same id and geometry) are removed automatically and reported in `run.log`. The 2021 example contained 84 such copies, which inflated tonnage by 18.9%.

### 5.2 Roads
```
ogr2ogr -clipsrc area_buffered_20km.shp roads_myarea.gpkg national_roads.shp
```
For OpenStreetMap roads you may want to drop footpaths and tracks first, or exclude them with `network.use_exclusions = true` and `network.excluded_classes = ["footway", "path", "steps", "cycleway"]`.

### 5.3 Virtual-road vertices
Run the Virtual_roads model for your area (see its `USER_GUIDE.md`), then point to its outputs:
```toml
[paths]
vr_start_vertices = "C:/.../Virtual_roads/results/<run>/bea_start_vertices.gpkg"
vr_end_vertices   = "C:/.../Virtual_roads/results/<run>/bea_end_vertices.gpkg"
```
No virtual roads available? Use the 2021 national files if they cover your area, or set `access.method = "vector"` and point both VR paths to the files in `data/`. Stands far from any VR vertex will then simply use the nearest road, and the straight-line distance to it is costed as off-road haulage (`costs.cost_access_as_offroad`).

### 5.4 Proximity rasters (tested recipe)
Build them from **national or regional** layers (not ones already clipped to your area), so cells near the edge also see roads just outside it. Use the Albers projection, 100 m cells, an extent covering your area plus a margin, and Int32 values. The 2021 files are Int16, which caps distances at 32.7 km.

Replace `XMIN YMIN XMAX YMAX` with your extent in Albers metres. You can read it from a layer reprojected to Albers in QGIS, or from `gdalinfo` of a grid template; round it to multiples of 100.
```
set ALBERS=+proj=aea +lat_1=-22 +lat_2=-38 +lat_0=-30 +lon_0=25 +x_0=1400000 +y_0=1300000 +datum=WGS84 +units=m +no_defs

:: distance to roads
ogr2ogr -t_srs "%ALBERS%" -nlt PROMOTE_TO_MULTI roads_albers.gpkg national_roads.shp
gdal_rasterize -burn 1 -init 0 -at -ot Byte -tr 100 100 -te XMIN YMIN XMAX YMAX roads_albers.gpkg roads_100m.tif
gdal_proximity roads_100m.tif NGI_roads_100m_proximity.tif -values 1 -distunits GEO -ot Int32

:: distance to virtual-road start vertices
ogr2ogr -t_srs "%ALBERS%" vr_start_albers.gpkg bea_start_vertices.gpkg
gdal_rasterize -burn 1 -init 0 -ot Byte -tr 100 100 -te XMIN YMIN XMAX YMAX vr_start_albers.gpkg vr_100m.tif
gdal_proximity vr_100m.tif VR_roads_100m_proximity.tif -values 1 -distunits GEO -ot Int32
```
(In a Linux/macOS terminal use `ALBERS="..."` and `"$ALBERS"`. `gdal_proximity` may be called `gdal_proximity.py` on some installations.) This recipe was checked against the shipped example rasters: same grid, and identical values for most cells. They differ near the edges because the shipped rasters were built from national layers.

### 5.5 Cost and energy tables
Copy `parameters/` to your project if you want to change prices, then set `paths.cost_curves` / `paths.product_energy` to your copies.

---

## 6. Where to put the files

Default layout (all paths can be changed in the settings):
```
BEA_Feasibility_model_test/
├── run_bea.py                       <- the command you run
├── config.toml                      <- all settings (copy it for your own runs)
├── inputs/
│   ├── KZN_resampled.shp (+.dbf .prj .shx)   <- feedstock polygons   (paths.feedstock)
│   └── BEA_roads1.shp                         <- roads                (paths.roads)
├── data/
│   ├── start_vertices.shp, end_vertices.shp   <- virtual-road vertices
│   ├── NGI_roads_100m_proximity.tif           <- distance to roads
│   └── VR_roads_100m_proximity.tif            <- distance to VR starts
├── parameters/
│   ├── Technology_cost_curves.csv             <- cost curves (paths.cost_curves)
│   └── Biofuel energy densities.csv           <- energy (paths.product_energy)
├── results/<run_name>/                        <- created by each run
├── bea/                                       <- model code
├── tests/                                     <- automated tests
└── legacy/                                    <- original 2021 scripts (reference only - do not run)
```
**Recommended for your own projects:** a separate folder per project with its own settings file. Relative paths are resolved from the settings file's folder:
```
D:\projects\eastern_cape\
├── ec.toml
├── data\feedstock_ec.gpkg, roads_ec.gpkg, bea_start_vertices.gpkg, bea_end_vertices.gpkg
├── data\NGI_roads_100m_proximity.tif, VR_roads_100m_proximity.tif
└── parameters\Technology_cost_curves.csv, Biofuel energy densities.csv
```
`ec.toml` (only what differs from the defaults):
```toml
[paths]
feedstock = "data/feedstock_ec.gpkg"
roads = "data/roads_ec.gpkg"
vr_start_vertices = "data/bea_start_vertices.gpkg"
vr_end_vertices = "data/bea_end_vertices.gpkg"
road_proximity_raster = "data/NGI_roads_100m_proximity.tif"
vr_proximity_raster = "data/VR_roads_100m_proximity.tif"
cost_curves = "parameters/Technology_cost_curves.csv"
product_energy = "parameters/Biofuel energy densities.csv"

[fields]
id_field = "stand_id"
area_field = "area_ha"
volume_field = "tonnes"

[scenario]
feedstock_name = "Wattle"
technologies = ["CBP-Pelleting", "TORR-1"]
capacities_t = [20000, 40000]
```
Run it with `python C:\path\to\BEA_Feasibility_model_test\run_bea.py --config D:\projects\eastern_cape\ec.toml`.

---

## 7. Running the model

| Command | What it does |
|---------|--------------|
| `python run_bea.py` | run with `config.toml` (the example) |
| `python run_bea.py --config my.toml` | run with your settings file |
| `python run_bea.py --set paths.run_name=base` | name the run folder `results/base` |
| `python run_bea.py --set "scenario.capacities_t=[30000, 60000]"` | other plant sizes |
| `python run_bea.py --set "scenario.technologies=['TORR-1']" --set "scenario.capacities_t=[]"` | another technology, at the three sizes in its cost curve |
| `python run_bea.py --set costs.discount_rate=0.10 --set costs.escalation_rate=0.05` | change finance assumptions |
| `python run_bea.py --print-config` | show the final settings without running |
| `python run_bea.py --help` | help |

Settings are applied in the order **built-in defaults → settings file → `--set`** (later wins). `--set` values follow TOML rules: numbers `0.1`, booleans `true`, lists `[24000, 48000]`, lists of text `['CBP-Pelleting','TORR-1']` (single quotes avoid escaping on the Windows command line), maps `{"Street" = 1.2}`. Quote the whole argument when it contains spaces or quotes.

**Scenarios.** Every combination of `scenario.technologies` x `scenario.capacities_t` is one scenario, all in the same run, followed by the cost step. Run time for the example is about 30 s for 3 scenarios. Many small plants take longer, because each facility is routed separately (CBP-Chipping at its curve sizes 500 / 2 786 / 70 000 t: 323 + 58 + 3 facilities, about 10 minutes).

**Resume after a crash or interruption.** Run the same command again with the same `paths.run_name`. Finished scenarios (folders with a `DONE` file) are skipped (`run.resume = true`). A new `run_name` always starts fresh. Earlier runs are never deleted.

**Comparing assumptions.** Give each run its own name and compare their `scenario_summary.csv` files:
```
python run_bea.py --set paths.run_name=base
python run_bea.py --set paths.run_name=disc10 --set costs.discount_rate=0.10
python run_bea.py --set paths.run_name=median --set clustering.facility_location=geometric_median
python run_bea.py --set paths.run_name=legacy_like --set clustering.weight_by=available --set costs.cost_curve_method=first
```

---

## 8. What you can change: all settings

All settings are in `config.toml` with comments. The defaults are the values that were fixed in the original code (marked *orig.*).

### [paths]
`feedstock`, `roads`, `vr_start_vertices`, `vr_end_vertices`, `vr_proximity_raster`, `road_proximity_raster`, `cost_curves`, `product_energy` (input files, see sections 3 and 6), `output_dir` (default `results`) and `run_name` (`""` = date_time; reuse a name to resume).

### [scenario]
| Setting | Default | Meaning |
|---------|---------|---------|
| `feedstock_name` | `Aliens` *orig.* | label written to the outputs |
| `technologies` | `["CBP-Pelleting"]` *orig.* | IDs from the cost-curve table |
| `capacities_t` | `[24000, 48000, 84000]` *orig.* | plant sizes in t/yr; `[]` = the three sizes in each technology's curve |

### [fields]
| Setting | Default | Meaning |
|---------|---------|---------|
| `id_field` | `NbalId` *orig. position 1* | feedstock id |
| `area_field` | `Area` *orig. position 2* | hectares |
| `volume_field` | `CSIR_T` *orig. position 14* | tonnes |
| `road_class_field` | `FEAT_TYPE` *orig.* | road class in the road layer |
| `vr_id_field`, `vr_length_field` | `fid2`, `length` *orig.* | VR vertex link id and length (m) |

### [data]
| Setting | Default | Meaning |
|---------|---------|---------|
| `drop_duplicate_features` | `true` | remove exact duplicate feedstock features |
| `min_volume_t` | `0` *orig.* | stands with tonnes at or below this are ignored |
| `working_crs` | `""` | `""` = automatic UTM zone; or for example `"EPSG:32735"` or the Albers string (section 4) |
| `output_crs` | `EPSG:4326` *orig.* | CRS of the GeoPackage layers |

### [clustering] – facility siting
| Setting | Default | Meaning |
|---------|---------|---------|
| `facility_location` | `weighted_mean` *orig.* | `geometric_median` places the plant where tonne-distance is smallest (less pulled by far-away stands) |
| `weight_by` | `contributed` | weight stands by the tonnes they actually supply; `available` = *orig.* (whole stand volume) |
| `remaining_tolerance_t` | `0.001` | leftover tonnes below this count as used up |

### [access] – existing road or virtual road
| Setting | Default | Meaning |
|---------|---------|---------|
| `method` | `raster` *orig.* | `raster`: proximity rasters, exact vector fallback where they do not cover; `vector`: always exact |
| `vr_candidates` | `4` *orig.* | nearest VR start vertices considered (ties go to the shortest virtual road) |

### [network] – routing
| Setting | Default | Meaning |
|---------|---------|---------|
| `use_exclusions` | `false` *orig.* | remove some road classes from the network |
| `excluded_classes` | `["bad road", "Dirt Road"]` *orig.* | classes removed when `use_exclusions = true` |
| `class_weight_factors` | `{}` | make some classes less attractive for route choice, for example `{ "Street" = 1.2, "Other Road" = 1.5 }`; reported distances stay true metres |
| `unreachable_tortuosity` | `1.3` | if no network path exists, straight-line distance x this factor is used (and flagged) |
| `node_precision_m` | `0.001` | coordinate rounding used to join road ends |

### [costs]
| Setting | Default | Meaning |
|---------|---------|---------|
| `time_horizon_years` | `0` | plant life for finance; `0` = `lifespan_years` from the cost curve (*orig.* behaviour) |
| `discount_rate` | `0.06` *orig.* | discount rate for present values and capital recovery |
| `escalation_rate` | `0.06` *orig.* | yearly growth of operating costs (inflation) |
| `cost_curve_method` | `loglinear` | how capex/opex are read from the 3-point curve: `loglinear` interpolation, `nearest` point, `first` (*orig.* always used point 1) |
| `capex_basis` | `throughput` *orig.* | capex x tonnes supplied, or `capacity` = capex x plant size (a part-full plant still pays for its full size) |
| `road_speed_kmh`, `offroad_speed_kmh` | `50`, `8` *orig.* | used for fleet sizing |
| `hours_per_day`, `truck_working_days` | `8`, `260` *orig.* | |
| `truck_annual_km` | `100000` *orig.* | basis for the truck cost per km |
| `road_payload_t`, `offroad_payload_t` | `26`, `4` *orig.* | t per truck / tractor load |
| `truck_standing_cost_r_per_year`, `truck_variable_cost_r_per_year` | `923352`, `1093536` *orig.* | truck cost per km = (standing + variable) / annual km ≈ R20.17/km |
| `offroad_haulage_r_per_hour` | `453.31` *orig.* | tractor cost per km = this / off-road speed ≈ R56.66/km |
| `load_cost_r_per_t`, `unload_cost_r_per_t` | `14.4`, `12.32` *orig.* | |
| `harvest_cost_r_per_ha` | `0` *orig.* | yearly harvesting cost per hectare of supplying stands |
| `round_up_trips` | `false` *orig.* | `true` = whole truck loads per stand |
| `cost_access_as_offroad` | `true` | cost the stand→road and stand→VR-start distances at the tractor rate |
| `min_haul_distance_m` | `0` | minimum one-way haul per stand (avoids zero transport cost for stands at the plant) |

### [output] and [run]
| Setting | Default | Meaning |
|---------|---------|---------|
| `output.write_catchments` | `true` | write the supplying polygons per facility |
| `run.log_level` | `INFO` | `DEBUG` for more detail |
| `run.overwrite` | `false` | allow writing into an existing run folder |
| `run.resume` | `true` | skip finished scenarios when re-using a run name |

---

## 9. Outputs and how to read them

`results/<run_name>/`:

| File | Content |
|------|---------|
| `scenario_summary.csv` | one row per technology x capacity: facilities, tonnes, mean % full, R per feedstock t, transport R/t, R/GJ |
| `facility_costs.csv` | one row per facility (all cost details, below) |
| `feedstock_routes_all.csv` | one row per (facility, stand) for all scenarios |
| `scenarios/<tech>_<capacity>/spatial.gpkg` | layers `facilities`, `feedstock_points`, `paths`, `catchments` |
| `scenarios/<tech>_<capacity>/feedstock_routes.csv`, `DONE` | per-scenario table and completion marker |
| `run.log`, `run_manifest.json` | log; settings used, library versions, input checksums, per-scenario statistics |

**`feedstock_routes_all.csv`: one row per stand and facility.** A stand can supply two facilities when its volume is split.

| Column | Meaning |
|--------|---------|
| `scenario_id`, `facility_id`, `facility_lon/lat` | which facility |
| `point_key`, `point_id`, `point_x/y` | the stand (unique key, your id, centroid in working CRS) |
| `area_ha`, `original_t`, `contributed_t`, `remaining_t` | stand area; tonnes available before, supplied, left after |
| `still_needed_t`, `percent_full` | facility fill after this stand |
| `access_type`, `access_method` | `road` or `vr`; decided by `raster` or `vector` |
| `vr_id`, `vr_m` | virtual road used and its length (m) |
| `access_m` | stand → road (or stand → VR start + VR end → road) straight-line distance (m) |
| `road_m_<class>`, `road_m` | network distance per road class and in total (m, one way) |
| `total_m` | road + VR + access (m) |
| `route_status` | `ok`, `zero_distance` (stand at the plant), `unreachable_fallback` (no network path; straight line x tortuosity used) |

**`facility_costs.csv`, main columns.** All money in Rand, per year unless stated.
* Size and supply: `capacity_t`, `tonnes_per_year`, `percent_full`, `feedstock_points`, `service_area_ha`.
* Finance: `time_horizon_years`, `discount_rate`, `escalation_rate`, `crf` (capital recovery factor), `pv_factor_operating`.
* Plant: `capex_r_per_t`, `opex_r_per_t` (from the curve), `capex_total_r`.
* Transport: `road_km_per_year`, `offroad_km_per_year`, `road_trips_per_year`, `offroad_trips_per_year`, `road_trucks_needed`, `offroad_tractors_needed`, `truck_cpk_r_per_km`, `tractor_cpk_r_per_km`.
* Year-1 costs: `opex_r_year1`, `load_r_year1`, `unload_r_year1`, `road_haul_r_year1`, `offroad_haul_r_year1`, `harvest_r_year1`, `operating_r_year1`.
* Totals:
  * `npv_total_cost_r` = capex + present value of all operating costs over the plant life;
  * `equivalent_annual_cost_r` = the same as a constant yearly amount (capex x CRF + PV(operating) x CRF);
  * `cost_r_per_feedstock_t` = equivalent annual cost / tonnes per year;
  * `transport_r_per_feedstock_t` = year-1 haulage / tonnes.
* Product: `product_t_per_year` (tonnes x efficiency), `energy_gj_per_year`, `cost_r_per_product_t`, `cost_r_per_gj`, `cost_r_per_kwh`.
* Quality flags: `routes_unreachable`, `routes_zero_distance`.

**In QGIS** (`spatial.gpkg`): show `catchments` coloured by `facility_id`, `paths` coloured by `facility_id`, and `facilities` labelled with `tonnes_per_year`.

Example results (defaults): 24 000 t → 7 facilities, R2 386 per t; 48 000 t → 4 facilities, R1 881 per t; 84 000 t → 2 facilities, R1 597 per t.

---

## 10. Model assumptions you should know

* **Tonnes are per year.** The feedstock tonnes are treated as an annual supply that is available every year of the plant life (as in the original model). For a one-off standing stock (for example a single alien-clearing operation), divide the tonnes by the years over which it will be harvested before running.
* **Greedy siting.** Facilities are filled from the largest remaining stand outwards. It is a good heuristic, but it does not guarantee the cheapest overall layout. The last facility of a scenario is usually only partly full.
* **Mass-yield conversion.** Product = feedstock tonnes x `Efficiency`/100. Moisture and heating value of the feedstock are not modelled.
* **Transport.** Return trips are counted (trucks drive back empty). Road speed is the same for every road class. Virtual roads have no construction cost, only the tractor running cost.
* **Prices** are those in the parameter tables (2019–2021 sources). Update them for current studies.

---

## 11. Tests

```
python -m pytest -q
```
There are 18 tests (about 20–40 s), covering settings handling, finance formulas, cost-curve interpolation, facility siting, road routing (snapping, unreachable and zero-distance cases, weights, exclusions), the road/virtual-road choice, haulage costing and a full example run with resume. `pytest.ini` restricts the tests to `tests/`.

> **Do not run the scripts in `legacy/`.** They are kept for reference only, do not work on current libraries, and the original `BEA_model_test.py` deletes the `outputs/` folder of the directory it runs in.

---

## 12. Troubleshooting

| Problem | Cause / fix |
|---------|-------------|
| `Input not found` | wrong path; relative paths are resolved from the settings file's folder |
| `Feedstock layer lacks field(s) [...]` | set `[fields]` to your column names (the message lists the available ones) |
| `... has no CRS defined` | assign the CRS (section 4) |
| `Technology 'X' not found` / `Product 'Y' not found` | spelling must match the `ID` / `Prod_id` columns of the parameter tables |
| `Unknown config key` / `must be a number` | typo or wrong type in a setting; see `--print-config` |
| `... exists; set run.resume = true ...` | choose a new `paths.run_name`, or resume/overwrite |
| many `unreachable_fallback` | the road layer has gaps or islands (`run.log` shows the number of connected components); use a more complete road layer, extend it beyond the area, or check `use_exclusions` |
| many `zero_distance` | stands at the facility site; consider `costs.min_haul_distance_m` |
| warning `VR start vertices have no matching end vertex` | VR start/end files from different runs or areas; they are skipped |
| `ModuleNotFoundError` | install the missing package into the active environment (section 1) |

**About the links.** Geofabrik was reachable in November 2026. The South African government portals (NGI, CD:NGI, DFFE) could not be opened by an automated check at the time of writing (they may block automated access or have moved). If a link fails, search for the dataset name given here, or contact the provider directly.
