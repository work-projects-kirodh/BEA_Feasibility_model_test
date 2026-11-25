"""Spatial threads for one scenario (technology x capacity): cluster -> access -> route.

The key structural fix: every feedstock point is ONE record carried through all steps.
The original kept parallel lists (ids, tonnes, areas, VR distances, coordinates) and
re-ordered only some of them in adjust_points(), so in the example 96% of path/CSV rows
carried another point's id, tonnes or VR distance (draft.txt T-B5).
"""
from __future__ import annotations

import logging
import re
import time

import geopandas as gpd
import numpy as np
import pandas as pd
from pyproj import Transformer
from shapely.geometry import LineString, Point

from . import cluster
from .access import AccessModel
from .data import Feedstock, to_array
from .network import RoadNetwork

log = logging.getLogger(__name__)


def class_column(cls: str) -> str:
    return "road_m_" + re.sub(r"[^0-9a-zA-Z]+", "_", cls).strip("_").lower()


def scenario_id(tech: str, capacity: float) -> str:
    return f"{tech}_{int(capacity) if float(capacity).is_integer() else capacity}"


def run_scenario(cfg: dict, feed: Feedstock, network: RoadNetwork, access: AccessModel,
                 tech: str, capacity: float) -> dict:
    sid = scenario_id(tech, capacity)
    t0 = time.perf_counter()
    pts = feed.points
    xy = to_array(pts)
    cl = cfg["clustering"]
    facilities = cluster.allocate(xy, pts.volume_t.to_numpy(), capacity, cl["facility_location"],
                                  cl["weight_by"], cl["remaining_tolerance_t"])
    to_ll = Transformer.from_crs(feed.crs, "EPSG:4326", always_xy=True)
    class_cols = {c: class_column(c) for c in network.classes + ["Unconnected"]}
    records, path_geoms, fac_rows = [], [], []
    for fac in facilities:
        members = np.array(fac.members)
        acc = access.classify(xy[members])
        routes, fac_snap = network.route_many(acc.route_xy, (fac.x, fac.y))
        lon, lat = to_ll.transform(fac.x, fac.y)
        fac_rows.append({"scenario_id": sid, "facility_id": fac.facility_id, "technology": tech,
                         "capacity_t": capacity, "tonnes_per_year": fac.tonnes,
                         "percent_full": 100 * fac.tonnes / capacity, "feedstock_points": len(members),
                         "facility_lon": lon, "facility_lat": lat,
                         "snapped_x": fac_snap[0], "snapped_y": fac_snap[1],
                         "geometry": Point(fac.x, fac.y)})
        for j, k in enumerate(members):
            p, rt = pts.iloc[k], routes[j]
            rec = {
                "scenario_id": sid, "feedstock": cfg["scenario"]["feedstock_name"],
                "technology": tech, "capacity_t": capacity, "facility_id": fac.facility_id,
                "facility_lon": lon, "facility_lat": lat,
                "point_key": int(p.point_key), "point_id": p.point_id,
                "point_x": p.geometry.x, "point_y": p.geometry.y,
                "area_ha": float(p.area_ha), "original_t": fac.original_t[j],
                "contributed_t": fac.contributed_t[j], "still_needed_t": fac.still_needed_t[j],
                "remaining_t": fac.remaining_t[j], "percent_full": fac.percent_full[j],
                "access_type": acc.kind[j], "access_method": acc.method[j],
                "vr_id": int(acc.vr_id[j]), "vr_m": float(acc.vr_m[j]),
                "access_m": float(acc.pre_access_m[j] + rt.snap_m),
                "route_status": rt.status,
            }
            for cls, col in class_cols.items():
                rec[col] = float(rt.length_by_class.get(cls, 0.0))
            rec["road_m"] = rt.road_m
            rec["total_m"] = rt.road_m + rec["vr_m"] + rec["access_m"]
            records.append(rec)
            path_geoms.append(LineString(rt.coords) if len(rt.coords) > 1 else None)
    table = pd.DataFrame(records)
    status = table.route_status.value_counts().to_dict()
    log.info("%s: %d facilities, %d feedstock rows, %.1f t, routes %s (%.1f s)", sid,
             len(facilities), len(table), table.contributed_t.sum(), status, time.perf_counter() - t0)
    paths = gpd.GeoDataFrame(table, geometry=path_geoms, crs=feed.crs)
    fac_gdf = gpd.GeoDataFrame(fac_rows, geometry="geometry", crs=feed.crs)
    catch = None
    if cfg["output"]["write_catchments"]:
        poly = feed.polygons.loc[table.point_key, ["geometry"]].reset_index()
        catch = gpd.GeoDataFrame(
            table[["scenario_id", "facility_id", "point_key", "point_id", "area_ha", "contributed_t"]]
            .reset_index(drop=True), geometry=poly.geometry.to_numpy(), crs=feed.crs)
    return {"scenario_id": sid, "table": table, "paths": paths, "facilities": fac_gdf,
            "catchments": catch}
