"""Input loading and validation.

Fixes: attributes are selected by NAME (original: positions [1, 2, 14]); exact duplicate
features are removed (example data: 84 copies, +18.9% feedstock); every feedstock record
gets a unique integer key (original searched by the non-unique NbalId); centroids are
computed in a projected CRS instead of degrees.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import CRS

log = logging.getLogger(__name__)


class InputError(ValueError):
    pass


@dataclass
class Feedstock:
    polygons: gpd.GeoDataFrame   # deduplicated polygons in working CRS, index = point_key
    points: gpd.GeoDataFrame     # centroids with point_key, point_id, area_ha, volume_t
    crs: CRS
    duplicates_removed: int


def read_layer(path: str) -> gpd.GeoDataFrame:
    if not Path(path).exists():
        raise InputError(f"Input not found: {path}")
    gdf = gpd.read_file(path)
    if gdf.crs is None:
        raise InputError(f"{path} has no CRS defined")
    return gdf


def working_crs(cfg: dict, gdf: gpd.GeoDataFrame) -> CRS:
    if cfg["data"]["working_crs"]:
        return CRS.from_user_input(cfg["data"]["working_crs"])
    crs = gdf.estimate_utm_crs()
    log.info("Working CRS estimated from the data: %s", crs.name)
    return crs


def load_feedstock(cfg: dict) -> Feedstock:
    f = cfg["fields"]
    gdf = read_layer(cfg["paths"]["feedstock"])
    missing = [f[k] for k in ("id_field", "area_field", "volume_field") if f[k] not in gdf.columns]
    if missing:
        raise InputError(f"Feedstock layer lacks field(s) {missing}; available: {list(gdf.columns)}")
    crs = working_crs(cfg, gdf)
    gdf = gdf.to_crs(crs)
    n0, removed = len(gdf), 0
    if cfg["data"]["drop_duplicate_features"]:
        key = gdf[f["id_field"]].astype(str) + "|" + gdf.geometry.to_wkb().apply(bytes.hex)
        gdf = gdf[~key.duplicated()].copy()
        removed = n0 - len(gdf)
        if removed:
            log.warning("Removed %d exact duplicate feedstock feature(s) (same id and geometry)", removed)
    gdf = gdf.reset_index(drop=True)
    gdf.index.name = "point_key"
    vol = pd.to_numeric(gdf[f["volume_field"]], errors="coerce")
    if vol.isna().any():
        log.warning("%d feedstock record(s) have a non-numeric volume and are ignored", int(vol.isna().sum()))
    pts = gpd.GeoDataFrame({
        "point_key": gdf.index.to_numpy(),
        "point_id": gdf[f["id_field"]].astype(str).to_numpy(),
        "area_ha": pd.to_numeric(gdf[f["area_field"]], errors="coerce").fillna(0).to_numpy(),
        "volume_t": vol.fillna(0).to_numpy(),
    }, geometry=gdf.geometry.centroid.to_numpy(), crs=crs)
    pts = pts[pts.volume_t > cfg["data"]["min_volume_t"]].reset_index(drop=True)
    log.info("Feedstock: %d polygons (%d duplicates removed), %d usable points, %.1f t total",
             n0, removed, len(pts), pts.volume_t.sum())
    return Feedstock(gdf, pts, crs, removed)


def load_vr_vertices(cfg: dict, crs: CRS) -> gpd.GeoDataFrame:
    """Join VR start and end vertices on their id. MultiPoints are exploded to Points
    (original: GetX() on MultiPoint -> crash on current GDAL). Start vertices without an
    end vertex are dropped and reported (original: silently misaligned lists)."""
    f = cfg["fields"]
    start = read_layer(cfg["paths"]["vr_start_vertices"]).to_crs(crs).explode(index_parts=False)
    end = read_layer(cfg["paths"]["vr_end_vertices"]).to_crs(crs).explode(index_parts=False)
    for name, layer in (("start", start), ("end", end)):
        for fld in (f["vr_id_field"], f["vr_length_field"]):
            if fld not in layer.columns:
                raise InputError(f"VR {name} vertices lack field '{fld}'")
    start = start.drop_duplicates(f["vr_id_field"])
    end = end.drop_duplicates(f["vr_id_field"])
    merged = start[[f["vr_id_field"], f["vr_length_field"], "geometry"]].merge(
        end[[f["vr_id_field"], "geometry"]], on=f["vr_id_field"], suffixes=("", "_end"))
    dropped = len(start) - len(merged)
    if dropped:
        log.warning("%d VR start vertices have no matching end vertex and are not used", dropped)
    out = gpd.GeoDataFrame({
        "vr_id": merged[f["vr_id_field"]].to_numpy(),
        "vr_length_m": pd.to_numeric(merged[f["vr_length_field"]], errors="coerce").fillna(0).to_numpy(),
        "end_x": gpd.GeoSeries(merged["geometry_end"], crs=crs).x.to_numpy(),
        "end_y": gpd.GeoSeries(merged["geometry_end"], crs=crs).y.to_numpy(),
    }, geometry=gpd.GeoSeries(merged["geometry"], crs=crs).to_numpy(), crs=crs)
    log.info("VR vertices: %d usable start/end pairs", len(out))
    return out


def load_tables(cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    curves = pd.read_csv(cfg["paths"]["cost_curves"])
    energy = pd.read_csv(cfg["paths"]["product_energy"])
    curves["ID"] = curves["ID"].astype(str).str.strip()
    curves["Prod_id"] = curves["Prod_id"].astype(str).str.strip()
    energy["Prod_id"] = energy["Prod_id"].astype(str).str.strip()
    for tech in cfg["scenario"]["technologies"]:
        row = curves[curves.ID.str.lower() == tech.lower()]
        if row.empty:
            raise InputError(f"Technology '{tech}' not found in {cfg['paths']['cost_curves']}")
        prod = row.Prod_id.iloc[0]
        if energy[energy.Prod_id.str.lower() == prod.lower()].empty:
            raise InputError(f"Product '{prod}' of '{tech}' not found in {cfg['paths']['product_energy']}")
    return curves, energy


def scenario_capacities(cfg: dict, curves: pd.DataFrame, tech: str) -> list[float]:
    caps = cfg["scenario"]["capacities_t"]
    if caps:
        return [float(c) for c in caps]
    row = curves[curves.ID.str.lower() == tech.lower()].iloc[0]
    return [float(row[f"capacity{i}"]) for i in (1, 2, 3)]


def to_array(points: gpd.GeoDataFrame) -> np.ndarray:
    return np.column_stack([points.geometry.x.to_numpy(), points.geometry.y.to_numpy()])
