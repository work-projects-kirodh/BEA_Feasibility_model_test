"""Tests for the upgraded BEA feasibility model.

Run:  python -m pytest -q        (from the repository root)
The example-run tests take ~40 s in total.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from bea import cluster, config, costs, pipeline  # noqa: E402
from bea.access import AccessModel  # noqa: E402
from bea.network import RoadNetwork  # noqa: E402

UTM = "EPSG:32736"


# ---------------------------------------------------------------- config
def test_config_toml_equals_defaults():
    assert config.load_config(REPO / "config.toml") == config.load_config(None)


def test_overrides_lists_maps_and_validation():
    cfg = config.load_config(None, ["scenario.capacities_t=[30000, 60000]",
                                    'network.class_weight_factors={"Street" = 1.5}',
                                    "costs.round_up_trips=true"])
    assert cfg["scenario"]["capacities_t"] == [30000, 60000]
    assert cfg["network"]["class_weight_factors"] == {"Street": 1.5}
    assert cfg["costs"]["round_up_trips"] is True
    for bad in ["costs.nope=1", "costs.discount_rate=abc", "clustering.facility_location=x",
                "scenario.capacities_t=[-5]"]:
        with pytest.raises(config.ConfigError):
            config.load_config(None, [bad])


# ---------------------------------------------------------------- finance (D-04, D-05)
def test_finance_identities():
    assert costs.crf(0.06, 20) == pytest.approx(0.087185, abs=1e-6)
    assert costs.pv_escalating(0.06, 0.0, 20) == pytest.approx(11.4699, abs=1e-4)
    assert costs.pv_escalating(0.06, 0.06, 15) == pytest.approx(15 / 1.06)
    assert costs.crf(0.0, 10) == pytest.approx(0.1)


def test_cost_curve_interpolation():
    curve = pd.Series({"capacity1": 24000, "capacity2": 48000, "capacity3": 84000,
                       "capex1": 2658.43, "capex2": 1887.65, "capex3": 1427.79,
                       "opex1": 1383.63, "opex2": 1088.47, "opex3": 917.13})
    assert costs.curve_values(curve, 48000, "loglinear") == pytest.approx((1887.65, 1088.47))
    mid = costs.curve_values(curve, math.sqrt(24000 * 48000), "loglinear")[0]
    assert mid == pytest.approx((2658.43 + 1887.65) / 2)
    assert costs.curve_values(curve, 84000, "first") == (2658.43, 1383.63)     # original behaviour
    assert costs.curve_values(curve, 70000, "nearest")[0] == 1427.79
    assert costs.curve_values(curve, 1e6, "loglinear")[0] == 1427.79          # flat beyond the end


# ---------------------------------------------------------------- clustering
def test_allocation_mass_balance_and_nearest_fill():
    xy = np.array([[0, 0], [10, 0], [1000, 0], [20, 0]], float)
    vol = np.array([50.0, 30.0, 30.0, 30.0])
    facs = cluster.allocate(xy, vol, 100.0)
    assert sum(f.tonnes for f in facs) == pytest.approx(vol.sum())
    assert facs[0].members == [0, 1, 3]                     # largest first, then nearest (not 2)
    assert facs[0].contributed_t == [50, 30, 20]
    # second facility is seeded by the largest REMAINING volume (point 2: 30 t), then point 3
    assert facs[1].members == [2, 3] and facs[1].contributed_t == [30, 10]


def test_repeated_volumes_do_not_corrupt_bookkeeping():
    """Original removed list items by value; equal volumes then mixed up records (B1)."""
    xy = np.array([[0, 0], [5, 0], [10, 0], [500, 0]], float)
    vol = np.array([40.0, 40.0, 40.0, 40.0])
    facs = cluster.allocate(xy, vol, 60.0)
    given = np.zeros(4)
    for f in facs:
        for k, t in zip(f.members, f.contributed_t):
            given[k] += t
    assert given == pytest.approx(vol)


def test_facility_weighted_by_contributed_tonnes():
    xy = np.array([[0, 0], [100, 0]], float)
    facs = cluster.allocate(xy, np.array([90.0, 1000.0]), 100.0, weight_by="contributed")
    # seed is the 1000 t point, it alone fills the facility
    assert facs[0].members == [1] and facs[0].x == pytest.approx(100.0)
    f2 = cluster.allocate(np.array([[0, 0], [100, 0]], float), np.array([60.0, 50.0]), 100.0)[0]
    assert f2.x == pytest.approx(100 * 40 / 100)            # 60 t at 0, 40 t (not 50) at 100


def test_geometric_median():
    xy = np.array([[0, 0], [10, 0], [0, 10], [100, 100]], float)
    m = cluster.geometric_median(xy, np.ones(4))
    assert np.hypot(*(m - [3, 3])) < 5                      # robust to the outlier


# ---------------------------------------------------------------- network
def _net(lines, classes, **net):
    cfg = config.load_config(None)
    cfg["network"].update(net)
    roads = gpd.GeoDataFrame({"FEAT_TYPE": classes}, geometry=lines, crs=UTM)
    return RoadNetwork(roads, "FEAT_TYPE", cfg)


def test_routing_snaps_splits_and_restores_graph():
    net = _net([LineString([(0, 0), (1000, 0)]), LineString([(1000, 0), (1000, 1000)])],
               ["Street", "Main Road"])
    n_edges = net.G.number_of_edges()
    starts = np.array([[200, 30], [600, -40], [1010, 700]], float)
    res, fac = net.route_many(starts, (100, 5))
    assert fac == pytest.approx((100, 0))
    assert [r.status for r in res] == ["ok", "ok", "ok"]
    assert res[0].road_m == pytest.approx(100) and res[0].snap_m == pytest.approx(30)
    assert res[1].road_m == pytest.approx(500)
    assert res[2].length_by_class == pytest.approx({"Street": 900, "Main Road": 700})
    assert res[2].coords[0] == pytest.approx((1000, 700)) and res[2].coords[-1] == pytest.approx((100, 0))
    assert net.G.number_of_edges() == n_edges               # temporary nodes removed again


def test_unreachable_and_zero_distance_do_not_crash():
    net = _net([LineString([(0, 0), (100, 0)]), LineString([(5000, 0), (5100, 0)])], ["Street", "Street"])
    res, _ = net.route_many(np.array([[5050, 10], [50, 3]], float), (50, 0))
    assert res[0].status == "unreachable_fallback"
    assert res[0].length_by_class["Unconnected"] == pytest.approx(5000 * 1.3)
    assert res[1].status == "zero_distance" and res[1].road_m == 0


def test_class_weights_change_route_not_reported_length():
    # two parallel ways from A to B: short Street vs longer Main Road
    lines = [LineString([(0, 0), (1000, 0)]),
             LineString([(0, 0), (0, 200), (1000, 200), (1000, 0)])]
    net = _net(lines, ["Street", "Main Road"], class_weight_factors={"Street": 5.0})
    res, _ = net.route_many(np.array([[1000, -1]], float), (0, -1))
    assert set(res[0].length_by_class) == {"Main Road"}
    assert res[0].road_m == pytest.approx(1400)


def test_exclusions_remove_classes():
    net = _net([LineString([(0, 0), (100, 0)]), LineString([(100, 0), (200, 0)])],
               ["Street", "Dirt Road"], use_exclusions=True)
    assert net.classes == ["Street"]


# ---------------------------------------------------------------- access
def test_access_vector_prefers_nearer_vr_and_ties_go_to_road():
    cfg = config.load_config(None, ["access.method=vector"])
    net = _net([LineString([(0, 0), (1000, 0)])], ["Street"])
    vr = gpd.GeoDataFrame({"vr_id": [7, 8], "vr_length_m": [900.0, 500.0],
                           "end_x": [10.0, 20.0], "end_y": [0.0, 0.0]},
                          geometry=[Point(0, 900), Point(0, 2000)], crs=UTM)
    acc = AccessModel(cfg, gpd.GeoSeries([], crs=UTM).crs, vr, net).classify(
        np.array([[0, 950], [500, 10], [0, 450]], float))
    assert list(acc.kind) == ["vr", "road", "road"]           # 50 m to VR vs 950 m to road
    assert acc.vr_id[0] == 7 and acc.vr_m[0] == 900 and acc.pre_access_m[0] == pytest.approx(50)
    assert tuple(acc.route_xy[0]) == (10.0, 0.0)              # route starts at the VR end vertex


# ---------------------------------------------------------------- costs
def _points_frame(**over):
    row = dict(scenario_id="s", facility_id=1, feedstock="A", technology="CBP-Pelleting",
               capacity_t=24000.0, facility_lon=0, facility_lat=0, contributed_t=2600.0,
               road_m=10000.0, vr_m=0.0, access_m=0.0, area_ha=10.0, route_status="ok")
    row.update(over)
    return pd.DataFrame([row])


def test_haulage_is_cpk_times_km_not_times_trucks():
    cfg = config.load_config(None)
    curves = pd.read_csv(REPO / "parameters/Technology_cost_curves.csv")
    energy = pd.read_csv(REPO / "parameters/Biofuel energy densities.csv")
    out = costs.facility_costs(_points_frame(), cfg, curves, energy).iloc[0]
    cpk = (923352 + 1093536) / 100000
    km = 2600 / 26 * 2 * 10.0                                   # 100 loads, return trips, 10 km
    assert out.road_km_per_year == pytest.approx(km)
    assert out.road_haul_r_year1 == pytest.approx(cpk * km)
    assert out.cost_r_per_kwh == pytest.approx(out.cost_r_per_gj / 277.7778, rel=1e-6)
    assert out.time_horizon_years == 15                          # from the curve (lifespan)
    cfg2 = config.load_config(None, ["costs.time_horizon_years=20"])
    assert costs.facility_costs(_points_frame(), cfg2, curves, energy).iloc[0].time_horizon_years == 20


# ---------------------------------------------------------------- full example
@pytest.fixture(scope="module")
def example_run(tmp_path_factory):
    out = tmp_path_factory.mktemp("bea")
    cfg = config.load_config(REPO / "config.toml",
                             [f"paths.output_dir={json.dumps(out.as_posix())}", "paths.run_name=test"])
    return pipeline.run(cfg, base=REPO)


def test_example_complete_and_mass_balance(example_run):
    m = json.loads((example_run / "run_manifest.json").read_text())
    assert m["status"] == "complete" and m["results"]["duplicates_removed"] == 84
    total = m["results"]["feedstock_t"]
    assert total == pytest.approx(161124.3, abs=0.1)
    for sid, s in m["results"]["scenarios"].items():
        assert s["tonnes"] == pytest.approx(total, abs=0.01), sid
    fc = pd.read_csv(example_run / "facility_costs.csv")
    assert fc.cost_r_per_feedstock_t.between(500, 10000).all()
    caps = fc.groupby("capacity_t").capex_r_per_t.first()
    assert caps.is_monotonic_decreasing                             # economies of scale (D-06)


def test_rows_align_with_their_own_paths(example_run):
    """Regression for the 96% misalignment (T-B5): each path starts at its own point."""
    sdir = example_run / "scenarios" / "CBP-Pelleting_24000"
    paths = gpd.read_file(sdir / "spatial.gpkg", layer="paths")
    pts = gpd.read_file(sdir / "spatial.gpkg", layer="feedstock_points")
    crs = paths.estimate_utm_crs()                      # same working CRS as the model
    paths, pts = paths.to_crs(crs), pts.to_crs(crs)
    m = paths.merge(pd.DataFrame({"facility_id": pts.facility_id, "point_key": pts.point_key,
                                  "pt": pts.geometry}), on=["facility_id", "point_key"])
    road = m[m.access_type == "road"]
    start = gpd.GeoSeries([Point(g.coords[0]) for g in road.geometry], crs=crs)
    gap = start.distance(gpd.GeoSeries(road.pt.values, crs=crs)).to_numpy()
    assert np.allclose(gap, road.access_m.to_numpy(), atol=0.5)
    # a point may supply two facilities (split volume), but only once per facility
    assert not paths.duplicated(["facility_id", "point_key"]).any()


def test_outputs_have_string_ids_and_layers(example_run):
    sdir = example_run / "scenarios" / "CBP-Pelleting_48000"
    layers = set(gpd.list_layers(sdir / "spatial.gpkg").name)
    assert {"facilities", "paths", "feedstock_points", "catchments"} <= layers
    pts = gpd.read_file(sdir / "spatial.gpkg", layer="feedstock_points")
    assert pts.point_id.str.startswith("W").all()                  # was written as 0 (D-07)


def test_resume_skips_finished_scenarios(example_run):
    cfg = config.load_config(REPO / "config.toml", [
        f"paths.output_dir={json.dumps(example_run.parent.as_posix())}", "paths.run_name=test"])
    pipeline.run(cfg, base=REPO)
    log = (example_run / "run.log").read_text(encoding="utf8")
    assert log.count("skipped (resume)") == 3
