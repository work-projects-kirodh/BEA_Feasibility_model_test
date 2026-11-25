"""Decide how each feedstock point reaches the road network: directly (existing road) or
along a pre-computed virtual road (VR) from the Virtual_roads model.

Fixes: raster sampling uses an explicit lon/lat axis order (original relied on GDAL 3's
swapped EPSG:4326 axes); out-of-raster points fall back to an exact vector test instead of
an IndexError / silent negative-index wrap-around (D-14); VR start vertices without an end
vertex are never chosen (D-17); fewer than 4 candidates is fine (D-16).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import rasterio
from pyproj import CRS, Transformer
from scipy.spatial import cKDTree

from .network import RoadNetwork

log = logging.getLogger(__name__)


@dataclass
class Access:
    kind: np.ndarray          # "road" or "vr" per point
    route_xy: np.ndarray      # point where the network route starts (VR end vertex or the point)
    pre_access_m: np.ndarray  # point -> VR start vertex distance (0 for road points)
    vr_m: np.ndarray          # VR length (0 for road points)
    vr_id: np.ndarray         # chosen VR id (-1 for road points)
    method: np.ndarray        # "raster" or "vector" (fallback) per point


class RasterSampler:
    def __init__(self, path: str, crs: CRS):
        self.src = rasterio.open(path)
        self.band = self.src.read(1, masked=True)
        self.tf = Transformer.from_crs(crs, self.src.crs, always_xy=True)

    def sample(self, xy: np.ndarray) -> np.ndarray:
        x, y = self.tf.transform(xy[:, 0], xy[:, 1])
        rows, cols = rasterio.transform.rowcol(self.src.transform, x, y)
        rows, cols = np.asarray(rows), np.asarray(cols)
        out = np.full(len(xy), np.nan)
        inside = (rows >= 0) & (rows < self.src.height) & (cols >= 0) & (cols < self.src.width)
        vals = self.band[rows[inside], cols[inside]]
        out[inside] = np.ma.filled(vals.astype("float64"), np.nan)
        return out


class AccessModel:
    def __init__(self, cfg: dict, crs: CRS, vr: gpd.GeoDataFrame, network: RoadNetwork):
        self.cfg, self.network, self.vr = cfg, network, vr
        self.vr_xy = np.column_stack([vr.geometry.x, vr.geometry.y]) if len(vr) else np.empty((0, 2))
        self.vr_tree = cKDTree(self.vr_xy) if len(vr) else None
        self.samplers = None
        p = cfg["paths"]
        if cfg["access"]["method"] == "raster" and p["vr_proximity_raster"] and p["road_proximity_raster"]:
            try:
                self.samplers = (RasterSampler(p["road_proximity_raster"], crs),
                                 RasterSampler(p["vr_proximity_raster"], crs))
            except Exception as exc:  # a missing/unreadable raster must not stop the model
                log.warning("Proximity rasters unavailable (%s); using the vector method", exc)

    def classify(self, xy: np.ndarray) -> Access:
        n = len(xy)
        d_road = np.full(n, np.nan)
        d_vr = np.full(n, np.nan)
        method = np.full(n, "vector", dtype=object)
        if self.samplers is not None:
            d_road, d_vr = self.samplers[0].sample(xy), self.samplers[1].sample(xy)
            ok = np.isfinite(d_road) & np.isfinite(d_vr)
            method[ok] = "raster"
        need = ~(np.isfinite(d_road) & np.isfinite(d_vr))
        if need.any():
            d_road[need] = self.network.nearest_road_distance(xy[need])
            d_vr[need] = (self.vr_tree.query(xy[need])[0] if self.vr_tree is not None
                          else np.inf)
        # original rule: an existing road wins ties (value <= value2)
        use_vr = (d_vr < d_road) & (self.vr_tree is not None)

        kind = np.where(use_vr, "vr", "road").astype(object)
        route_xy = xy.astype("float64").copy()
        pre, vr_m = np.zeros(n), np.zeros(n)
        vr_id = np.full(n, -1, dtype="int64")
        if use_vr.any():
            k = min(self.cfg["access"]["vr_candidates"], len(self.vr_xy))
            dist, idx = self.vr_tree.query(xy[use_vr], k=list(range(1, k + 1)))  # always 2-D
            lengths = self.vr.vr_length_m.to_numpy()[idx]
            rounded = np.round(dist, 6)
            best = np.empty(len(idx), dtype=int)
            for r in range(len(idx)):
                ties = np.flatnonzero(rounded[r] == rounded[r].min())
                best[r] = ties[np.argmin(lengths[r, ties])]     # tie -> shortest VR (original)
            chosen = idx[np.arange(len(idx)), best]
            rows = np.flatnonzero(use_vr)
            route_xy[rows, 0] = self.vr.end_x.to_numpy()[chosen]
            route_xy[rows, 1] = self.vr.end_y.to_numpy()[chosen]
            pre[rows] = dist[np.arange(len(idx)), best]
            vr_m[rows] = self.vr.vr_length_m.to_numpy()[chosen]
            vr_id[rows] = self.vr.vr_id.to_numpy()[chosen]
        return Access(kind, route_xy, pre, vr_m, vr_id, method)
