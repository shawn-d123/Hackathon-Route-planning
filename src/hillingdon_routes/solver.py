"""VRP solver for Hillingdon waste collection.

Two entry points:

- naive_route_distance: single-vehicle nearest-neighbour tour from the depot,
  used as the "before" baseline for the comparison strip.
- solve_vrp: multi-vehicle OR-Tools VRP with a capacity dimension and
  intermediate facility visits (tipping back at the depot when the load
  reaches the tip threshold), a Time dimension that enforces the 8-hour
  shift cap (with the 30-minute lunch absorbed into the budget), optional
  peak-hour travel-time inflation, and optional soft school-window penalties
  for stops within 100 m of a school.

The intermediate-facility behaviour is implemented with the OR-Tools
"reload node" pattern: the depot is duplicated as a set of optional reload
nodes. A vehicle can enter a reload node mid-tour, which resets its load via
a slack-bounded capacity dimension. Spare reload copies stay unused at zero
cost via free disjunctions.

Peak hours use a global travel-time multiplier computed from the overlap
between the working day and the peak windows. This is an approximation:
OR-Tools does not natively support time-of-day-dependent travel costs, so
we inflate the matrix uniformly by the average peak factor for the day.

School soft windows are encoded as a soft upper bound on the Time-dimension
cumul at school-adjacent stops, biasing the solver to serve them before
08:30. Visits during the windows are also counted post-solve and surfaced
as a violation metric for the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import numpy as np
import pandas as pd
from ortools.constraint_solver import pywrapcp, routing_enums_pb2
from sklearn.cluster import KMeans

from .config import (
    AVERAGE_SPEED_KMH,
    CLOCK_DAY_MINUTES,
    CO2_GRAMS_PER_KM,
    DEFAULT_DEPARTURE_MINUTES,
    DEFAULT_VEHICLE_CAPACITY_KG,
    DISTANCE_SPAN_COST_COEFFICIENT,
    LUNCH_MINUTES,
    MAX_DISTANCE_METRES,
    METRES_PER_KM,
    PEAK_HOUR_MULTIPLIER,
    PEAK_HOUR_WINDOWS,
    SCHOOL_PENALTY_PER_MINUTE,
    SCHOOL_WINDOWS,
    SHIFT_MINUTES,
    SOLVER_TIME_LIMIT_SECONDS,
    TIP_THRESHOLD,
    ZONED_SOLVER_TIME_LIMIT_SECONDS,
)


@dataclass
class TipEvent:
    vehicle: int
    after_stop_id: Optional[int]
    cumulative_km: float
    load_kg_before_tip: int
    clock_minutes: int


@dataclass
class VrpSolution:
    routes: List[List[int]]                    # per vehicle: ordered stop_ids
    routes_with_depot: List[List[int]]         # per vehicle: matrix indices, depot first/last
    arrivals_minutes: List[List[int]]          # per vehicle: clock-minute arrival per node in routes_with_depot
    tip_events: List[TipEvent]
    distances_m: List[float]
    durations_minutes: List[int]               # per vehicle: end - start cumul on Time dim
    finish_clock_minutes: List[int]            # per vehicle: clock time at last depot return
    total_distance_m: float
    total_co2_g: float
    loads_kg: List[int]
    school_violations: int
    peak_multiplier: float
    departure_minutes: int
    source: str


def naive_route_distance(stops: pd.DataFrame, matrix: np.ndarray) -> Tuple[float, List[int]]:
    """Single-vehicle nearest-neighbour tour starting and ending at the depot."""
    n = matrix.shape[0]
    unvisited = set(range(1, n))
    order = [0]
    current = 0
    total = 0.0
    while unvisited:
        nxt = min(unvisited, key=lambda j: matrix[current, j])
        total += float(matrix[current, nxt])
        order.append(nxt)
        unvisited.remove(nxt)
        current = nxt
    total += float(matrix[current, 0])
    order.append(0)
    return total, order


def _peak_overlap_minutes(start_min: int, end_min: int) -> int:
    """Total minutes of [start_min, end_min] that fall inside any peak window."""
    total = 0
    for ws, we in PEAK_HOUR_WINDOWS:
        total += max(0, min(end_min, we) - max(start_min, ws))
    return total


def _compute_peak_multiplier(departure_minutes: int) -> float:
    """Average travel-time inflation across the working day.

    If half the day overlaps peak windows, returns 1 + 0.5 * (1.4 - 1) = 1.2.
    Outside any peak window the multiplier is 1.0.
    """
    end_min = departure_minutes + SHIFT_MINUTES - LUNCH_MINUTES
    overlap = _peak_overlap_minutes(departure_minutes, end_min)
    span = max(1, end_min - departure_minutes)
    fraction = overlap / span
    return 1.0 + fraction * (PEAK_HOUR_MULTIPLIER - 1.0)


def _build_reload_matrix(matrix: np.ndarray, n_reloads: int) -> np.ndarray:
    """Extend the distance matrix with reload copies of the depot."""
    n = matrix.shape[0]
    size = n + n_reloads
    extended = np.zeros((size, size), dtype=float)
    extended[:n, :n] = matrix
    for r in range(n_reloads):
        rid = n + r
        extended[rid, :n] = matrix[0, :n]
        extended[:n, rid] = matrix[:n, 0]
        for r2 in range(n_reloads):
            extended[rid, n + r2] = 0.0
    return extended


def _travel_minutes(distance_m: float, peak_mult: float) -> float:
    """Convert metres to minutes at the configured average speed, inflated by peak."""
    km = distance_m / METRES_PER_KM
    return (km / AVERAGE_SPEED_KMH) * 60.0 * peak_mult


def _is_in_window(t: int, windows) -> bool:
    return any(ws <= t < we for ws, we in windows)


def solve_vrp(
    stops: pd.DataFrame,
    matrix: np.ndarray,
    num_vehicles: int,
    vehicle_capacity_kg: int = DEFAULT_VEHICLE_CAPACITY_KG,
    tip_threshold: float = TIP_THRESHOLD,
    matrix_source: str = "haversine",
    time_limit_seconds: int = SOLVER_TIME_LIMIT_SECONDS,
    departure_minutes: int = DEFAULT_DEPARTURE_MINUTES,
    apply_peak_hours: bool = False,
    apply_school_windows: bool = False,
    school_adjacent_stop_ids: Optional[Set[int]] = None,
) -> VrpSolution:
    """Solve the multi-vehicle VRP with capacity, time, and optional constraints.

    Index 0 of `matrix` must be the depot, index i+1 the i-th stop in `stops`.
    """
    if num_vehicles < 1:
        raise ValueError("num_vehicles must be at least 1")

    n_real = matrix.shape[0]
    if n_real != len(stops) + 1:
        raise ValueError("matrix size must equal len(stops) + 1 (depot at index 0)")

    school_adjacent_stop_ids = school_adjacent_stop_ids or set()

    demands_real = [0] + stops["demand_kg"].astype(int).tolist()
    service_minutes_real = [0] + stops["service_minutes"].astype(int).tolist()
    total_demand = sum(demands_real)

    effective_cap = max(1, int(round(vehicle_capacity_kg * tip_threshold)))
    expected_tips_total = max(1, int(np.ceil(total_demand / effective_cap)))
    n_reloads = max(2 * num_vehicles, expected_tips_total + num_vehicles)

    extended_matrix = _build_reload_matrix(matrix, n_reloads)
    size = extended_matrix.shape[0]
    demands = demands_real + [0] * n_reloads
    service_minutes = service_minutes_real + [0] * n_reloads
    reload_indices = list(range(n_real, size))
    reload_set = set(reload_indices)

    peak_mult = _compute_peak_multiplier(departure_minutes) if apply_peak_hours else 1.0

    manager = pywrapcp.RoutingIndexManager(size, num_vehicles, 0)
    routing = pywrapcp.RoutingModel(manager)

    def distance_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        return int(round(extended_matrix[i, j]))

    transit_cb_idx = routing.RegisterTransitCallback(distance_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_idx)

    # Capacity dimension with full-capacity slack so reload visits reset the load.
    def demand_cb(from_index: int) -> int:
        i = manager.IndexToNode(from_index)
        return demands[i]

    demand_cb_idx = routing.RegisterUnaryTransitCallback(demand_cb)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_idx,
        effective_cap,
        [effective_cap] * num_vehicles,
        True,
        "Capacity",
    )
    capacity_dim = routing.GetDimensionOrDie("Capacity")

    for rid in reload_indices:
        ridx = manager.NodeToIndex(rid)
        routing.AddDisjunction([ridx], 0)
        capacity_dim.SlackVar(ridx).SetRange(0, effective_cap)

    # Time dimension (clock-time minutes from midnight).
    def time_cb(from_index: int, to_index: int) -> int:
        i = manager.IndexToNode(from_index)
        j = manager.IndexToNode(to_index)
        travel = _travel_minutes(extended_matrix[i, j], peak_mult)
        return int(round(service_minutes[i] + travel))

    time_cb_idx = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        time_cb_idx,
        SHIFT_MINUTES,                         # slack: allow waiting up to a shift
        CLOCK_DAY_MINUTES,                     # max cumul value (clock time domain)
        False,                                 # don't fix start cumul to zero
        "Time",
    )
    time_dim = routing.GetDimensionOrDie("Time")

    # Effective shift budget in minutes after subtracting the lunch break.
    duration_cap = SHIFT_MINUTES - LUNCH_MINUTES
    for v in range(num_vehicles):
        start = routing.Start(v)
        time_dim.CumulVar(start).SetRange(departure_minutes, departure_minutes)
        time_dim.SetSpanUpperBoundForVehicle(duration_cap, v)

    # School soft windows: penalise arriving after 08:30 at school-adjacent stops.
    if apply_school_windows and school_adjacent_stop_ids:
        morning_window_start = SCHOOL_WINDOWS[0][0]   # 08:30
        for stop_id in school_adjacent_stop_ids:
            node = stop_id + 1
            if node >= n_real:
                continue
            sidx = manager.NodeToIndex(node)
            time_dim.SetCumulVarSoftUpperBound(
                sidx, morning_window_start, SCHOOL_PENALTY_PER_MINUTE,
            )

    # Soft load balancing on distance.
    routing.AddDimension(transit_cb_idx, 0, MAX_DISTANCE_METRES, True, "Distance")
    distance_dim = routing.GetDimensionOrDie("Distance")
    distance_dim.SetGlobalSpanCostCoefficient(DISTANCE_SPAN_COST_COEFFICIENT)

    search_params = pywrapcp.DefaultRoutingSearchParameters()
    search_params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_params.time_limit.FromSeconds(int(time_limit_seconds))

    assignment = routing.SolveWithParameters(search_params)
    if assignment is None:
        raise RuntimeError(
            "OR-Tools could not find a feasible solution. Try fewer stops, "
            "more vehicles, or relax the shift cap."
        )

    routes: List[List[int]] = []
    routes_with_depot: List[List[int]] = []
    arrivals_minutes: List[List[int]] = []
    distances_m: List[float] = []
    durations_minutes: List[int] = []
    finish_clock_minutes: List[int] = []
    loads_kg: List[int] = []
    tip_events: List[TipEvent] = []
    school_violations = 0

    for v in range(num_vehicles):
        index = routing.Start(v)
        seq_matrix: List[int] = []
        seq_real_stops: List[int] = []
        seq_arrivals: List[int] = []
        running_distance = 0.0
        running_load = 0
        last_real_stop_id: Optional[int] = None

        prev_node = manager.IndexToNode(index)
        seq_matrix.append(prev_node)
        seq_arrivals.append(int(assignment.Value(time_dim.CumulVar(index))))

        while not routing.IsEnd(index):
            next_index = assignment.Value(routing.NextVar(index))
            next_node = manager.IndexToNode(next_index)
            running_distance += float(extended_matrix[prev_node, next_node])
            arrival_clock = int(assignment.Value(time_dim.CumulVar(next_index)))

            if next_node in reload_set:
                tip_events.append(TipEvent(
                    vehicle=v,
                    after_stop_id=last_real_stop_id,
                    cumulative_km=running_distance / METRES_PER_KM,
                    load_kg_before_tip=running_load,
                    clock_minutes=arrival_clock,
                ))
                running_load = 0
                seq_matrix.append(0)
            elif next_node == 0:
                seq_matrix.append(0)
            else:
                stop_id = next_node - 1
                seq_matrix.append(next_node)
                seq_real_stops.append(stop_id)
                last_real_stop_id = stop_id
                running_load += demands[next_node]
                if stop_id in school_adjacent_stop_ids:
                    if (_is_in_window(arrival_clock, SCHOOL_WINDOWS)
                            or _is_in_window(arrival_clock + service_minutes[next_node], SCHOOL_WINDOWS)):
                        school_violations += 1

            seq_arrivals.append(arrival_clock)
            prev_node = next_node
            index = next_index

        end_clock = seq_arrivals[-1] if seq_arrivals else departure_minutes
        routes.append(seq_real_stops)
        routes_with_depot.append(seq_matrix)
        arrivals_minutes.append(seq_arrivals)
        distances_m.append(running_distance)
        durations_minutes.append(end_clock - departure_minutes)
        finish_clock_minutes.append(end_clock)
        loads_kg.append(
            int(stops.loc[stops["stop_id"].isin(seq_real_stops), "demand_kg"].sum())
            if seq_real_stops else 0
        )

    total_distance_m = float(sum(distances_m))
    total_co2_g = total_distance_m / METRES_PER_KM * CO2_GRAMS_PER_KM

    return VrpSolution(
        routes=routes,
        routes_with_depot=routes_with_depot,
        arrivals_minutes=arrivals_minutes,
        tip_events=tip_events,
        distances_m=distances_m,
        durations_minutes=durations_minutes,
        finish_clock_minutes=finish_clock_minutes,
        total_distance_m=total_distance_m,
        total_co2_g=total_co2_g,
        loads_kg=loads_kg,
        school_violations=school_violations,
        peak_multiplier=peak_mult,
        departure_minutes=departure_minutes,
        source=matrix_source,
    )


def _subset_matrix(matrix: np.ndarray, global_stop_ids: List[int]) -> np.ndarray:
    """Return a depot-first matrix for a subset of global stop IDs."""
    indices = [0] + [sid + 1 for sid in global_stop_ids]
    return matrix[np.ix_(indices, indices)]


def _remap_zoned_solution(
    local_solution: VrpSolution,
    local_stops: pd.DataFrame,
    vehicle_offset: int,
    original_stop_ids: List[int],
) -> VrpSolution:
    """Convert a one-zone solution back to global stop IDs and vehicle IDs."""
    mapped_routes: List[List[int]] = []
    mapped_routes_with_depot: List[List[int]] = []

    for route in local_solution.routes:
        mapped_routes.append([original_stop_ids[sid] for sid in route])

    for route in local_solution.routes_with_depot:
        mapped_nodes: List[int] = []
        for node in route:
            if node == 0:
                mapped_nodes.append(0)
            elif 1 <= node <= len(original_stop_ids):
                mapped_nodes.append(original_stop_ids[node - 1] + 1)
            else:
                mapped_nodes.append(0)
        mapped_routes_with_depot.append(mapped_nodes)

    mapped_tips = []
    for tip in local_solution.tip_events:
        after_stop_id = None
        if tip.after_stop_id is not None and tip.after_stop_id < len(original_stop_ids):
            after_stop_id = original_stop_ids[tip.after_stop_id]
        mapped_tips.append(TipEvent(
            vehicle=tip.vehicle + vehicle_offset,
            after_stop_id=after_stop_id,
            cumulative_km=tip.cumulative_km,
            load_kg_before_tip=tip.load_kg_before_tip,
            clock_minutes=tip.clock_minutes,
        ))

    return VrpSolution(
        routes=mapped_routes,
        routes_with_depot=mapped_routes_with_depot,
        arrivals_minutes=local_solution.arrivals_minutes,
        tip_events=mapped_tips,
        distances_m=local_solution.distances_m,
        durations_minutes=local_solution.durations_minutes,
        finish_clock_minutes=local_solution.finish_clock_minutes,
        total_distance_m=local_solution.total_distance_m,
        total_co2_g=local_solution.total_co2_g,
        loads_kg=local_solution.loads_kg,
        school_violations=local_solution.school_violations,
        peak_multiplier=local_solution.peak_multiplier,
        departure_minutes=local_solution.departure_minutes,
        source=local_solution.source,
    )


def solve_zoned_vrp(
    stops: pd.DataFrame,
    matrix: np.ndarray,
    num_vehicles: int,
    vehicle_capacity_kg: int = DEFAULT_VEHICLE_CAPACITY_KG,
    tip_threshold: float = TIP_THRESHOLD,
    matrix_source: str = "haversine",
    time_limit_seconds: int = ZONED_SOLVER_TIME_LIMIT_SECONDS,
    departure_minutes: int = DEFAULT_DEPARTURE_MINUTES,
    apply_peak_hours: bool = False,
    apply_school_windows: bool = False,
    school_adjacent_stop_ids: Optional[Set[int]] = None,
) -> VrpSolution:
    """Cluster stops geographically, then solve one route per zone."""
    if num_vehicles < 1:
        raise ValueError("num_vehicles must be at least 1")
    if len(stops) < num_vehicles:
        return solve_vrp(
            stops=stops,
            matrix=matrix,
            num_vehicles=num_vehicles,
            vehicle_capacity_kg=vehicle_capacity_kg,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            time_limit_seconds=time_limit_seconds,
            departure_minutes=departure_minutes,
            apply_peak_hours=apply_peak_hours,
            apply_school_windows=apply_school_windows,
            school_adjacent_stop_ids=school_adjacent_stop_ids,
        )

    school_adjacent_stop_ids = school_adjacent_stop_ids or set()
    coords = stops[["lat", "lng"]].to_numpy()
    labels = KMeans(n_clusters=num_vehicles, random_state=0, n_init=10).fit_predict(coords)

    combined_routes: List[List[int]] = []
    combined_routes_with_depot: List[List[int]] = []
    combined_arrivals: List[List[int]] = []
    combined_tips: List[TipEvent] = []
    combined_distances: List[float] = []
    combined_durations: List[int] = []
    combined_finishes: List[int] = []
    combined_loads: List[int] = []
    school_violations = 0
    peak_multiplier = 1.0

    for zone in range(num_vehicles):
        zone_stops = stops.loc[labels == zone].copy()
        if zone_stops.empty:
            combined_routes.append([])
            combined_routes_with_depot.append([0, 0])
            combined_arrivals.append([departure_minutes, departure_minutes])
            combined_distances.append(0.0)
            combined_durations.append(0)
            combined_finishes.append(departure_minutes)
            combined_loads.append(0)
            continue

        original_ids = zone_stops["stop_id"].astype(int).tolist()
        zone_matrix = _subset_matrix(matrix, original_ids)
        local_stops = zone_stops.reset_index(drop=True).copy()
        local_stops["stop_id"] = range(len(local_stops))
        local_school_ids = {
            idx for idx, original_id in enumerate(original_ids)
            if original_id in school_adjacent_stop_ids
        }

        local_solution = solve_vrp(
            stops=local_stops,
            matrix=zone_matrix,
            num_vehicles=1,
            vehicle_capacity_kg=vehicle_capacity_kg,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            time_limit_seconds=time_limit_seconds,
            departure_minutes=departure_minutes,
            apply_peak_hours=apply_peak_hours,
            apply_school_windows=apply_school_windows,
            school_adjacent_stop_ids=local_school_ids,
        )
        mapped = _remap_zoned_solution(local_solution, local_stops, zone, original_ids)
        combined_routes.extend(mapped.routes)
        combined_routes_with_depot.extend(mapped.routes_with_depot)
        combined_arrivals.extend(mapped.arrivals_minutes)
        combined_tips.extend(mapped.tip_events)
        combined_distances.extend(mapped.distances_m)
        combined_durations.extend(mapped.durations_minutes)
        combined_finishes.extend(mapped.finish_clock_minutes)
        combined_loads.extend(mapped.loads_kg)
        school_violations += mapped.school_violations
        peak_multiplier = mapped.peak_multiplier

    total_distance_m = float(sum(combined_distances))
    return VrpSolution(
        routes=combined_routes,
        routes_with_depot=combined_routes_with_depot,
        arrivals_minutes=combined_arrivals,
        tip_events=combined_tips,
        distances_m=combined_distances,
        durations_minutes=combined_durations,
        finish_clock_minutes=combined_finishes,
        total_distance_m=total_distance_m,
        total_co2_g=total_distance_m / METRES_PER_KM * CO2_GRAMS_PER_KM,
        loads_kg=combined_loads,
        school_violations=school_violations,
        peak_multiplier=peak_multiplier,
        departure_minutes=departure_minutes,
        source=matrix_source,
    )
