"""Configuration for the BEA generalisable feasibility model.

All tunable values live in DEFAULTS. Where the original scripts hard-coded a value, that
value is the default here (noted as "original"). Override in three layers, later wins:
DEFAULTS <- TOML file (--config, default config.toml) <- --set section.key=value.
"""
from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, dict[str, Any]] = {
    "paths": {
        "feedstock": "inputs/KZN_resampled.shp",            # feedstock polygons (original)
        "roads": "inputs/BEA_roads1.shp",                   # road network (original)
        "vr_start_vertices": "data/start_vertices.shp",     # virtual-road start vertices
        "vr_end_vertices": "data/end_vertices.shp",         # virtual-road end vertices (on roads)
        "vr_proximity_raster": "data/VR_roads_100m_proximity.tif",
        "road_proximity_raster": "data/NGI_roads_100m_proximity.tif",
        "cost_curves": "parameters/Technology_cost_curves.csv",
        "product_energy": "parameters/Biofuel energy densities.csv",
        "output_dir": "results",
        "run_name": "",                                     # "" = timestamp
    },
    "scenario": {
        "feedstock_name": "Aliens",                         # original
        "technologies": ["CBP-Pelleting"],                  # original
        # Facility capacities (t/yr) run for every technology (original 24000, 48000, 84000).
        # Empty list = use the three capacities of each technology's cost curve.
        "capacities_t": [24000, 48000, 84000],
    },
    "fields": {
        # The original used attribute positions [1, 2, 14]; names are used now.
        "id_field": "NbalId",
        "area_field": "Area",                               # hectares
        "volume_field": "CSIR_T",                           # tonnes
        "road_class_field": "FEAT_TYPE",
        "vr_id_field": "fid2",
        "vr_length_field": "length",                        # metres
    },
    "data": {
        # Remove exact duplicate feedstock features (same id AND geometry). The example input
        # has 84 such copies, inflating total feedstock by 18.9%.
        "drop_duplicate_features": True,
        "min_volume_t": 0.0,                                # points with volume <= this are ignored
        "working_crs": "",                                  # "" = UTM zone estimated from the data
        "output_crs": "EPSG:4326",                          # CRS of written layers (original WGS84)
    },
    "clustering": {
        # "weighted_mean" (original) or "geometric_median" (minimises tonne-km to the facility)
        "facility_location": "weighted_mean",
        # Weights: "contributed" tonnes (fix) or "available" tonnes (original behaviour)
        "weight_by": "contributed",
        "remaining_tolerance_t": 0.001,                     # remaining volume below this is used up
    },
    "access": {
        # How a feedstock point decides between an existing road and a virtual road:
        # "raster" (original: 100 m proximity rasters, vector fallback outside them) or "vector"
        "method": "raster",
        "vr_candidates": 4,                                 # original: 4 nearest VR start vertices
    },
    "network": {
        "use_exclusions": False,                            # original passed False
        "excluded_classes": ["bad road", "Dirt Road"],      # original list
        # Multiply edge length by a factor per road class when choosing routes,
        # e.g. {"Street" = 1.2}. Distances reported are always true metres.
        "class_weight_factors": {},
        # If a feedstock point cannot reach the facility on the network, use the straight-line
        # distance times this factor (flagged in route_status) instead of crashing.
        "unreachable_tortuosity": 1.3,
        "node_precision_m": 0.001,
    },
    "costs": {
        "time_horizon_years": 0,            # 0 = lifespan from the cost curve (original behaviour)
        "discount_rate": 0.06,              # original used "inflation" 0.06 as the rate
        "escalation_rate": 0.06,            # yearly growth of operating costs (original inflation)
        "cost_curve_method": "loglinear",   # loglinear | nearest | first (original was always first)
        "capex_basis": "throughput",        # throughput (original: t contributed) | capacity
        "road_speed_kmh": 50.0,             # original
        "offroad_speed_kmh": 8.0,           # original
        "hours_per_day": 8.0,               # original
        "truck_working_days": 260,          # original
        "truck_annual_km": 100000.0,        # original
        "road_payload_t": 26.0,             # original
        "offroad_payload_t": 4.0,           # original
        "truck_standing_cost_r_per_year": 923352.0,    # original (fleetwatch 2019)
        "truck_variable_cost_r_per_year": 1093536.0,   # original
        "offroad_haulage_r_per_hour": 453.31,          # original (SASRI)
        "load_cost_r_per_t": 14.4,          # original
        "unload_cost_r_per_t": 12.32,       # original
        "harvest_cost_r_per_ha": 0.0,       # original (defined but never used before)
        "round_up_trips": False,            # original: fractional trips
        "cost_access_as_offroad": True,     # cost point->road / point->VR distances at tractor rate
        "min_haul_distance_m": 0.0,         # minimum one-way haul distance per feedstock point
    },
    "output": {
        "write_catchments": True,
    },
    "run": {
        "log_level": "INFO",
        "overwrite": False,                 # allow re-using an existing run folder
        "resume": True,                     # skip scenarios already finished in that run folder
    },
}

