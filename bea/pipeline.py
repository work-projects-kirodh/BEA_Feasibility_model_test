"""One unbroken run of the whole model: inputs -> every scenario (spatial threads) ->
cost thread -> combined outputs and run manifest.

Replaces the two-script workflow (BEA_model_test.py, then Process_feasibility_options.py
with hand-edited absolute paths). Previous runs are never deleted (original wiped
outputs/ at start, D-22); with run.resume = true a crashed or interrupted run continues
from the first unfinished scenario.
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import subprocess
import time
from datetime import datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd

from . import costs, data, spatial
from .access import AccessModel
from .network import RoadNetwork

log = logging.getLogger("bea")
REPO = Path(__file__).resolve().parent.parent


def resolve_paths(cfg: dict, base: Path) -> None:
    for key, value in cfg["paths"].items():
        if key == "run_name" or not value:
            continue
        if not Path(value).is_absolute():
            cfg["paths"][key] = str((base / value).resolve())


def prepare_run_dir(cfg: dict) -> Path:
    name = cfg["paths"]["run_name"] or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(cfg["paths"]["output_dir"]) / name
    if run_dir.exists() and any(run_dir.iterdir()) and not (cfg["run"]["overwrite"] or cfg["run"]["resume"]):
        raise FileExistsError(f"{run_dir} exists; set run.resume = true to continue it, "
                              "run.overwrite = true, or choose another run_name")
    (run_dir / "scenarios").mkdir(parents=True, exist_ok=True)
    return run_dir


def setup_logging(run_dir: Path, level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    for h in list(root.handlers):
        if getattr(h, "_bea", False):
            root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    for h in (logging.StreamHandler(), logging.FileHandler(run_dir / "run.log", encoding="utf8")):
        h.setFormatter(fmt)
        h._bea = True
        root.addHandler(h)
    for noisy in ("pyogrio", "fiona", "rasterio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(run_dir: Path, cfg: dict, started: datetime, stats: dict) -> None:
    import networkx, numpy, shapely  # noqa: E401
    from osgeo import gdal
    try:
        commit = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True,
                                text=True, timeout=10).stdout.strip()
    except Exception:  # git not available is not a model error
        commit = ""
    hashes = {}
    for key in ("feedstock", "roads", "vr_start_vertices", "vr_end_vertices", "cost_curves",
                "product_energy", "vr_proximity_raster", "road_proximity_raster"):
        p = cfg["paths"][key]
        if p and Path(p).exists():
            hashes[key] = _sha256(p)
    manifest = {
        "model": "BEA_Feasibility_model", "status": stats.get("status"),
        "started": started.isoformat(timespec="seconds"),
        "finished": datetime.now().isoformat(timespec="seconds"),
        "git_commit": commit, "python": platform.python_version(),
        "libraries": {"gdal": gdal.__version__, "geopandas": gpd.__version__,
                      "networkx": networkx.__version__, "numpy": numpy.__version__,
                      "pandas": pd.__version__, "shapely": shapely.__version__},
        "inputs_sha256": hashes, "config": cfg, "results": stats,
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, default=str))


def _write_scenario(res: dict, sdir: Path, out_crs: str) -> None:
    sdir.mkdir(parents=True, exist_ok=True)
    res["table"].to_csv(sdir / "feedstock_routes.csv", index=False)
    gpkg = sdir / "spatial.gpkg"
    gpkg.unlink(missing_ok=True)
    res["facilities"].to_crs(out_crs).to_file(gpkg, layer="facilities", driver="GPKG")
    res["paths"][res["paths"].geometry.notna()].to_crs(out_crs).to_file(gpkg, layer="paths", driver="GPKG")
    pts = gpd.GeoDataFrame(res["table"], geometry=gpd.points_from_xy(res["table"].point_x, res["table"].point_y),
                           crs=res["paths"].crs)
    pts.to_crs(out_crs).to_file(gpkg, layer="feedstock_points", driver="GPKG")
    if res["catchments"] is not None:
        res["catchments"].to_crs(out_crs).to_file(gpkg, layer="catchments", driver="GPKG")
    (sdir / "DONE").write_text(datetime.now().isoformat(timespec="seconds"))


def run(cfg: dict, base: Path = REPO) -> Path:
    started, t0 = datetime.now(), time.perf_counter()
    resolve_paths(cfg, base)
    run_dir = prepare_run_dir(cfg)
    setup_logging(run_dir, cfg["run"]["log_level"])
    log.info("BEA feasibility run -> %s", run_dir)
    stats: dict = {"status": "failed", "scenarios": {}}
    try:
        curves, energy = data.load_tables(cfg)
        feed = data.load_feedstock(cfg)
        stats["duplicates_removed"] = feed.duplicates_removed
        stats["feedstock_t"] = round(float(feed.points.volume_t.sum()), 3)
        roads = data.read_layer(cfg["paths"]["roads"]).to_crs(feed.crs)
        network = RoadNetwork(roads, cfg["fields"]["road_class_field"], cfg)
        vr = data.load_vr_vertices(cfg, feed.crs)
        access = AccessModel(cfg, feed.crs, vr, network)

        tables = []
        for tech in cfg["scenario"]["technologies"]:
            for cap in data.scenario_capacities(cfg, curves, tech):
                sid = spatial.scenario_id(tech, cap)
                sdir = run_dir / "scenarios" / sid
                if cfg["run"]["resume"] and (sdir / "DONE").exists():
                    log.info("%s already finished in this run folder - skipped (resume)", sid)
                    table = pd.read_csv(sdir / "feedstock_routes.csv")
                else:
                    res = spatial.run_scenario(cfg, feed, network, access, tech, cap)
                    _write_scenario(res, sdir, cfg["data"]["output_crs"])
                    table = res["table"]
                tables.append(table)
                stats["scenarios"][sid] = {"facilities": int(table.facility_id.nunique()),
                                           "rows": len(table),
                                           "tonnes": round(float(table.contributed_t.sum()), 3),
                                           "route_status": table.route_status.value_counts().to_dict()}
        combined = pd.concat(tables, ignore_index=True).fillna(
            {c: 0.0 for c in pd.concat(tables).columns if c.startswith("road_m_")})
        combined.to_csv(run_dir / "feedstock_routes_all.csv", index=False)
        fac_costs = costs.facility_costs(combined, cfg, curves, energy)
        fac_costs.to_csv(run_dir / "facility_costs.csv", index=False)
        summary = fac_costs.groupby(["technology", "capacity_t"]).apply(
            lambda g: pd.Series({
                "facilities": len(g), "tonnes_per_year": g.tonnes_per_year.sum(),
                "mean_percent_full": g.percent_full.mean(),
                "cost_r_per_feedstock_t": g.equivalent_annual_cost_r.sum() / g.tonnes_per_year.sum(),
                "transport_r_per_feedstock_t": (g.road_haul_r_year1.sum() + g.offroad_haul_r_year1.sum())
                / g.tonnes_per_year.sum(),
                "cost_r_per_gj": g.equivalent_annual_cost_r.sum() / g.energy_gj_per_year.sum(),
            }), include_groups=False).reset_index()
        summary.to_csv(run_dir / "scenario_summary.csv", index=False)
        log.info("Scenario summary:\n%s", summary.round(1).to_string(index=False))
        stats["status"] = "complete"
        return run_dir
    finally:
        stats["seconds_total"] = round(time.perf_counter() - t0, 2)
        write_manifest(run_dir, cfg, started, stats)
        log.info("Finished (%s) in %.1f s", stats["status"], stats["seconds_total"])
