"""Folium rendering for the Hillingdon route optimiser."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import folium
import pandas as pd

from .config import (
    BROKEN_TRUCK_COLOUR,
    DEFAULT_STOP_COLOUR,
    DEFAULT_ZOOM,
    DEPOT_ICON_COLOUR,
    DEPOT_LAT,
    DEPOT_LNG,
    DEPOT_NAME,
    PEAK_HOUR_WINDOWS,
    PEAK_TRAFFIC_ZONE_COLOUR,
    PEAK_TRAFFIC_ZONE_FILL_OPACITY,
    PEAK_TRAFFIC_ZONES,
    ROAD_CLOSURE_LINE_COLOUR,
    SCHOOL_OVERLAY_WINDOWS,
    SCHOOL_ZONE_COLOUR,
    SCHOOL_ZONE_FILL_OPACITY,
    SCHOOL_ZONE_MAX_MARKERS,
    SCHOOL_ZONE_RADIUS_METRES,
    TIP_ICON_COLOUR,
    VEHICLE_COLOURS,
    WARD_COLOURS,
)
from .disruptions import BreakdownPlan
from .solver import VrpSolution


Coordinate = Tuple[float, float]


def _is_in_time_window(clock_minutes: int, windows: Sequence[Tuple[int, int]]) -> bool:
    """Return whether a clock time falls within any configured window."""
    return any(start <= clock_minutes < end for start, end in windows)


def is_peak_window_active(departure_minutes: int) -> bool:
    """Return whether the selected departure time is inside a peak window."""
    return _is_in_time_window(departure_minutes, PEAK_HOUR_WINDOWS)


def is_school_window_active(departure_minutes: int) -> bool:
    """Return whether the selected departure time is inside a school window."""
    return _is_in_time_window(departure_minutes, SCHOOL_OVERLAY_WINDOWS)


def get_peak_traffic_zones() -> List[Dict[str, Any]]:
    """Return deterministic visual traffic zones."""
    return list(PEAK_TRAFFIC_ZONES)


def _stop_lookup(stops: pd.DataFrame) -> Dict[int, Dict[str, Any]]:
    """Map synthetic stop IDs to row dictionaries."""
    return {
        int(row["stop_id"]): row.to_dict()
        for _, row in stops.iterrows()
    }


def _ordered_points_for_route(
    stops: pd.DataFrame,
    route: Sequence[int],
) -> List[Coordinate]:
    """Return depot-start and depot-end coordinates for a stop route."""
    lookup = _stop_lookup(stops)
    coords: List[Coordinate] = [(DEPOT_LAT, DEPOT_LNG)]
    for stop_id in route:
        row = lookup.get(int(stop_id))
        if row is not None:
            coords.append((float(row["lat"]), float(row["lng"])))
    coords.append((DEPOT_LAT, DEPOT_LNG))
    return coords


def _ordered_points_from_matrix_nodes(
    stops: pd.DataFrame,
    route_nodes: Sequence[int],
) -> List[Coordinate]:
    """Return coordinates from a solver route that includes depot and reload nodes."""
    lookup = _stop_lookup(stops)
    points: List[Coordinate] = []
    for node in route_nodes:
        if node == 0:
            points.append((DEPOT_LAT, DEPOT_LNG))
        else:
            row = lookup.get(int(node) - 1)
            if row is not None:
                points.append((float(row["lat"]), float(row["lng"])))
    if not points:
        return [(DEPOT_LAT, DEPOT_LNG), (DEPOT_LAT, DEPOT_LNG)]
    return points


def get_nearest_node_for_point(graph: Any, lat: float, lon: float) -> Optional[int]:
    """Snap a WGS84 point to the nearest graph node."""
    if graph is None:
        return None
    try:
        import osmnx as ox

        return int(ox.distance.nearest_nodes(graph, lon, lat))
    except Exception:
        return None


def shortest_path_nodes(
    graph: Any,
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    weight: str = "length",
) -> Optional[List[int]]:
    """Return shortest-path graph nodes for one route leg."""
    if graph is None:
        return None
    try:
        import networkx as nx

        start_node = get_nearest_node_for_point(graph, start_lat, start_lon)
        end_node = get_nearest_node_for_point(graph, end_lat, end_lon)
        if start_node is None or end_node is None:
            return None
        return list(nx.shortest_path(graph, start_node, end_node, weight=weight))
    except Exception:
        return None


def _best_edge_data(graph: Any, u: int, v: int) -> Dict[str, Any]:
    """Pick the shortest parallel edge between two path nodes."""
    edge_bundle = graph.get_edge_data(u, v) or {}
    if not edge_bundle:
        return {}
    _, data = min(
        edge_bundle.items(),
        key=lambda item: float(item[1].get("length", 0.0)),
    )
    return data


def path_nodes_to_coordinates(graph: Any, node_path: Sequence[int]) -> List[Coordinate]:
    """Convert graph nodes to full road-following coordinates."""
    if graph is None or not node_path:
        return []
    if len(node_path) == 1:
        node = node_path[0]
        return [(float(graph.nodes[node]["y"]), float(graph.nodes[node]["x"]))]

    coords: List[Coordinate] = []
    for u, v in zip(node_path, node_path[1:]):
        data = _best_edge_data(graph, int(u), int(v))
        geometry = data.get("geometry")
        if geometry is not None:
            segment = [(float(lat), float(lon)) for lon, lat in geometry.coords]
        else:
            segment = [
                (float(graph.nodes[u]["y"]), float(graph.nodes[u]["x"])),
                (float(graph.nodes[v]["y"]), float(graph.nodes[v]["x"])),
            ]
        if coords and segment:
            coords.extend(segment[1:])
        else:
            coords.extend(segment)
    return coords


def build_vehicle_route_geometry(
    graph: Any,
    ordered_points: Sequence[Coordinate],
    cache: Optional[Dict[Tuple[int, float, float, float, float], List[Coordinate]]] = None,
) -> Tuple[List[Coordinate], List[str]]:
    """Build one road-following vehicle route polyline."""
    warnings: List[str] = []
    if graph is None:
        return list(ordered_points), warnings

    cache = cache if cache is not None else {}
    route_coords: List[Coordinate] = []
    for start, end in zip(ordered_points, ordered_points[1:]):
        cache_key = (
            id(graph),
            round(start[0], 6),
            round(start[1], 6),
            round(end[0], 6),
            round(end[1], 6),
        )
        segment = cache.get(cache_key)
        if segment is None:
            node_path = shortest_path_nodes(graph, start[0], start[1], end[0], end[1])
            segment = path_nodes_to_coordinates(graph, node_path) if node_path else []
            if not segment:
                segment = [start, end]
                warnings.append("A route leg could not use road geometry and was drawn directly.")
            cache[cache_key] = segment
        if route_coords and segment:
            route_coords.extend(segment[1:])
        else:
            route_coords.extend(segment)
    return route_coords, warnings


def draw_vehicle_route_geometry_on_map(
    fmap: folium.Map,
    coords: Sequence[Coordinate],
    vehicle_id: int,
    colour: str,
    is_broken: bool = False,
) -> None:
    """Draw one vehicle route polyline."""
    if len(coords) < 2:
        return
    folium.PolyLine(
        coords,
        color=BROKEN_TRUCK_COLOUR if is_broken else colour,
        weight=7 if not is_broken else 6,
        opacity=0.9 if not is_broken else 0.55,
        dash_array="8, 8" if is_broken else None,
        tooltip=f"Vehicle {vehicle_id + 1}",
    ).add_to(fmap)


def draw_closed_edges_on_map(
    fmap: folium.Map,
    closed_edges: Sequence[Dict[str, Any]],
) -> None:
    """Draw closed road segments in red."""
    for edge in closed_edges:
        coords = edge.get("coords") or []
        if len(coords) < 2:
            continue
        folium.PolyLine(
            coords,
            color=ROAD_CLOSURE_LINE_COLOUR,
            weight=8,
            opacity=0.95,
            dash_array="6, 6",
            tooltip=f"Closed: {edge.get('name', 'Road segment')}",
        ).add_to(fmap)


def draw_peak_traffic_zones_on_map(
    fmap: folium.Map,
    zones: Sequence[Dict[str, Any]],
    active: bool,
) -> None:
    """Draw red peak traffic overlays when active."""
    if not active:
        return
    for zone in zones:
        folium.Circle(
            location=(float(zone["lat"]), float(zone["lng"])),
            radius=float(zone["radius_m"]),
            color=PEAK_TRAFFIC_ZONE_COLOUR,
            fill=True,
            fill_color=PEAK_TRAFFIC_ZONE_COLOUR,
            fill_opacity=PEAK_TRAFFIC_ZONE_FILL_OPACITY,
            weight=2,
            tooltip=f"{zone['name']}: peak traffic zone active",
            popup="Peak traffic zone active",
        ).add_to(fmap)


def draw_school_zones_on_map(
    fmap: folium.Map,
    school_points: Optional[Sequence[Coordinate]],
    active: bool,
) -> None:
    """Draw yellow school influence zones when active."""
    if not active or not school_points:
        return
    for idx, (lat, lng) in enumerate(school_points[:SCHOOL_ZONE_MAX_MARKERS], start=1):
        folium.Circle(
            location=(float(lat), float(lng)),
            radius=SCHOOL_ZONE_RADIUS_METRES,
            color=SCHOOL_ZONE_COLOUR,
            fill=True,
            fill_color=SCHOOL_ZONE_COLOUR,
            fill_opacity=SCHOOL_ZONE_FILL_OPACITY,
            weight=1,
            tooltip=f"School zone active {idx}",
            popup="School zone active",
        ).add_to(fmap)


def draw_breakdown_marker_on_map(
    fmap: folium.Map,
    breakdown_plan: Optional[BreakdownPlan],
) -> None:
    """Draw the broken truck location."""
    if breakdown_plan is None:
        return
    folium.Marker(
        location=breakdown_plan.breakdown_location,
        tooltip=f"Vehicle {breakdown_plan.broken_truck + 1} breakdown",
        popup=(
            f"Vehicle {breakdown_plan.broken_truck + 1} out of service after "
            f"{breakdown_plan.breakdown_after} completed stops"
        ),
        icon=folium.Icon(color="black", icon="wrench", prefix="fa"),
    ).add_to(fmap)


def _stop_assignments(
    routes: Sequence[Sequence[int]],
) -> Dict[int, Tuple[int, int]]:
    """Return stop ID to (vehicle, visit order)."""
    assignments: Dict[int, Tuple[int, int]] = {}
    for vehicle_id, route in enumerate(routes):
        for order, stop_id in enumerate(route, start=1):
            assignments[int(stop_id)] = (vehicle_id, order)
    return assignments


def _numbered_stop_marker(
    order: int,
    colour: str,
) -> folium.DivIcon:
    """Create a compact numbered marker."""
    html = f"""
    <div style="
        background:{colour};
        color:white;
        border:2px solid white;
        border-radius:14px;
        width:28px;
        height:28px;
        line-height:24px;
        text-align:center;
        font-size:12px;
        font-weight:700;
        box-shadow:0 1px 4px rgba(0,0,0,0.35);">
        {order}
    </div>
    """
    return folium.DivIcon(html=html, icon_size=(28, 28), icon_anchor=(14, 14))


def _add_stop_markers(
    fmap: folium.Map,
    stops: pd.DataFrame,
    routes: Sequence[Sequence[int]],
    school_adjacent_stop_ids: Set[int],
) -> None:
    """Add numbered stop markers for assigned stops."""
    assignments = _stop_assignments(routes)
    for _, row in stops.iterrows():
        stop_id = int(row["stop_id"])
        ward = str(row["ward"])
        assigned = assignments.get(stop_id)
        if assigned is None:
            colour = WARD_COLOURS.get(ward, DEFAULT_STOP_COLOUR)
            tooltip = f"Unassigned stop {stop_id} | {ward}"
            folium.CircleMarker(
                location=(float(row["lat"]), float(row["lng"])),
                radius=4,
                color=colour,
                fill=True,
                fill_color=colour,
                fill_opacity=0.55,
                weight=1,
                tooltip=tooltip,
            ).add_to(fmap)
            continue

        vehicle_id, order = assigned
        colour = VEHICLE_COLOURS[vehicle_id % len(VEHICLE_COLOURS)]
        school_flag = "yes" if stop_id in school_adjacent_stop_ids else "no"
        popup = (
            f"Stop {stop_id}<br>"
            f"Truck {vehicle_id + 1}<br>"
            f"Visit order {order}<br>"
            f"Ward {ward}<br>"
            f"School-adjacent {school_flag}"
        )
        folium.Marker(
            location=(float(row["lat"]), float(row["lng"])),
            tooltip=f"Truck {vehicle_id + 1}, stop {order}: {ward}",
            popup=popup,
            icon=_numbered_stop_marker(order, colour),
        ).add_to(fmap)


def _add_tip_markers(
    fmap: folium.Map,
    stops: pd.DataFrame,
    solution: VrpSolution,
) -> None:
    """Add markers for reload events."""
    lookup = _stop_lookup(stops)
    for tip in solution.tip_events:
        if tip.after_stop_id is not None and tip.after_stop_id in lookup:
            row = lookup[tip.after_stop_id]
            location = (float(row["lat"]), float(row["lng"]))
            label = f"Tip after stop {tip.after_stop_id}"
        else:
            location = (DEPOT_LAT, DEPOT_LNG)
            label = "Tip at depot"
        folium.Marker(
            location=location,
            tooltip=label,
            popup=f"{label}, vehicle {tip.vehicle + 1}",
            icon=folium.Icon(color=TIP_ICON_COLOUR, icon="refresh", prefix="fa"),
        ).add_to(fmap)


def _add_legend(
    fmap: folium.Map,
    routes: Sequence[Sequence[int]],
    peak_zones_active: bool,
    school_zones_active: bool,
    breakdown_active: bool,
) -> None:
    """Add a simple route colour legend."""
    rows = []
    for vehicle_id, route in enumerate(routes):
        if not route:
            continue
        colour = VEHICLE_COLOURS[vehicle_id % len(VEHICLE_COLOURS)]
        rows.append(
            f"<div><span style='background:{colour};display:inline-block;width:12px;"
            f"height:12px;margin-right:6px;'></span>Truck {vehicle_id + 1}</div>"
        )
    if peak_zones_active:
        rows.append(
            "<div><span style='background:#dc2626;display:inline-block;width:12px;"
            "height:12px;margin-right:6px;opacity:0.45;'></span>Peak traffic zone</div>"
        )
    if school_zones_active:
        rows.append(
            "<div><span style='background:#facc15;display:inline-block;width:12px;"
            "height:12px;margin-right:6px;opacity:0.55;'></span>School zone</div>"
        )
    if breakdown_active:
        rows.append(
            "<div><span style='background:#111827;display:inline-block;width:12px;"
            "height:12px;margin-right:6px;'></span>Breakdown point</div>"
        )
    if not rows:
        return
    html = """
    <div style="
        position: fixed;
        bottom: 24px;
        left: 24px;
        z-index: 9999;
        background: rgba(9,10,11,0.92);
        border: 1px solid rgba(45,242,230,0.22);
        border-radius: 6px;
        padding: 8px 10px;
        font-size: 12px;
        color: #f8fafc;
        box-shadow: 0 14px 30px rgba(0,0,0,0.38);">
        <strong>Routes</strong>
    """ + "".join(rows) + "</div>"
    fmap.get_root().html.add_child(folium.Element(html))


def build_map(
    stops: pd.DataFrame,
    solution: Optional[VrpSolution],
    depot: Coordinate = (DEPOT_LAT, DEPOT_LNG),
    graph: Any = None,
    closed_edges: Optional[Sequence[Dict[str, Any]]] = None,
    school_adjacent_stop_ids: Optional[Set[int]] = None,
    route_override: Optional[List[List[int]]] = None,
    breakdown_plan: Optional[BreakdownPlan] = None,
    geometry_cache: Optional[Dict[Tuple[int, float, float, float, float], List[Coordinate]]] = None,
    peak_zones_active: bool = False,
    school_zones_active: bool = False,
    school_points: Optional[Sequence[Coordinate]] = None,
) -> Tuple[folium.Map, List[str]]:
    """Build the interactive route map and return geometry warnings."""
    fmap = folium.Map(location=depot, zoom_start=DEFAULT_ZOOM, tiles=None)
    folium.TileLayer(
        tiles="CartoDB dark_matter",
        name="Dark map",
        control=False,
    ).add_to(fmap)
    warnings: List[str] = []
    school_adjacent_stop_ids = school_adjacent_stop_ids or set()
    closed_edges = closed_edges or []

    folium.Marker(
        location=depot,
        tooltip=DEPOT_NAME,
        popup=DEPOT_NAME,
        icon=folium.Icon(color=DEPOT_ICON_COLOUR, icon="recycle", prefix="fa"),
    ).add_to(fmap)

    draw_peak_traffic_zones_on_map(fmap, get_peak_traffic_zones(), peak_zones_active)
    draw_school_zones_on_map(fmap, school_points, school_zones_active)

    routes = route_override if route_override is not None else (solution.routes if solution else [])

    if solution is not None:
        for vehicle_id, route in enumerate(routes):
            colour = VEHICLE_COLOURS[vehicle_id % len(VEHICLE_COLOURS)]
            is_broken = breakdown_plan is not None and vehicle_id == breakdown_plan.broken_truck
            if route_override is None and vehicle_id < len(solution.routes_with_depot):
                ordered_points = _ordered_points_from_matrix_nodes(stops, solution.routes_with_depot[vehicle_id])
            else:
                ordered_points = _ordered_points_for_route(stops, route)
            coords, route_warnings = build_vehicle_route_geometry(
                graph=graph,
                ordered_points=ordered_points,
                cache=geometry_cache,
            )
            warnings.extend(route_warnings)
            draw_vehicle_route_geometry_on_map(fmap, coords, vehicle_id, colour, is_broken=is_broken)

        _add_tip_markers(fmap, stops, solution)

    _add_stop_markers(fmap, stops, routes, school_adjacent_stop_ids)
    draw_closed_edges_on_map(fmap, closed_edges)
    draw_breakdown_marker_on_map(fmap, breakdown_plan)
    _add_legend(
        fmap,
        routes,
        peak_zones_active=peak_zones_active,
        school_zones_active=school_zones_active,
        breakdown_active=breakdown_plan is not None,
    )
    return fmap, warnings