# Settings whose value is a free-form mapping (any keys allowed)
FREEFORM = {("network", "class_weight_factors")}


class ConfigError(ValueError):
    """Raised for unknown keys or values of the wrong type."""


def _parse_value(text: str) -> Any:
    try:
        return tomllib.loads(f"v = {text}")["v"]
    except tomllib.TOMLDecodeError:
        return text


def _coerce(default: Any, value: Any, name: str) -> Any:
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"true", "false"}:
            return value.lower() == "true"
        raise ConfigError(f"'{name}' must be true/false, got {value!r}")
    if isinstance(default, (int, float)):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value) if isinstance(default, float) else value
        raise ConfigError(f"'{name}' must be a number, got {value!r}")
    if isinstance(default, list):
        if isinstance(value, str):
            value = [v.strip() for v in value.split(",") if v.strip()]
        if not isinstance(value, list):
            raise ConfigError(f"'{name}' must be a list, got {value!r}")
        return value
    if isinstance(default, str):
        return str(value)
    return value


def _merge(base: dict, new: dict, where: str = "") -> None:
    for key, value in new.items():
        if key not in base:
            raise ConfigError(f"Unknown config key '{where}{key}'. Valid keys: {sorted(base)}")
        section = where.rstrip(".")
        if (section, key) in FREEFORM:
            if not isinstance(value, dict):
                raise ConfigError(f"'{where}{key}' must be a table, e.g. {{\"Street\" = 1.2}}")
            base[key] = {str(k): float(v) for k, v in value.items()}
        elif isinstance(base[key], dict):
            if not isinstance(value, dict):
                raise ConfigError(f"'{where}{key}' must be a section")
            _merge(base[key], value, f"{where}{key}.")
        else:
            base[key] = _coerce(base[key], value, f"{where}{key}")


def apply_override(cfg: dict, assignment: str) -> None:
    if "=" not in assignment:
        raise ConfigError(f"Override '{assignment}' must look like section.key=value")
    dotted, raw = assignment.split("=", 1)
    parts = dotted.strip().split(".")
    if len(parts) != 2:
        raise ConfigError(f"Override key '{dotted}' must be section.key")
    _merge(cfg, {parts[0]: {parts[1]: _parse_value(raw.strip())}})


def load_config(path: str | Path | None = None, overrides: list[str] | None = None) -> dict:
    cfg = copy.deepcopy(DEFAULTS)
    if path:
        with open(path, "rb") as fh:
            _merge(cfg, tomllib.load(fh))
    for item in overrides or []:
        apply_override(cfg, item)
    validate(cfg)
    return cfg


def validate(cfg: dict) -> None:
    c, cl, a = cfg["costs"], cfg["clustering"], cfg["access"]
    if cl["facility_location"] not in {"weighted_mean", "geometric_median"}:
        raise ConfigError("clustering.facility_location must be weighted_mean or geometric_median")
    if cl["weight_by"] not in {"contributed", "available"}:
        raise ConfigError("clustering.weight_by must be contributed or available")
    if a["method"] not in {"raster", "vector"}:
        raise ConfigError("access.method must be raster or vector")
    if c["cost_curve_method"] not in {"loglinear", "nearest", "first"}:
        raise ConfigError("costs.cost_curve_method must be loglinear, nearest or first")
    if c["capex_basis"] not in {"throughput", "capacity"}:
        raise ConfigError("costs.capex_basis must be throughput or capacity")
    if not cfg["scenario"]["technologies"]:
        raise ConfigError("scenario.technologies must list at least one technology")
    for cap in cfg["scenario"]["capacities_t"]:
        if not isinstance(cap, (int, float)) or cap <= 0:
            raise ConfigError("scenario.capacities_t must be positive numbers")
    for name in ("road_payload_t", "offroad_payload_t", "road_speed_kmh", "offroad_speed_kmh",
                 "truck_annual_km", "hours_per_day"):
        if c[name] <= 0:
            raise ConfigError(f"costs.{name} must be > 0")
    if a["vr_candidates"] < 1:
        raise ConfigError("access.vr_candidates must be >= 1")


def dumps(cfg: dict) -> str:
    return json.dumps(cfg, indent=2)
