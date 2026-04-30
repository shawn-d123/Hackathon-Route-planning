"""Streamlit UI for the Hillingdon route optimisation demo."""

from __future__ import annotations

import time
import warnings
from datetime import time as clock_time
from typing import Any, Dict, List, Optional, Set

import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from .config import (
    AVERAGE_SPEED_KMH,
    DEFAULT_DEPARTURE_MINUTES,
    DEFAULT_NUM_STOPS,
    DEFAULT_NUM_VEHICLES,
    DEFAULT_SEED,
    DEFAULT_VEHICLE_CAPACITY_KG,
    DEMO_PRESET_SEED,
    DEMO_PRESET_STOPS,
    DEMO_PRESET_VEHICLES,
    DEPOT_LAT,
    DEPOT_LNG,
    GRAMS_PER_KG,
    MAP_HEIGHT_PIXELS,
    MAX_NUM_STOPS,
    MAX_NUM_VEHICLES,
    METRES_PER_KM,
    MIN_NUM_STOPS,
    MIN_NUM_VEHICLES,
    MINUTES_PER_HOUR,
    NO_CLOSURE_LABEL,
    ROAD_CLOSURE_SCENARIOS,
    TIP_THRESHOLD,
    TIP_THRESHOLD_MAX_PERCENT,
    TIP_THRESHOLD_MIN_PERCENT,
    TIP_THRESHOLD_STEP_PERCENT,
)
from .disruptions import BreakdownPlan, get_active_graph, simulate_truck_breakdown
from .generate_stops import generate_stops
from .graph_utils import (
    build_distance_matrix,
    load_or_build_graph,
    load_or_fetch_schools,
    tag_school_adjacent_stops,
)
from .solver import VrpSolution, naive_route_distance, solve_vrp, solve_zoned_vrp
from .viz import build_map
from .viz import is_peak_window_active, is_school_window_active


def format_minutes(clock_minutes: int) -> str:
    """Format minutes from midnight as HH:MM."""
    minutes = int(clock_minutes) % (24 * MINUTES_PER_HOUR)
    return f"{minutes // MINUTES_PER_HOUR:02d}:{minutes % MINUTES_PER_HOUR:02d}"


def _duration_label(minutes: int) -> str:
    """Format elapsed minutes as h:mm."""
    minutes = max(0, int(minutes))
    return f"{minutes // MINUTES_PER_HOUR}:{minutes % MINUTES_PER_HOUR:02d}"


