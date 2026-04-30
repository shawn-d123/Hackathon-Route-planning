"""Road closure and truck breakdown helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from .config import (
    BREAKDOWN_REASSIGN_TIME_LIMIT_SECONDS,
    CO2_GRAMS_PER_KM,
    DEFAULT_DEPARTURE_MINUTES,
    DEFAULT_VEHICLE_CAPACITY_KG,
    METRES_PER_KM,
    NO_CLOSURE_LABEL,
    ROAD_CLOSURE_EDGE_COUNT,
    ROAD_CLOSURE_SCENARIOS,
    SOLVER_TIME_LIMIT_SECONDS,
    TIP_THRESHOLD,
)
from .solver import TipEvent, VrpSolution, solve_vrp, solve_zoned_vrp


@dataclass
class ClosureResult:
    scenario_name: str
    graph: Any
    closed_edges: List[Dict[str, Any]]
    warning: Optional[str] = None


@dataclass
class BreakdownPlan:
    broken_truck: int
    breakdown_after: int
    completed_stops: List[int]
    unfinished_stops: List[int]
    active_trucks: List[int]
    updated_routes: List[List[int]]
    reassigned_by_vehicle: Dict[int, List[int]]
    recovery_solution: Optional[VrpSolution]
    breakdown_location: Tuple[float, float]
    warning: Optional[str] = None


def _edge_coordinates(graph: Any, u: int, v: int, key: int) -> List[Tuple[float, float]]:
    """Return display coordinates for a graph edge."""
    edge_data = graph.get_edge_data(u, v, key) or {}
    geometry = edge_data.get("geometry")
    if geometry is not None:
        return [(float(lat), float(lng)) for lng, lat in geometry.coords]
    return [
        (float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])),
        (float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"])),
    ]


def get_closure_edges_for_scenario(
    graph: Any,
    scenario_name: str,
) -> List[Dict[str, Any]]:
    """Pick deterministic nearby edges for a named closure scenario."""
    scenario = ROAD_CLOSURE_SCENARIOS.get(scenario_name)
    if graph is None or scenario is None:
        return []

    try:
        import osmnx as ox

        centre_node = ox.distance.nearest_nodes(graph, scenario["lng"], scenario["lat"])
        candidates: List[Tuple[float, int, int, int]] = []
        for u, v, key, data in graph.out_edges(centre_node, keys=True, data=True):
            candidates.append((float(data.get("length", 0.0)), int(u), int(v), int(key)))
        for u, v, key, data in graph.in_edges(centre_node, keys=True, data=True):
            candidates.append((float(data.get("length", 0.0)), int(u), int(v), int(key)))
        candidates = sorted(candidates, key=lambda item: item[0])
        if not candidates:
            u, v, key = ox.distance.nearest_edges(graph, scenario["lng"], scenario["lat"])
            candidates = [(0.0, int(u), int(v), int(key))]

        seen = set()
        edges: List[Dict[str, Any]] = []
        for _, u, v, key in candidates:
            marker = (u, v, key)
            if marker in seen or not graph.has_edge(u, v, key):
                continue
            seen.add(marker)
            edge_data = graph.get_edge_data(u, v, key) or {}
            coords = _edge_coordinates(graph, u, v, key)
            edges.append({
                "u": u,
                "v": v,
                "key": key,
                "name": edge_data.get("name", scenario_name),
                "coords": coords,
                "lat": coords[len(coords) // 2][0],
                "lng": coords[len(coords) // 2][1],
            })
            if len(edges) >= ROAD_CLOSURE_EDGE_COUNT:
                break
        return edges
    except Exception:
        return []


def apply_closure_scenario(graph: Any, scenario_name: str) -> ClosureResult:
    """Return a graph copy with the selected closure edges removed."""
    if graph is None or scenario_name == NO_CLOSURE_LABEL:
        return ClosureResult(scenario_name=scenario_name, graph=graph, closed_edges=[])

    graph_copy = graph.copy()
    edges = get_closure_edges_for_scenario(graph_copy, scenario_name)
    if not edges:
        return ClosureResult(
            scenario_name=scenario_name,
            graph=graph_copy,
            closed_edges=[],
            warning="Could not find a matching road segment for this closure.",
        )

    removed: List[Dict[str, Any]] = []
    for edge in edges:
        u = edge["u"]
        v = edge["v"]
        key = edge["key"]
        if graph_copy.has_edge(u, v, key):
            graph_copy.remove_edge(u, v, key)
            removed.append(edge)

    return ClosureResult(scenario_name=scenario_name, graph=graph_copy, closed_edges=removed)


def get_active_graph(base_graph: Any, closure_scenario: str) -> ClosureResult:
    """Build the route graph for the selected closure state."""
    return apply_closure_scenario(base_graph, closure_scenario)


def extract_completed_and_unfinished_stops(
    route: List[int],
    breakdown_after_stop_index: int,
) -> Tuple[List[int], List[int]]:
    """Split a route into completed and unfinished stops."""
    split_at = max(0, min(int(breakdown_after_stop_index), len(route)))
    return route[:split_at], route[split_at:]


def _subset_matrix(matrix: np.ndarray, stop_ids: List[int]) -> np.ndarray:
    """Return depot-first matrix for selected global stop IDs."""
    indices = [0] + [sid + 1 for sid in stop_ids]
    return matrix[np.ix_(indices, indices)]


def _remap_solution_routes(
    solution: VrpSolution,
    original_ids: List[int],
    active_trucks: List[int],
) -> Tuple[List[List[int]], List[TipEvent]]:
    """Map a local recovery solution back to original stop and truck IDs."""
    routes: List[List[int]] = []
    for route in solution.routes:
        routes.append([original_ids[local_id] for local_id in route])

    tips: List[TipEvent] = []
    for tip in solution.tip_events:
        after_stop_id = None
        if tip.after_stop_id is not None and tip.after_stop_id < len(original_ids):
            after_stop_id = original_ids[tip.after_stop_id]
        vehicle = active_trucks[tip.vehicle] if tip.vehicle < len(active_trucks) else tip.vehicle
        tips.append(TipEvent(
            vehicle=vehicle,
            after_stop_id=after_stop_id,
            cumulative_km=tip.cumulative_km,
            load_kg_before_tip=tip.load_kg_before_tip,
            clock_minutes=tip.clock_minutes,
        ))
    return routes, tips


def reoptimise_remaining_stops(
    stops: pd.DataFrame,
    matrix: np.ndarray,
    unfinished_stop_ids: List[int],
    active_trucks: List[int],
    vehicle_capacity_kg: int,
    tip_threshold: float,
    matrix_source: str,
    departure_minutes: int,
    apply_peak_hours: bool,
    apply_school_windows: bool,
    school_adjacent_stop_ids: Set[int],
) -> Optional[VrpSolution]:
    """Re-optimise unfinished stops across the trucks still in service."""
    if not unfinished_stop_ids or not active_trucks:
        return None

    recovery_stops = stops.loc[stops["stop_id"].isin(unfinished_stop_ids)].copy()
    recovery_stops = recovery_stops.sort_values("stop_id").reset_index(drop=True)
    original_ids = recovery_stops["stop_id"].astype(int).tolist()
    recovery_stops["stop_id"] = range(len(recovery_stops))
    recovery_matrix = _subset_matrix(matrix, original_ids)
    local_school_ids = {
        idx for idx, original_id in enumerate(original_ids)
        if original_id in school_adjacent_stop_ids
    }

    try:
        local_solution = solve_zoned_vrp(
            stops=recovery_stops,
            matrix=recovery_matrix,
            num_vehicles=len(active_trucks),
            vehicle_capacity_kg=vehicle_capacity_kg,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            time_limit_seconds=BREAKDOWN_REASSIGN_TIME_LIMIT_SECONDS,
            departure_minutes=departure_minutes,
            apply_peak_hours=apply_peak_hours,
            apply_school_windows=apply_school_windows,
            school_adjacent_stop_ids=local_school_ids,
        )
    except RuntimeError:
        local_solution = solve_vrp(
            stops=recovery_stops,
            matrix=recovery_matrix,
            num_vehicles=len(active_trucks),
            vehicle_capacity_kg=vehicle_capacity_kg,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            time_limit_seconds=SOLVER_TIME_LIMIT_SECONDS,
            departure_minutes=departure_minutes,
            apply_peak_hours=apply_peak_hours,
            apply_school_windows=apply_school_windows,
            school_adjacent_stop_ids=local_school_ids,
        )

    mapped_routes, mapped_tips = _remap_solution_routes(local_solution, original_ids, active_trucks)
    total_distance_m = float(sum(local_solution.distances_m))
    return VrpSolution(
        routes=mapped_routes,
        routes_with_depot=local_solution.routes_with_depot,
        arrivals_minutes=local_solution.arrivals_minutes,
        tip_events=mapped_tips,
        distances_m=local_solution.distances_m,
        durations_minutes=local_solution.durations_minutes,
        finish_clock_minutes=local_solution.finish_clock_minutes,
        total_distance_m=total_distance_m,
        total_co2_g=total_distance_m / METRES_PER_KM * CO2_GRAMS_PER_KM,
        loads_kg=local_solution.loads_kg,
        school_violations=local_solution.school_violations,
        peak_multiplier=local_solution.peak_multiplier,
        departure_minutes=local_solution.departure_minutes,
        source=matrix_source,
    )


def build_updated_routes_after_breakdown(
    original_routes: List[List[int]],
    broken_truck_id: int,
    completed_stops: List[int],
    active_trucks: List[int],
    recovery_solution: Optional[VrpSolution],
) -> Tuple[List[List[int]], Dict[int, List[int]]]:
    """Merge completed work and recovery routes into a final plan."""
    updated = [list(route) for route in original_routes]
    updated[broken_truck_id] = completed_stops
    reassigned: Dict[int, List[int]] = {truck: [] for truck in range(len(original_routes))}
    if recovery_solution is None:
        return updated, reassigned

    for local_idx, route in enumerate(recovery_solution.routes):
        if local_idx >= len(active_trucks):
            continue
        truck = active_trucks[local_idx]
        updated[truck].extend(route)
        reassigned[truck] = list(route)
    return updated, reassigned


def _breakdown_location(
    stops: pd.DataFrame,
    completed_stops: List[int],
) -> Tuple[float, float]:
    """Return the last completed stop location, or the depot if none."""
    if not completed_stops:
        from .config import DEPOT_LAT, DEPOT_LNG

        return DEPOT_LAT, DEPOT_LNG
    row = stops.loc[stops["stop_id"] == completed_stops[-1]].iloc[0]
    return float(row["lat"]), float(row["lng"])


def simulate_truck_breakdown(
    stops: pd.DataFrame,
    matrix: np.ndarray,
    solution: VrpSolution,
    broken_truck_id: int,
    breakdown_after_stop_index: int,
    vehicle_capacity_kg: int = DEFAULT_VEHICLE_CAPACITY_KG,
    tip_threshold: float = TIP_THRESHOLD,
    matrix_source: str = "haversine",
    departure_minutes: int = DEFAULT_DEPARTURE_MINUTES,
    apply_peak_hours: bool = False,
    apply_school_windows: bool = False,
    school_adjacent_stop_ids: Optional[Set[int]] = None,
) -> BreakdownPlan:
    """Reassign unfinished work from a broken truck to remaining trucks."""
    school_adjacent_stop_ids = school_adjacent_stop_ids or set()
    if broken_truck_id < 0 or broken_truck_id >= len(solution.routes):
        raise ValueError("broken_truck_id is outside the available route range")

    original_route = solution.routes[broken_truck_id]
    completed, unfinished = extract_completed_and_unfinished_stops(
        original_route,
        breakdown_after_stop_index,
    )
    active_trucks = [truck for truck in range(len(solution.routes)) if truck != broken_truck_id]
    warning = None
    recovery_solution = None
    if unfinished and active_trucks:
        recovery_solution = reoptimise_remaining_stops(
            stops=stops,
            matrix=matrix,
            unfinished_stop_ids=unfinished,
            active_trucks=active_trucks,
            vehicle_capacity_kg=vehicle_capacity_kg,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            departure_minutes=departure_minutes,
            apply_peak_hours=apply_peak_hours,
            apply_school_windows=apply_school_windows,
            school_adjacent_stop_ids=school_adjacent_stop_ids,
        )
    elif unfinished:
        warning = "No active trucks remain to absorb unfinished stops."

    updated_routes, reassigned = build_updated_routes_after_breakdown(
        original_routes=solution.routes,
        broken_truck_id=broken_truck_id,
        completed_stops=completed,
        active_trucks=active_trucks,
        recovery_solution=recovery_solution,
    )

    return BreakdownPlan(
        broken_truck=broken_truck_id,
        breakdown_after=breakdown_after_stop_index,
        completed_stops=completed,
        unfinished_stops=unfinished,
        active_trucks=active_trucks,
        updated_routes=updated_routes,
        reassigned_by_vehicle=reassigned,
        recovery_solution=recovery_solution,
        breakdown_location=_breakdown_location(stops, completed),
        warning=warning,
    )
