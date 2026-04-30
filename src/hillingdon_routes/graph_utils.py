"""Distance-matrix and road-graph utilities.

Two paths to a distance matrix:
1. Haversine: pure-Python great-circle distance, no network, always works.
2. OSMnx: real road geometry via NetworkX shortest paths on the cached
   Hillingdon drive network.

The unified entry point falls back to haversine with a visible warning if
OSMnx is unavailable, the download fails, or the road graph cannot resolve
nearest nodes for every stop.
"""

from __future__ import annotations

import math
import os
import pickle
import warnings
from typing import List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .config import (
    DEPOT_LAT,
    DEPOT_LNG,
    GRAPH_CACHE_PATH,
    HILLINGDON_BBOX,
    SCHOOL_PROXIMITY_METRES,
    SCHOOLS_CACHE_PATH,
)


def _haversine_metres(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    earth_radius_m = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return earth_radius_m * c


def _stops_with_depot(stops: pd.DataFrame) -> Tuple[np.ndarray, np.ndarray]:
    """Return lat/lng arrays with the depot prepended at index 0.

    OR-Tools expects the depot at a known matrix index. Index 0 = depot,
    index i+1 = stops.iloc[i].
    """
    lats = np.concatenate([[DEPOT_LAT], stops["lat"].to_numpy()])
    lngs = np.concatenate([[DEPOT_LNG], stops["lng"].to_numpy()])
    return lats, lngs


def haversine_matrix(stops: pd.DataFrame) -> np.ndarray:
    """Square symmetric distance matrix in metres.

    Always works, no network required. Used as the offline-safe fallback.
    """
    lats, lngs = _stops_with_depot(stops)
    n = len(lats)
    matrix = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(i + 1, n):
            d = _haversine_metres(lats[i], lngs[i], lats[j], lngs[j])
            matrix[i, j] = d
            matrix[j, i] = d
    return matrix


def load_or_build_graph(cache_path: str = GRAPH_CACHE_PATH):
    """Return the Hillingdon drive network, downloading and caching on first call.

    Returns a NetworkX MultiDiGraph, or None if OSMnx is unavailable or the
    download fails. Subsequent calls load the pickle from disk.
    """
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            warnings.warn(f"Failed to load cached graph at {cache_path}: {exc}. Rebuilding.")

    try:
        import osmnx as ox
    except ImportError:
        warnings.warn("OSMnx not installed. Distance matrix will use haversine fallback.")
        return None

    try:
        graph = ox.graph_from_bbox(
            north=HILLINGDON_BBOX["north"],
            south=HILLINGDON_BBOX["south"],
            east=HILLINGDON_BBOX["east"],
            west=HILLINGDON_BBOX["west"],
            network_type="drive",
        )
    except Exception as exc:
        warnings.warn(f"OSMnx download failed: {exc}. Falling back to haversine.")
        return None

    try:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump(graph, f)
    except Exception as exc:
        warnings.warn(f"Could not write graph cache to {cache_path}: {exc}.")

    return graph


def osmnx_matrix(stops: pd.DataFrame, graph) -> Optional[np.ndarray]:
    """Distance matrix in metres via shortest paths on the road graph.

    Returns None if the graph is missing or any lookup fails. Disconnected
    pairs fall back to haversine for that pair only so a single bad node
    cannot collapse the whole matrix.
    """
    if graph is None:
        return None

    try:
        import osmnx as ox
        import networkx as nx
    except ImportError:
        return None

    lats, lngs = _stops_with_depot(stops)

    try:
        nodes = ox.distance.nearest_nodes(graph, lngs.tolist(), lats.tolist())
    except Exception as exc:
        warnings.warn(f"OSMnx nearest-node lookup failed: {exc}.")
        return None

    n = len(lats)
    matrix = np.zeros((n, n), dtype=float)
    for i in range(n):
        try:
            lengths = nx.single_source_dijkstra_path_length(
                graph, nodes[i], weight="length",
            )
        except Exception as exc:
            warnings.warn(f"Shortest-path lookup failed at index {i}: {exc}.")
            return None
        for j in range(n):
            if i == j:
                continue
            d = lengths.get(nodes[j])
            if d is None:
                d = _haversine_metres(lats[i], lngs[i], lats[j], lngs[j])
            matrix[i, j] = float(d)
    return matrix


def load_or_fetch_schools(
    cache_path: str = SCHOOLS_CACHE_PATH,
) -> Optional[List[Tuple[float, float]]]:
    """Return school points (lat, lng) inside Hillingdon, downloading once.

    Uses OSM amenity=school within the borough bbox. Returns None if OSMnx
    is unavailable or the lookup fails, an empty list if no schools found,
    or a list of (lat, lng) centroid points otherwise.
    """
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as f:
                return pickle.load(f)
        except Exception as exc:
            warnings.warn(f"Failed to load cached schools at {cache_path}: {exc}.")

    try:
        import osmnx as ox
    except ImportError:
        warnings.warn("OSMnx not installed. School tagging disabled.")
        return None

    try:
        gdf = ox.features_from_bbox(
            north=HILLINGDON_BBOX["north"],
            south=HILLINGDON_BBOX["south"],
            east=HILLINGDON_BBOX["east"],
            west=HILLINGDON_BBOX["west"],
            tags={"amenity": "school"},
        )
    except Exception as exc:
        warnings.warn(f"Failed to fetch schools from OSM: {exc}.")
        return None

    if gdf is None or len(gdf) == 0:
        points: List[Tuple[float, float]] = []
    else:
        # Schools come back as Points or Polygons. Centroid covers both.
        points = []
        for geom in gdf.geometry:
            if geom is None or geom.is_empty:
                continue
            c = geom.centroid
            points.append((float(c.y), float(c.x)))

    try:
        os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
        with open(cache_path, "wb") as f:
            pickle.dump(points, f)
    except Exception as exc:
        warnings.warn(f"Could not write schools cache to {cache_path}: {exc}.")

    return points


def tag_school_adjacent_stops(
    stops: pd.DataFrame,
    schools: Optional[List[Tuple[float, float]]],
    proximity_metres: int = SCHOOL_PROXIMITY_METRES,
) -> Set[int]:
    """Return the set of stop_ids within proximity_metres of any school point."""
    if not schools:
        return set()
    flagged: Set[int] = set()
    for _, row in stops.iterrows():
        for s_lat, s_lng in schools:
            if _haversine_metres(row["lat"], row["lng"], s_lat, s_lng) <= proximity_metres:
                flagged.add(int(row["stop_id"]))
                break
    return flagged


def build_distance_matrix(
    stops: pd.DataFrame,
    use_osmnx: bool = False,
    graph=None,
) -> Tuple[np.ndarray, str]:
    """Unified entry point. Returns (matrix, source).

    source is either 'osmnx' or 'haversine'. If use_osmnx is True but OSMnx
    fails for any reason, falls back to haversine and emits a warning so the
    UI can show that the road graph was not used. Pass an already-loaded
    graph to skip the disk pickle load.
    """
    if not use_osmnx:
        return haversine_matrix(stops), "haversine"

    if graph is None:
        graph = load_or_build_graph()
    matrix = osmnx_matrix(stops, graph) if graph is not None else None
    if matrix is None:
        warnings.warn("Falling back to haversine distance matrix.")
        return haversine_matrix(stops), "haversine"
    return matrix, "osmnx"
