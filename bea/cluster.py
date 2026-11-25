"""Facility location-allocation (greedy nearest-neighbour fill).

Same idea as the original generate_facilities(): take the point with the most remaining
volume, then fill the facility from it and its nearest neighbours until the capacity is
reached; repeat until all volume is used.

Fixes: distances in metres (original: degrees); points tracked by integer index
(original removed list items by VALUE -> wrong records with repeated volumes/ids); the
facility position is weighted by tonnes CONTRIBUTED (original: full available volume);
an optional geometric median minimises tonne-distance to the facility; volumes are not
rounded at every step (original rounded to 3 decimals, leaving residues).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Facility:
    facility_id: int
    x: float
    y: float
    members: list[int] = field(default_factory=list)          # indices into the points table
    original_t: list[float] = field(default_factory=list)     # remaining volume before
    contributed_t: list[float] = field(default_factory=list)
    still_needed_t: list[float] = field(default_factory=list) # capacity still open after
    remaining_t: list[float] = field(default_factory=list)    # volume left at the point after
    percent_full: list[float] = field(default_factory=list)   # cumulative fill after the point

    @property
    def tonnes(self) -> float:
        return float(sum(self.contributed_t))


def geometric_median(xy: np.ndarray, w: np.ndarray, iters: int = 200, tol: float = 1e-3) -> np.ndarray:
    """Weiszfeld's algorithm: point minimising sum(w_i * distance_i)."""
    p = np.average(xy, axis=0, weights=w)
    for _ in range(iters):
        d = np.hypot(*(xy - p).T)
        if (d < 1e-9).any():                 # sits on a data point
            return p
        wd = w / d
        new = (xy * wd[:, None]).sum(axis=0) / wd.sum()
        if np.hypot(*(new - p)) < tol:
            return new
        p = new
    return p


def allocate(xy: np.ndarray, volume: np.ndarray, capacity: float, location: str = "weighted_mean",
             weight_by: str = "contributed", tol: float = 1e-3) -> list[Facility]:
    remaining = volume.astype("float64").copy()
    facilities: list[Facility] = []
    while (remaining > tol).any():
        active = np.flatnonzero(remaining > tol)
        seed = active[np.argmax(remaining[active])]
        dist = np.hypot(*(xy[active] - xy[seed]).T)
        # nearest first; ties broken by larger remaining volume (as the original sort did)
        order = active[np.lexsort((-remaining[active], dist))]
        fac = Facility(len(facilities) + 1, np.nan, np.nan)
        need = capacity
        for k in order:
            before = remaining[k]
            take = min(before, need)
            need -= take
            remaining[k] = before - take
            if remaining[k] <= tol:
                remaining[k] = 0.0
            fac.members.append(int(k))
            fac.original_t.append(float(before))
            fac.contributed_t.append(float(take))
            fac.still_needed_t.append(float(max(need, 0.0)))
            fac.remaining_t.append(float(remaining[k]))
            fac.percent_full.append(float(100.0 * (capacity - need) / capacity))
            if need <= tol:
                break
        pts = xy[fac.members]
        w = np.array(fac.contributed_t if weight_by == "contributed" else fac.original_t)
        if w.sum() <= 0:
            w = np.ones(len(pts))
        loc = (geometric_median(pts, w) if location == "geometric_median"
               else np.average(pts, axis=0, weights=w))
        fac.x, fac.y = float(loc[0]), float(loc[1])
        facilities.append(fac)
    return facilities
