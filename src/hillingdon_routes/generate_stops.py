"""Synthetic collection-stop generator for Hillingdon.

Stops cluster around hand-coded ward centres with Gaussian noise. Ward weights
drive the share of stops in each cluster, so Hayes and Yiewsley dominate while
Harefield and Ickenham stay sparse. All data is synthetic, no PII.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (
    DEMAND_KG_MAX,
    DEMAND_KG_MIN,
    HILLINGDON_BBOX,
    SERVICE_MINUTES_MAX,
    SERVICE_MINUTES_MIN,
    WARD_SPREAD_DEGREES,
    WARDS,
)


def generate_stops(n_stops: int = 50, seed: int = 42) -> pd.DataFrame:
    """Return a dataframe of synthetic collection stops.

    Columns: stop_id, lat, lng, demand_kg, service_minutes, ward.
    """
    if n_stops <= 0:
        raise ValueError("n_stops must be positive")

    rng = np.random.default_rng(seed)

    ward_names = list(WARDS.keys())
    weights = np.array([WARDS[w]["weight"] for w in ward_names], dtype=float)
    weights = weights / weights.sum()

    ward_assignments = rng.choice(ward_names, size=n_stops, p=weights)

    rows = []
    for i, ward in enumerate(ward_assignments):
        centre = WARDS[ward]
        # Clip to the borough envelope so noise never drifts a stop outside it.
        lat = float(np.clip(
            rng.normal(centre["lat"], WARD_SPREAD_DEGREES),
            HILLINGDON_BBOX["south"], HILLINGDON_BBOX["north"],
        ))
        lng = float(np.clip(
            rng.normal(centre["lng"], WARD_SPREAD_DEGREES),
            HILLINGDON_BBOX["west"], HILLINGDON_BBOX["east"],
        ))
        rows.append({
            "stop_id": i,
            "lat": lat,
            "lng": lng,
            "demand_kg": int(rng.integers(DEMAND_KG_MIN, DEMAND_KG_MAX + 1)),
            "service_minutes": int(rng.integers(SERVICE_MINUTES_MIN, SERVICE_MINUTES_MAX + 1)),
            "ward": str(ward),
        })

    return pd.DataFrame(rows)
