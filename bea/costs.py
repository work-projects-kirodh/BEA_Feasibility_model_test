"""Cost of production per facility (replaces Process_feasibility_functions.py).

Fixes (review IDs in bea_feasibility_upgrades.txt):
* AVE_LTC / pv were not finance formulas (D-04, D-05) -> capital recovery factor and the
  present value of an escalating annuity, with discount and escalation as separate rates;
* the cost curve was always point 1 (D-06, B4) -> log-linear interpolation between the
  three curve points (or nearest / first);
* road cost multiplied CPK x km by the number of trucks (B5a) -> CPK x km; fleet size is
  reported separately;
* the "annual" total added lifetime opex and full capex (B5b) -> equivalent annual cost;
* energy_generation_Rand_kwh was R/MJ (D-11) -> R/GJ and R/kWh with correct units;
* the user's time horizon was silently replaced (D-26) -> 0 = curve lifespan, else user's.
Assumption kept from the original: tonnes contributed are tonnes PER YEAR.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def crf(rate: float, years: float) -> float:
    """Capital recovery factor: equal yearly payment that repays 1 over `years`."""
    if years <= 0:
        raise ValueError("years must be > 0")
    if abs(rate) < 1e-12:
        return 1.0 / years
    f = (1 + rate) ** years
    return rate * f / (f - 1)


def pv_escalating(rate: float, growth: float, years: float) -> float:
    """Present value of a yearly cost of 1 (year 1), growing by `growth`, discounted at `rate`."""
    if abs(rate - growth) < 1e-12:
        return years / (1 + rate)
    return (1 - ((1 + growth) / (1 + rate)) ** years) / (rate - growth)


def curve_values(curve: pd.Series, capacity: float, method: str) -> tuple[float, float]:
    caps = np.array([curve[f"capacity{i}"] for i in (1, 2, 3)], dtype=float)
    capex = np.array([curve[f"capex{i}"] for i in (1, 2, 3)], dtype=float)
    opex = np.array([curve[f"opex{i}"] for i in (1, 2, 3)], dtype=float)
    order = np.argsort(caps)
    caps, capex, opex = caps[order], capex[order], opex[order]
    if method == "first":
        return float(curve["capex1"]), float(curve["opex1"])
    if method == "nearest":
        k = int(np.argmin(np.abs(np.log(caps) - math.log(capacity))))
        return float(capex[k]), float(opex[k])
    lc, lq = np.log(caps), math.log(capacity)            # loglinear, flat beyond the ends
    return float(np.interp(lq, lc, capex)), float(np.interp(lq, lc, opex))


def facility_costs(points: pd.DataFrame, cfg: dict, curves: pd.DataFrame,
                   energy: pd.DataFrame) -> pd.DataFrame:
    """One row per facility from the per-feedstock-point table written by the spatial step."""
    c = cfg["costs"]
    truck_cpk = (c["truck_standing_cost_r_per_year"] + c["truck_variable_cost_r_per_year"]) / c["truck_annual_km"]
    tractor_cpk = c["offroad_haulage_r_per_hour"] / c["offroad_speed_kmh"]
    road_km_per_truck = c["road_speed_kmh"] * c["hours_per_day"] * c["truck_working_days"]
    offroad_km_per_tractor = c["offroad_speed_kmh"] * c["hours_per_day"] * c["truck_working_days"]

    p = points.copy()
    total_m = p.road_m + p.vr_m + p.access_m
    p["road_m_eff"] = np.where((total_m < c["min_haul_distance_m"]),
                               p.road_m + (c["min_haul_distance_m"] - total_m), p.road_m)
    p["offroad_m"] = p.vr_m + (p.access_m if c["cost_access_as_offroad"] else 0.0)
    loads_road = p.contributed_t / c["road_payload_t"]
    loads_off = p.contributed_t / c["offroad_payload_t"]
    if c["round_up_trips"]:
        loads_road, loads_off = np.ceil(loads_road), np.ceil(loads_off)
    p["road_trips"] = 2 * loads_road                       # return trips
    p["offroad_trips"] = np.where(p.offroad_m > 0, 2 * loads_off, 0.0)
    p["road_km"] = p.road_trips * p.road_m_eff / 1000
    p["offroad_km"] = p.offroad_trips * p.offroad_m / 1000

    rows = []
    for (sid, fid), g in p.groupby(["scenario_id", "facility_id"], sort=False):
        first = g.iloc[0]
        tech = first.technology
        curve = curves[curves.ID.str.lower() == tech.lower()].iloc[0]
        prod = curve.Prod_id
        mj_per_kg = float(energy[energy.Prod_id.str.lower() == prod.lower()]["Energy_(MJ/kg)"].iloc[0])
        years = c["time_horizon_years"] or float(curve.lifespan_years)
        r, gr = c["discount_rate"], c["escalation_rate"]
        f_crf, f_pv = crf(r, years), pv_escalating(r, gr, years)

        tonnes = float(g.contributed_t.sum())
        capacity = float(first.capacity_t)
        capex_t, opex_t = curve_values(curve, capacity, c["cost_curve_method"])
        capex_total = capex_t * (tonnes if c["capex_basis"] == "throughput" else capacity)
        road_km, off_km = float(g.road_km.sum()), float(g.offroad_km.sum())
        yr = {
            "opex": opex_t * tonnes,
            "load": c["load_cost_r_per_t"] * tonnes,
            "unload": c["unload_cost_r_per_t"] * tonnes,
            "road_haul": truck_cpk * road_km,
            "offroad_haul": tractor_cpk * off_km,
            "harvest": c["harvest_cost_r_per_ha"] * float(g.area_ha.sum()),
        }
        operating_y1 = sum(yr.values())
        annual_equiv = capex_total * f_crf + operating_y1 * f_pv * f_crf
        product_t = tonnes * float(curve.Efficiency) / 100.0
        energy_gj = product_t * mj_per_kg                  # t * MJ/kg = GJ
        rows.append({
            "scenario_id": sid, "facility_id": fid, "feedstock": first.feedstock,
            "technology": tech, "capacity_t": capacity, "product": prod,
            "facility_lon": first.facility_lon, "facility_lat": first.facility_lat,
            "feedstock_points": len(g), "tonnes_per_year": tonnes,
            "percent_full": 100 * tonnes / capacity,
            "service_area_ha": float(g.area_ha.sum()),
            "time_horizon_years": years, "discount_rate": r, "escalation_rate": gr,
            "crf": f_crf, "pv_factor_operating": f_pv,
            "capex_r_per_t": capex_t, "opex_r_per_t": opex_t, "capex_total_r": capex_total,
            "road_km_per_year": road_km, "offroad_km_per_year": off_km,
            "road_trips_per_year": float(g.road_trips.sum()),
            "offroad_trips_per_year": float(g.offroad_trips.sum()),
            "road_trucks_needed": math.ceil(road_km / road_km_per_truck) if road_km > 0 else 0,
            "offroad_tractors_needed": math.ceil(off_km / offroad_km_per_tractor) if off_km > 0 else 0,
            "truck_cpk_r_per_km": truck_cpk, "tractor_cpk_r_per_km": tractor_cpk,
            **{f"{k}_r_year1": v for k, v in yr.items()},
            "operating_r_year1": operating_y1,
            "npv_total_cost_r": capex_total + operating_y1 * f_pv,
            "equivalent_annual_cost_r": annual_equiv,
            "cost_r_per_feedstock_t": annual_equiv / tonnes if tonnes else np.nan,
            "transport_r_per_feedstock_t": (yr["road_haul"] + yr["offroad_haul"]) / tonnes if tonnes else np.nan,
            "product_t_per_year": product_t,
            "energy_gj_per_year": energy_gj,
            "cost_r_per_product_t": annual_equiv / product_t if product_t else np.nan,
            "cost_r_per_gj": annual_equiv / energy_gj if energy_gj else np.nan,
            "cost_r_per_kwh": annual_equiv / (energy_gj * 277.7778) if energy_gj else np.nan,
            "routes_unreachable": int((g.route_status == "unreachable_fallback").sum()),
            "routes_zero_distance": int((g.route_status == "zero_distance").sum()),
        })
    return pd.DataFrame(rows)