def _init_state() -> None:
    """Initialise Streamlit session defaults."""
    defaults: Dict[str, Any] = {
        "num_stops": DEFAULT_NUM_STOPS,
        "num_vehicles": DEFAULT_NUM_VEHICLES,
        "seed": DEFAULT_SEED,
        "departure": DEFAULT_DEPARTURE_MINUTES,
        "departure_time_input": clock_time(
            DEFAULT_DEPARTURE_MINUTES // MINUTES_PER_HOUR,
            DEFAULT_DEPARTURE_MINUTES % MINUTES_PER_HOUR,
        ),
        "tip_threshold_percent": int(TIP_THRESHOLD * 100),
        "use_zoning": True,
        "use_school_windows": True,
        "use_peak_hours": True,
        "use_osmnx": False,
        "closure_scenario": NO_CLOSURE_LABEL,
        "base_graph": None,
        "active_graph": None,
        "closed_edges": [],
        "closure_warning": None,
        "matrix": None,
        "school_ids": set(),
        "school_points": [],
        "geometry_cache": {},
        "enable_breakdown": False,
        "broken_truck": 1,
        "breakdown_after": 1,
        "breakdown_plan": None,
        "stops": None,
        "solution": None,
        "baseline_m": 0.0,
        "matrix_source": "haversine",
        "last_runtime_s": 0.0,
        "warnings": [],
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def _apply_demo_preset() -> None:
    """Load the stage-ready scenario."""
    st.session_state.num_stops = DEMO_PRESET_STOPS
    st.session_state.num_vehicles = DEMO_PRESET_VEHICLES
    st.session_state.seed = DEMO_PRESET_SEED
    st.session_state.departure = DEFAULT_DEPARTURE_MINUTES
    st.session_state.departure_time_input = clock_time(
        DEFAULT_DEPARTURE_MINUTES // MINUTES_PER_HOUR,
        DEFAULT_DEPARTURE_MINUTES % MINUTES_PER_HOUR,
    )
    st.session_state.tip_threshold_percent = int(TIP_THRESHOLD * 100)
    st.session_state.use_zoning = True
    st.session_state.use_school_windows = True
    st.session_state.use_peak_hours = True
    st.session_state.use_osmnx = True
    st.session_state.closure_scenario = NO_CLOSURE_LABEL
    st.session_state.enable_breakdown = False
    st.session_state.breakdown_plan = None


def _collect_school_context(stops: pd.DataFrame, enabled: bool) -> tuple[Set[int], list[tuple[float, float]]]:
    """Fetch school points and tag nearby synthetic stops when enabled."""
    if not enabled:
        return set(), []
    schools = load_or_fetch_schools()
    return tag_school_adjacent_stops(stops, schools), schools or []


def _solve_current() -> None:
    """Build data, solve routes, and store the result in session state."""
    start = time.perf_counter()
    warning_messages = []
    stops = generate_stops(
        n_stops=int(st.session_state.num_stops),
        seed=int(st.session_state.seed),
    )

    base_graph = st.session_state.base_graph
    active_graph = None
    closed_edges: List[Dict[str, Any]] = []
    closure_warning = None
    wants_road_graph = (
        bool(st.session_state.use_osmnx)
        or st.session_state.closure_scenario != NO_CLOSURE_LABEL
    )
    if wants_road_graph and base_graph is None:
        base_graph = load_or_build_graph()
    if wants_road_graph and base_graph is not None:
        closure_result = get_active_graph(base_graph, st.session_state.closure_scenario)
        active_graph = closure_result.graph
        closed_edges = closure_result.closed_edges
        closure_warning = closure_result.warning
    elif st.session_state.closure_scenario != NO_CLOSURE_LABEL:
        closure_warning = "Road closure scenarios need OSMnx roads, so routing used the offline fallback."

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        matrix, matrix_source = build_distance_matrix(
            stops,
            use_osmnx=wants_road_graph,
            graph=active_graph,
        )
        warning_messages = [str(item.message) for item in caught]

    baseline_m, _ = naive_route_distance(stops, matrix)
    school_ids, school_points = _collect_school_context(
        stops,
        bool(st.session_state.use_school_windows),
    )
    tip_threshold = int(st.session_state.tip_threshold_percent) / 100

    if st.session_state.use_zoning:
        solution = solve_zoned_vrp(
            stops=stops,
            matrix=matrix,
            num_vehicles=int(st.session_state.num_vehicles),
            vehicle_capacity_kg=DEFAULT_VEHICLE_CAPACITY_KG,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            departure_minutes=int(st.session_state.departure),
            apply_peak_hours=bool(st.session_state.use_peak_hours),
            apply_school_windows=bool(st.session_state.use_school_windows),
            school_adjacent_stop_ids=school_ids,
        )
    else:
        solution = solve_vrp(
            stops=stops,
            matrix=matrix,
            num_vehicles=int(st.session_state.num_vehicles),
            vehicle_capacity_kg=DEFAULT_VEHICLE_CAPACITY_KG,
            tip_threshold=tip_threshold,
            matrix_source=matrix_source,
            departure_minutes=int(st.session_state.departure),
            apply_peak_hours=bool(st.session_state.use_peak_hours),
            apply_school_windows=bool(st.session_state.use_school_windows),
            school_adjacent_stop_ids=school_ids,
        )

    st.session_state.base_graph = base_graph
    st.session_state.active_graph = active_graph
    st.session_state.closed_edges = closed_edges
    st.session_state.closure_warning = closure_warning
    st.session_state.matrix = matrix
    st.session_state.school_ids = school_ids
    st.session_state.school_points = school_points
    st.session_state.geometry_cache = {}
    st.session_state.breakdown_plan = None
    st.session_state.stops = stops
    st.session_state.solution = solution
    st.session_state.baseline_m = baseline_m
    st.session_state.matrix_source = matrix_source
    st.session_state.last_runtime_s = time.perf_counter() - start
    st.session_state.warnings = warning_messages


def _run_breakdown_current() -> Optional[BreakdownPlan]:
    """Simulate a truck failure against the latest solution."""
    solution: Optional[VrpSolution] = st.session_state.solution
    stops: Optional[pd.DataFrame] = st.session_state.stops
    matrix = st.session_state.matrix
    if solution is None or stops is None or matrix is None:
        return None

    broken_truck = max(0, int(st.session_state.broken_truck) - 1)
    route_len = len(solution.routes[broken_truck]) if broken_truck < len(solution.routes) else 0
    breakdown_after = max(0, min(int(st.session_state.breakdown_after), route_len))
    plan = simulate_truck_breakdown(
        stops=stops,
        matrix=matrix,
        solution=solution,
        broken_truck_id=broken_truck,
        breakdown_after_stop_index=breakdown_after,
        vehicle_capacity_kg=DEFAULT_VEHICLE_CAPACITY_KG,
        tip_threshold=int(st.session_state.tip_threshold_percent) / 100,
        matrix_source=st.session_state.matrix_source,
        departure_minutes=int(st.session_state.departure),
        apply_peak_hours=bool(st.session_state.use_peak_hours),
        apply_school_windows=bool(st.session_state.use_school_windows),
        school_adjacent_stop_ids=set(st.session_state.school_ids),
    )
    st.session_state.breakdown_plan = plan
    return plan


def _metric_card(label: str, value: str, detail: str) -> None:
    """Render a compact metric card."""
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-shine"></div>
            <div class="metric-label">{label}</div>
            <div class="metric-value">{value}</div>
            <div class="metric-detail">{detail}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_page_styles() -> None:
    """Apply visual styling without changing Streamlit control behaviour."""
    st.markdown(
        """
        <style>
        :root {
            --route-ink: #111827;
            --route-muted: #64748b;
            --route-line: #d8dee8;
            --route-soft: #f7fafc;
            --route-blue: #2563eb;
            --route-green: #16a34a;
            --route-yellow: #facc15;
        }

        .stApp {
            background:
                radial-gradient(circle at 18% 0%, rgba(37, 99, 235, 0.09), transparent 30rem),
                linear-gradient(180deg, #f8fafc 0%, #eef4f8 54%, #f8fafc 100%);
        }

        [data-testid="stHeader"] {
            background: rgba(248, 250, 252, 0.78);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(216, 222, 232, 0.75);
        }

        [data-testid="stSidebar"] {
            background: #ffffff;
            border-right: 1px solid var(--route-line);
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
            color: var(--route-ink);
            letter-spacing: 0;
        }

        .block-container {
            max-width: 1500px;
            padding-top: 2rem;
            padding-bottom: 3rem;
        }

        .route-hero {
            position: relative;
            overflow: hidden;
            border: 1px solid rgba(216, 222, 232, 0.95);
            border-radius: 8px;
            padding: 24px 28px;
            margin-bottom: 18px;
            background:
                linear-gradient(135deg, rgba(255, 255, 255, 0.96), rgba(241, 247, 252, 0.94)),
                repeating-linear-gradient(135deg, rgba(37, 99, 235, 0.07) 0 1px, transparent 1px 18px);
            box-shadow: 0 18px 44px rgba(15, 23, 42, 0.08);
        }

        .route-hero::after {
            content: "";
            position: absolute;
            inset: auto 22px 18px auto;
            width: 280px;
            height: 84px;
            border-top: 2px solid rgba(22, 163, 74, 0.42);
            border-right: 2px solid rgba(37, 99, 235, 0.32);
            transform: skewX(-18deg);
            opacity: 0.75;
        }

        .hero-eyebrow {
            color: var(--route-blue);
            font-size: 0.76rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .hero-title {
            color: var(--route-ink);
            font-size: clamp(2rem, 4vw, 3.2rem);
            font-weight: 800;
            line-height: 1.02;
            margin: 0;
            letter-spacing: 0;
        }

        .hero-copy {
            color: #475569;
            font-size: 1rem;
            line-height: 1.55;
            max-width: 760px;
            margin-top: 12px;
            margin-bottom: 18px;
        }

        .hero-chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            position: relative;
            z-index: 1;
        }

        .status-chip {
            display: inline-flex;
            align-items: center;
            min-height: 30px;
            padding: 6px 10px;
            border: 1px solid rgba(203, 213, 225, 0.95);
            border-radius: 8px;
            background: rgba(255, 255, 255, 0.78);
            color: #334155;
            font-size: 0.78rem;
            font-weight: 700;
            box-shadow: 0 5px 14px rgba(15, 23, 42, 0.05);
        }

        .metric-card {
            position: relative;
            overflow: hidden;
            min-height: 128px;
            border: 1px solid rgba(216, 222, 232, 0.96);
            border-radius: 8px;
            padding: 16px 18px;
            background: rgba(255, 255, 255, 0.96);
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.08);
        }

        .metric-card::before {
            content: "";
            position: absolute;
            inset: 0 auto 0 0;
            width: 4px;
            background: linear-gradient(180deg, var(--route-blue), var(--route-green));
        }

        .metric-shine {
            position: absolute;
            inset: 0 0 auto auto;
            width: 112px;
            height: 112px;
            background: radial-gradient(circle, rgba(37, 99, 235, 0.12), transparent 68%);
        }

        .metric-label {
            position: relative;
            color: #526071;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.04em;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .metric-value {
            position: relative;
            color: var(--route-ink);
            font-size: 2rem;
            font-weight: 800;
            line-height: 1.1;
            letter-spacing: 0;
        }

        .metric-detail {
            position: relative;
            color: var(--route-muted);
            font-size: 0.84rem;
            margin-top: 9px;
        }

        .map-shell {
            border: 1px solid rgba(216, 222, 232, 0.95);
            border-radius: 8px;
            overflow: hidden;
            background: #ffffff;
            box-shadow: 0 16px 36px rgba(15, 23, 42, 0.08);
        }

        .section-panel {
            border: 1px solid rgba(216, 222, 232, 0.92);
            border-radius: 8px;
            padding: 16px 18px;
            background: rgba(255, 255, 255, 0.92);
            box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
        }

        .section-title {
            color: var(--route-ink);
            font-size: 1rem;
            font-weight: 800;
            margin-bottom: 4px;
        }

        .section-copy {
            color: var(--route-muted);
            font-size: 0.88rem;
            line-height: 1.5;
        }

        .comparison-grid {
            display: grid;
            gap: 10px;
            margin-top: 4px;
        }

        .comparison-row {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            border-bottom: 1px solid #e5eaf0;
            padding: 8px 0;
            color: #334155;
            font-size: 0.9rem;
        }

        .comparison-row:last-child {
            border-bottom: 0;
        }

        .comparison-row strong {
            color: var(--route-ink);
            white-space: nowrap;
        }

        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            border-bottom: 1px solid #d8dee8;
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            padding: 10px 14px;
            background: rgba(255, 255, 255, 0.7);
            border: 1px solid #e5eaf0;
            border-bottom: 0;
            color: #475569;
            font-weight: 700;
        }

        .stTabs [aria-selected="true"] {
            background: #ffffff;
            color: var(--route-blue);
            box-shadow: 0 -2px 12px rgba(15, 23, 42, 0.05);
        }

        [data-testid="stDataFrame"] {
            border: 1px solid rgba(216, 222, 232, 0.95);
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 10px 24px rgba(15, 23, 42, 0.05);
        }

        div[data-testid="stAlert"] {
            border-radius: 8px;
            border: 1px solid rgba(203, 213, 225, 0.95);
        }

        .stButton > button {
            border-radius: 8px;
            font-weight: 800;
            min-height: 2.65rem;
        }

        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--route-blue), #1d4ed8);
            border: 0;
            box-shadow: 0 10px 22px rgba(37, 99, 235, 0.24);
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    """Render the dashboard header."""
    st.markdown(
        """
        <section class="route-hero">
            <div class="hero-eyebrow">RouteIQ operations dashboard</div>
            <h1 class="hero-title">Hillingdon waste route optimiser</h1>
            <div class="hero-copy">
                Synthetic collection planning with route optimisation, road-network fallback,
                closure scenarios, school-window awareness, and breakdown recovery.
            </div>
            <div class="hero-chip-row">
                <span class="status-chip">Synthetic stops only</span>
                <span class="status-chip">OpenStreetMap ready</span>
                <span class="status-chip">Haversine fallback</span>
                <span class="status-chip">No resident data</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_comparison_panel(solution: VrpSolution, baseline_m: float) -> None:
    """Render the comparison statistics with stronger visual hierarchy."""
    baseline_km = baseline_m / METRES_PER_KM
    optimised_km = solution.total_distance_m / METRES_PER_KM
    st.markdown(
        f"""
        <div class="section-panel">
            <div class="section-title">Comparison</div>
            <div class="section-copy">Current scenario output against the simple baseline route.</div>
            <div class="comparison-grid">
                <div class="comparison-row"><span>Baseline route</span><strong>{baseline_km:.1f} km</strong></div>
                <div class="comparison-row"><span>Optimised routes</span><strong>{optimised_km:.1f} km</strong></div>
                <div class="comparison-row"><span>CO2 estimate</span><strong>{solution.total_co2_g / GRAMS_PER_KG:.1f} kg</strong></div>
                <div class="comparison-row"><span>Solve time</span><strong>{st.session_state.last_runtime_s:.2f} s</strong></div>
                <div class="comparison-row"><span>School-window visits</span><strong>{solution.school_violations}</strong></div>
                <div class="comparison-row"><span>Peak multiplier</span><strong>{solution.peak_multiplier:.2f}</strong></div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_folium_map(fmap: Any, map_key: str) -> None:
    """Render a Folium component with a stable Streamlit key."""
    st.markdown('<div class="map-shell">', unsafe_allow_html=True)
    st_folium(
        fmap,
        height=MAP_HEIGHT_PIXELS,
        use_container_width=True,
        key=map_key,
    )
    st.markdown("</div>", unsafe_allow_html=True)


def _render_section_intro(title: str, copy: str) -> None:
    """Render a compact section header."""
    st.markdown(
        f"""
        <div class="section-panel">
            <div class="section-title">{title}</div>
            <div class="section-copy">{copy}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _active_overlay_flags() -> tuple[bool, bool]:
    """Return peak and school overlay active flags for the current scenario."""
    departure = int(st.session_state.departure)
    peak_active = bool(st.session_state.use_peak_hours) and is_peak_window_active(departure)
    school_active = bool(st.session_state.use_school_windows) and is_school_window_active(departure)
    return peak_active, school_active


def _baseline_minutes(stops: pd.DataFrame, baseline_m: float) -> float:
    """Estimate baseline working minutes from distance and service time."""
    travel = (baseline_m / METRES_PER_KM) / AVERAGE_SPEED_KMH * MINUTES_PER_HOUR
    service = float(stops["service_minutes"].sum()) if not stops.empty else 0.0
    return travel + service


def _render_metrics(stops: pd.DataFrame, solution: VrpSolution, baseline_m: float) -> None:
    """Render the four headline metrics."""
    baseline_km = baseline_m / METRES_PER_KM
    optimised_km = solution.total_distance_m / METRES_PER_KM
    km_saved = baseline_km - optimised_km
    co2_saved_kg = km_saved * 1.3
    baseline_minutes = _baseline_minutes(stops, baseline_m)
    optimised_minutes = max(solution.durations_minutes) if solution.durations_minutes else 0
    hours_saved = (baseline_minutes - optimised_minutes) / MINUTES_PER_HOUR
    trucks_used = sum(1 for route in solution.routes if route)

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        _metric_card("Kilometres saved", f"{km_saved:.1f} km", f"{baseline_km:.1f} to {optimised_km:.1f} km")
    with col2:
        _metric_card("CO2 saved", f"{co2_saved_kg:.1f} kg", "Based on 1.3 kg per km")
    with col3:
        _metric_card("Hours saved", f"{hours_saved:.1f} h", f"Baseline {_duration_label(int(baseline_minutes))}")
    with col4:
        _metric_card("Trucks used", str(trucks_used), f"{st.session_state.num_vehicles} available")


def _vehicle_table(solution: VrpSolution) -> pd.DataFrame:
    """Build the per-vehicle summary table."""
    rows = []
    tip_counts = {
        vehicle: sum(1 for tip in solution.tip_events if tip.vehicle == vehicle)
        for vehicle in range(len(solution.routes))
    }
    for vehicle, route in enumerate(solution.routes):
        load = solution.loads_kg[vehicle] if vehicle < len(solution.loads_kg) else 0
        full_pct = load / DEFAULT_VEHICLE_CAPACITY_KG * 100
        rows.append({
            "Vehicle": vehicle + 1,
            "Stops": len(route),
            "Distance km": round(solution.distances_m[vehicle] / METRES_PER_KM, 2),
            "Tip count": tip_counts.get(vehicle, 0),
            "Total time": _duration_label(solution.durations_minutes[vehicle]),
            "Full at end": f"{full_pct:.1f}%",
            "Finish time": format_minutes(solution.finish_clock_minutes[vehicle]),
        })
    return pd.DataFrame(rows)


def _breakdown_table(solution: VrpSolution, plan: BreakdownPlan) -> pd.DataFrame:
    """Build the original versus recovery route table."""
    rows = []
    for vehicle_id, original in enumerate(solution.routes):
        completed = plan.completed_stops if vehicle_id == plan.broken_truck else original
        reassigned = plan.reassigned_by_vehicle.get(vehicle_id, [])
        rows.append({
            "Vehicle": vehicle_id + 1,
            "Status": "Broken down" if vehicle_id == plan.broken_truck else "Active",
            "Original stops": len(original),
            "Completed before breakdown": len(completed) if vehicle_id == plan.broken_truck else len(original),
            "Reassigned stops taken": len(reassigned),
            "Final route": ", ".join(str(stop) for stop in plan.updated_routes[vehicle_id]),
        })
    return pd.DataFrame(rows)


def build_breakdown_recovery_report(solution: VrpSolution, plan: BreakdownPlan) -> tuple[str, pd.DataFrame]:
    """Build a plain-English recovery report from the actual recovery result."""
    broken_label = f"Truck {plan.broken_truck + 1}"
    active_labels = ", ".join(f"Truck {truck + 1}" for truck in plan.active_trucks) or "no trucks"
    recovery_km = 0.0
    recovery_co2 = 0.0
    recovery_minutes = 0
    if plan.recovery_solution is not None:
        recovery_km = plan.recovery_solution.total_distance_m / METRES_PER_KM
        recovery_co2 = plan.recovery_solution.total_co2_g / GRAMS_PER_KG
        recovery_minutes = max(plan.recovery_solution.durations_minutes) if plan.recovery_solution.durations_minutes else 0

    completed_text = ", ".join(str(stop) for stop in plan.completed_stops) or "none"
    unfinished_text = ", ".join(str(stop) for stop in plan.unfinished_stops) or "none"
    summary = (
        f"{broken_label} broke down after completing {len(plan.completed_stops)} stops. "
        f"Its remaining {len(plan.unfinished_stops)} unserved stops were reassigned to {active_labels}. "
        f"The recovery plan adds {recovery_km:.1f} km and {recovery_co2:.1f} kg CO2 for the unfinished work."
    )

    rows = []
    for truck, stops_taken in plan.reassigned_by_vehicle.items():
        if truck == plan.broken_truck:
            continue
        rows.append({
            "Truck": truck + 1,
            "Role": "Active recovery vehicle",
            "Stops absorbed": len(stops_taken),
            "Reassigned stop IDs": ", ".join(str(stop) for stop in stops_taken) or "none",
        })
    if not rows:
        rows.append({
            "Truck": plan.broken_truck + 1,
            "Role": "No recovery assignment",
            "Stops absorbed": 0,
            "Reassigned stop IDs": "none",
        })

    detail = pd.DataFrame(rows)
    markdown = (
        f"**Recovery summary**\n\n"
        f"{summary}\n\n"
        f"- Completed before breakdown: {completed_text}\n"
        f"- Became unserved: {unfinished_text}\n"
        f"- Recovery route duration estimate: {_duration_label(recovery_minutes)}\n"
        f"- Service maintained with {len(plan.active_trucks)} active vehicles"
    )
    return markdown, detail


def render_recovery_report(solution: VrpSolution, plan: BreakdownPlan) -> None:
    """Render the under-map operational recovery report."""
    markdown, detail = build_breakdown_recovery_report(solution, plan)
    st.markdown(markdown)
    st.dataframe(
        detail,
        use_container_width=True,
        hide_index=True,
        key="breakdown_recovery_report_table",
    )


def render_operational_summary(solution: VrpSolution) -> None:
    """Render a compact normal-plan explanation under the map."""
    truck_count = sum(1 for route in solution.routes if route)
    stop_count = sum(len(route) for route in solution.routes)
    st.markdown(
        f"**Operational summary**\n\n"
        f"The current plan serves {stop_count} synthetic stops using {truck_count} active trucks. "
        f"Routes include depot returns for tipping where the load threshold requires it."
    )


def _render_sidebar() -> None:
    """Render sidebar controls."""
    st.sidebar.header("Scenario setup")
    st.sidebar.caption("Choose the size and start time for the synthetic collection round.")
    st.sidebar.slider("Stops", MIN_NUM_STOPS, MAX_NUM_STOPS, key="num_stops")
    st.sidebar.slider("Vehicles", MIN_NUM_VEHICLES, MAX_NUM_VEHICLES, key="num_vehicles")
    st.sidebar.time_input("Departure time", key="departure_time_input")
    departure_time = st.session_state.departure_time_input
    st.session_state.departure = departure_time.hour * MINUTES_PER_HOUR + departure_time.minute
    st.sidebar.slider(
        "Tip threshold %",
        TIP_THRESHOLD_MIN_PERCENT,
        TIP_THRESHOLD_MAX_PERCENT,
        step=TIP_THRESHOLD_STEP_PERCENT,
        key="tip_threshold_percent",
    )
    st.sidebar.number_input("Seed", min_value=0, max_value=9999, step=1, key="seed")

    st.sidebar.header("Routing options")
    st.sidebar.caption("First road-graph load may take 10-30 seconds.")
    st.sidebar.toggle("Use real road network", key="use_osmnx")
    st.sidebar.toggle("Plan by geographic zones", key="use_zoning")
    st.sidebar.toggle("Avoid school-sensitive times", key="use_school_windows")
    st.sidebar.toggle("Apply peak traffic timing", key="use_peak_hours")

    st.sidebar.header("Disruption options")
    st.sidebar.selectbox(
        "Road closure scenario",
        list(ROAD_CLOSURE_SCENARIOS.keys()),
        key="closure_scenario",
    )
    st.sidebar.toggle("Enable breakdown simulation", key="enable_breakdown")
    route_count = len(st.session_state.solution.routes) if st.session_state.solution else DEFAULT_NUM_VEHICLES
    if int(st.session_state.broken_truck) > route_count:
        st.session_state.broken_truck = route_count
    st.sidebar.selectbox(
        "Truck out of service",
        list(range(1, max(1, route_count) + 1)),
        key="broken_truck",
    )
    max_completed = 1
    if st.session_state.solution and int(st.session_state.broken_truck) <= len(st.session_state.solution.routes):
        max_completed = max(0, len(st.session_state.solution.routes[int(st.session_state.broken_truck) - 1]))
    if int(st.session_state.breakdown_after) > max_completed:
        st.session_state.breakdown_after = max_completed
    st.sidebar.slider(
        "Breakdown after completed stops",
        0,
        max_completed,
        key="breakdown_after",
    )
    if st.session_state.enable_breakdown and st.sidebar.button("Run breakdown recovery", use_container_width=True):
        with st.spinner("Redistributing unfinished stops..."):
            _run_breakdown_current()

    st.sidebar.header("Demo helpers")
    if st.sidebar.button("Load stage demo preset", use_container_width=True):
        _apply_demo_preset()
    if st.sidebar.button("Optimise routes", type="primary", use_container_width=True):
        with st.spinner("Optimising routes..."):
            _solve_current()


def main() -> None:
    """Run the Streamlit app."""
    st.set_page_config(page_title="Hillingdon Routes", layout="wide")
    _init_state()
    _render_page_styles()

    _render_sidebar()
    _render_header()

    if st.session_state.solution is None:
        with st.spinner("Building first scenario..."):
            _solve_current()

    stops: pd.DataFrame = st.session_state.stops
    solution: VrpSolution = st.session_state.solution
    baseline_m = float(st.session_state.baseline_m)

    for message in st.session_state.warnings:
        st.warning(message)
    if st.session_state.closure_warning:
        st.warning(st.session_state.closure_warning)
    if st.session_state.matrix_source == "haversine":
        st.info("Using haversine distances. The demo still works without internet or an OSMnx cache.")

    _render_metrics(stops, solution, baseline_m)
    peak_zones_active, school_zones_active = _active_overlay_flags()
    if school_zones_active and not st.session_state.school_points:
        st.warning("School-zone overlay is active, but school data is not available.")

    normal_tab, disruption_tab, breakdown_tab = st.tabs([
        "Normal plan",
        "Closure scenario plan",
        "Breakdown recovery plan",
    ])

    with normal_tab:
        _render_section_intro(
            "Normal plan",
            "Optimised service routes for the current synthetic collection round.",
        )
        fmap, geometry_warnings = build_map(
            stops=stops,
            solution=solution,
            depot=(DEPOT_LAT, DEPOT_LNG),
            graph=st.session_state.active_graph if st.session_state.matrix_source == "osmnx" else None,
            closed_edges=st.session_state.closed_edges,
            school_adjacent_stop_ids=set(st.session_state.school_ids),
            geometry_cache=st.session_state.geometry_cache,
            peak_zones_active=peak_zones_active,
            school_zones_active=school_zones_active,
            school_points=st.session_state.school_points,
        )
        for message in sorted(set(geometry_warnings)):
            st.warning(message)
        _render_folium_map(fmap, "normal_plan_map")
        render_operational_summary(solution)

    with disruption_tab:
        if st.session_state.closure_scenario == NO_CLOSURE_LABEL:
            st.info("Select a closure scenario in the sidebar and optimise to show a disrupted route.")
        else:
            _render_section_intro(
                "Closure scenario plan",
                (
                    f"Active closure: {st.session_state.closure_scenario}. "
                    f"Closed segments: {len(st.session_state.closed_edges)}."
                ),
            )
            fmap, geometry_warnings = build_map(
                stops=stops,
                solution=solution,
                depot=(DEPOT_LAT, DEPOT_LNG),
                graph=st.session_state.active_graph if st.session_state.matrix_source == "osmnx" else None,
                closed_edges=st.session_state.closed_edges,
                school_adjacent_stop_ids=set(st.session_state.school_ids),
                geometry_cache=st.session_state.geometry_cache,
                peak_zones_active=peak_zones_active,
                school_zones_active=school_zones_active,
                school_points=st.session_state.school_points,
            )
            for message in sorted(set(geometry_warnings)):
                st.warning(message)
            _render_folium_map(fmap, "closure_plan_map")
            render_operational_summary(solution)

    with breakdown_tab:
        plan: Optional[BreakdownPlan] = st.session_state.breakdown_plan
        if not st.session_state.enable_breakdown:
            st.info("Enable breakdown simulation in the sidebar to model recovery.")
        elif plan is None:
            st.info("Choose a truck and breakdown point, then run breakdown recovery.")
        else:
            if plan.warning:
                st.warning(plan.warning)
            _render_section_intro(
                "Breakdown recovery plan",
                f"Truck {plan.broken_truck + 1} is out of service and unfinished stops are reassigned.",
            )
            fmap, geometry_warnings = build_map(
                stops=stops,
                solution=solution,
                depot=(DEPOT_LAT, DEPOT_LNG),
                graph=st.session_state.active_graph if st.session_state.matrix_source == "osmnx" else None,
                closed_edges=st.session_state.closed_edges,
                school_adjacent_stop_ids=set(st.session_state.school_ids),
                route_override=plan.updated_routes,
                breakdown_plan=plan,
                geometry_cache=st.session_state.geometry_cache,
                peak_zones_active=peak_zones_active,
                school_zones_active=school_zones_active,
                school_points=st.session_state.school_points,
            )
            for message in sorted(set(geometry_warnings)):
                st.warning(message)
            _render_folium_map(fmap, "breakdown_plan_map")
            cols = st.columns(4)
            cols[0].metric("Broken truck", f"Truck {plan.broken_truck + 1}")
            cols[1].metric("Completed first", len(plan.completed_stops))
            cols[2].metric("Reassigned stops", len(plan.unfinished_stops))
            recovery_km = 0.0 if plan.recovery_solution is None else plan.recovery_solution.total_distance_m / METRES_PER_KM
            cols[3].metric("Recovery distance", f"{recovery_km:.1f} km")
            st.dataframe(
                _breakdown_table(solution, plan),
                use_container_width=True,
                hide_index=True,
                key="breakdown_summary_table",
            )
            render_recovery_report(solution, plan)

    col1, col2 = st.columns([2, 1])
    with col1:
        _render_section_intro(
            "Vehicle plan",
            "Per-truck distance, loading, tipping, and finish-time summary.",
        )
        st.dataframe(
            _vehicle_table(solution),
            use_container_width=True,
            hide_index=True,
            key="vehicle_plan_table",
        )
    with col2:
        _render_comparison_panel(solution, baseline_m)


if __name__ == "__main__":
    main()
