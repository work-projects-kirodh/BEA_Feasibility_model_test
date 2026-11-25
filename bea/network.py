"""Road network graph and one-to-many routing.

Replaces nx.read_shp (removed in NetworkX 3) and the per-facility clip / updatenode
round trips. The graph is built ONCE from the road layer in the working CRS (metres).
For each facility the facility and its feedstock points are snapped onto their nearest
road segment by temporarily splitting that edge; one Dijkstra from the facility gives
every route; the temporary nodes are then removed again. No clipping means no clip
rectangle can cut the network apart (original D-08 KeyError).
Unreachable points get a flagged straight-line fallback instead of crashing.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field

import geopandas as gpd
import networkx as nx
import numpy as np
import shapely
from shapely.geometry import LineString

log = logging.getLogger(__name__)


@dataclass
class RouteResult:
    status: str                       # ok | zero_distance | unreachable_fallback
    snap_m: float                     # point -> road distance
    length_by_class: dict[str, float] = field(default_factory=dict)
    coords: list[tuple[float, float]] = field(default_factory=list)  # start -> facility

    @property
    def road_m(self) -> float:
        return float(sum(self.length_by_class.values()))


class RoadNetwork:
    def __init__(self, roads: gpd.GeoDataFrame, class_field: str, cfg: dict):
        net = cfg["network"]
        self.precision = net["node_precision_m"]
        self.tortuosity = net["unreachable_tortuosity"]
        factors = net["class_weight_factors"]
        excluded = {c.lower() for c in net["excluded_classes"]} if net["use_exclusions"] else set()
        if class_field not in roads.columns:
            log.warning("Road layer has no '%s' field; all roads are class 'Other'", class_field)
            roads = roads.assign(**{class_field: "Other"})
        roads = roads[roads.geometry.notna()].explode(index_parts=False)
        classes = roads[class_field].fillna("None").astype(str).to_numpy()
        keep = np.array([c.lower() not in excluded for c in classes])
        if excluded:
            log.info("Excluded %d road features of classes %s", int((~keep).sum()), sorted(excluded))
        self.G = nx.Graph()
        seg_geoms, seg_edges = [], []
        for geom, cls in zip(roads.geometry.to_numpy()[keep], classes[keep]):
            xy = np.asarray(geom.coords)[:, :2]
            factor = float(factors.get(cls, 1.0))
            for (x0, y0), (x1, y1) in zip(xy[:-1], xy[1:]):
                u, v = self._key(x0, y0), self._key(x1, y1)
                if u == v:
                    continue
                length = math.hypot(x1 - x0, y1 - y0)
                old = self.G.get_edge_data(u, v)
                if old is None or old["length"] > length:
                    self.G.add_edge(u, v, length=length, weight=length * factor, cls=cls, factor=factor)
                    if old is None:
                        seg_geoms.append(LineString([u, v]))
                        seg_edges.append((u, v))
        self.classes = sorted(set(classes[keep]))
        self.seg_edges = seg_edges
        self.seg_geoms = np.array(seg_geoms, dtype=object)
        self.tree = shapely.STRtree(self.seg_geoms)
        log.info("Road graph: %d nodes, %d edges, %d connected components, classes %s",
                 self.G.number_of_nodes(), self.G.number_of_edges(),
                 nx.number_connected_components(self.G), self.classes)

    def _key(self, x: float, y: float) -> tuple[float, float]:
        p = self.precision
        return (round(x / p) * p, round(y / p) * p)

    # ------------------------------------------------------------------ snapping
    def _snap(self, xy: np.ndarray):
        pts = shapely.points(xy)
        seg_idx = self.tree.nearest(pts)
        segs = self.seg_geoms[seg_idx]
        t = shapely.line_locate_point(segs, pts, normalized=True)
        snapped = shapely.line_interpolate_point(segs, t, normalized=True)
        return seg_idx, t, shapely.get_coordinates(snapped), shapely.distance(pts, snapped)

    def route_many(self, start_xy: np.ndarray, facility_xy: tuple[float, float]) -> tuple[list[RouteResult], tuple[float, float]]:
        """Route every start point to the facility. Returns results and the snapped facility."""
        allxy = np.vstack([start_xy, np.asarray(facility_xy)[None, :]])
        seg_idx, t, snapped, dist = self._snap(allxy)
        G = self.G
        temp_nodes, removed = [], []
        node_of: list = [None] * len(allxy)
        by_seg: dict[int, list[int]] = defaultdict(list)
        for i, s in enumerate(seg_idx):
            by_seg[int(s)].append(i)
        try:
            for s, members in by_seg.items():
                u, v = self.seg_edges[s]
                data = G.get_edge_data(u, v)
                chain = [u]
                for i in sorted(members, key=lambda k: t[k]):
                    if t[i] <= 1e-9:
                        node_of[i] = u
                    elif t[i] >= 1 - 1e-9:
                        node_of[i] = v
                    else:
                        node = ("tmp", i)
                        G.add_node(node, xy=(float(snapped[i, 0]), float(snapped[i, 1])))
                        temp_nodes.append(node)
                        node_of[i] = node
                        chain.append(node)
                chain.append(v)
                if len(chain) > 2:
                    removed.append((u, v, dict(data)))
                    G.remove_edge(u, v)
                    for a, b in zip(chain[:-1], chain[1:]):
                        L = math.dist(self._xy(a), self._xy(b))
                        G.add_edge(a, b, length=L, weight=L * data["factor"], cls=data["cls"],
                                   factor=data["factor"])
            fac_node = node_of[-1]
            _, paths = nx.single_source_dijkstra(G, fac_node, weight="weight")
            results = []
            fac_xy = self._xy(fac_node)
            for i in range(len(start_xy)):
                node = node_of[i]
                path = paths.get(node)
                if path is None:
                    straight = math.dist(self._xy(node), fac_xy) * self.tortuosity
                    results.append(RouteResult("unreachable_fallback", float(dist[i]),
                                               {"Unconnected": straight},
                                               [self._xy(node), fac_xy]))
                    continue
                by_class: dict[str, float] = defaultdict(float)
                for a, b in zip(path[:-1], path[1:]):
                    e = G[a][b]
                    by_class[e["cls"]] += e["length"]
                coords = [self._xy(n) for n in reversed(path)]
                status = "ok" if sum(by_class.values()) > 1e-6 else "zero_distance"
                results.append(RouteResult(status, float(dist[i]), dict(by_class), coords))
            return results, fac_xy
        finally:
            G.remove_nodes_from(temp_nodes)
            for u, v, data in removed:
                G.add_edge(u, v, **data)

    def _xy(self, node) -> tuple[float, float]:
        if isinstance(node, tuple) and node and node[0] == "tmp":
            return self.G.nodes[node]["xy"]
        return node

    def nearest_road_distance(self, xy: np.ndarray) -> np.ndarray:
        return self._snap(xy)[3]
