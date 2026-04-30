"""Streamlit UI for the Hillingdon route optimisation demo."""

from __future__ import annotations

import html
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
    VEHICLE_COLOURS,
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


def _escape(value: Any) -> str:
    """Escape dynamic values before inserting them into HTML."""
    return html.escape(str(value), quote=True)


def _metric_card(label: str, value: str, detail: str, icon: str, delta: str) -> None:
    """Render a premium KPI card."""
    st.markdown(
        f"""
        <div class="metric-card">
            <div class="metric-shine"></div>
            <div class="metric-topline">
                <span class="metric-icon">{_escape(icon)}</span>
                <span class="metric-delta">{_escape(delta)}</span>
            </div>
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
            --route-bg: #050606;
            --route-panel: #101112;
            --route-panel-2: #171819;
            --route-ink: #f8fafc;
            --route-muted: #9aa4af;
            --route-line: #2b2f33;
            --route-cyan: #2df2e6;
            --route-orange: #ff8a1f;
            --route-green: #31d97a;
            --route-red: #ff5757;
        }

        .stApp {
            background:
                radial-gradient(circle at 12% 18%, rgba(45, 242, 230, 0.15), transparent 22rem),
                radial-gradient(circle at 82% 6%, rgba(255, 138, 31, 0.14), transparent 26rem),
                linear-gradient(180deg, #020303 0%, #070909 44%, #050606 100%);
            color: var(--route-ink);
        }

        [data-testid="stHeader"] {
            background: rgba(5, 6, 6, 0.82);
            backdrop-filter: blur(12px);
            border-bottom: 1px solid rgba(45, 242, 230, 0.12);
        }

        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #090a0b, #050606);
            border-right: 1px solid var(--route-line);
        }

        [data-testid="stSidebar"] * {
            color: #e5e7eb;
        }

        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h2,
        [data-testid="stSidebar"] [data-testid="stMarkdownContainer"] h3 {
            color: var(--route-ink);
            letter-spacing: 0;
        }

        .block-container {
            max-width: 1500px;
            padding-top: 1.5rem;
            padding-bottom: 3rem;
        }

        .route-hero {
            position: relative;
            overflow: hidden;
            border: 1px solid rgba(45, 242, 230, 0.24);
            border-radius: 8px;
            padding: 26px 30px;
            margin-bottom: 18px;
            background:
                linear-gradient(135deg, rgba(16, 17, 18, 0.98), rgba(10, 12, 12, 0.98)),
                repeating-linear-gradient(135deg, rgba(45, 242, 230, 0.05) 0 1px, transparent 1px 20px);
            box-shadow: 0 22px 60px rgba(0, 0, 0, 0.45);
        }

        .route-hero::after {
            content: "";
            position: absolute;
            inset: 0 0 auto auto;
            width: 45%;
            height: 100%;
            background:
                linear-gradient(130deg, transparent 0 25%, rgba(45, 242, 230, 0.14) 26% 27%, transparent 28%),
                radial-gradient(circle at 75% 38%, rgba(255, 138, 31, 0.2), transparent 12rem);
            opacity: 0.85;
        }

        .route-hero::before {
            content: "";
            position: absolute;
            left: 0;
            top: 0;
            width: 4px;
            height: 100%;
            background: linear-gradient(180deg, var(--route-cyan), var(--route-orange));
        }

        .hero-eyebrow {
            position: relative;
            z-index: 1;
            color: var(--route-cyan);
            font-size: 0.76rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        .hero-title {
            position: relative;
            z-index: 1;
            color: var(--route-ink);
            font-size: 2.65rem;
            font-weight: 800;
            line-height: 1.02;
            margin: 0;
            letter-spacing: 0;
        }

        .hero-copy {
            position: relative;
            z-index: 1;
            color: #b7c0c8;
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
            border: 1px solid rgba(45, 242, 230, 0.22);
            border-radius: 8px;
            background: rgba(18, 20, 21, 0.82);
            color: #e5fafa;
            font-size: 0.78rem;
            font-weight: 700;
            box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.05);
        }

        .status-chip.active {
            border-color: rgba(49, 217, 122, 0.34);
            color: #dfffea;
            background: rgba(49, 217, 122, 0.1);
        }

        .status-chip.warn {
            border-color: rgba(255, 138, 31, 0.36);
            color: #ffe7c7;
            background: rgba(255, 138, 31, 0.11);
        }

        .sidebar-brand {
            border: 1px solid rgba(45, 242, 230, 0.22);
            border-radius: 8px;
            padding: 16px;
            margin: 4px 0 18px;
            background:
                linear-gradient(140deg, rgba(45, 242, 230, 0.11), transparent 44%),
                linear-gradient(180deg, rgba(22, 24, 25, 0.96), rgba(10, 11, 12, 0.98));
            box-shadow: 0 16px 34px rgba(0, 0, 0, 0.36);
        }

        .brand-mark {
            display: inline-grid;
            place-items: center;
            width: 38px;
            height: 38px;
            border-radius: 8px;
            margin-bottom: 12px;
            background: linear-gradient(135deg, var(--route-cyan), #14b8a6);
            color: #041011;
            font-weight: 900;
            letter-spacing: 0;
        }

        .brand-title {
            color: var(--route-ink);
            font-size: 1.05rem;
            font-weight: 850;
            line-height: 1.15;
        }

        .brand-subtitle {
            color: var(--route-muted);
            font-size: 0.78rem;
            margin-top: 5px;
            line-height: 1.4;
        }

        .control-section {
            border-top: 1px solid #24282c;
            margin: 16px 0 8px;
            padding-top: 14px;
        }

        .control-kicker {
            color: var(--route-cyan);
            font-size: 0.72rem;
            font-weight: 850;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .control-helper {
            color: #87919b;
            font-size: 0.78rem;
            margin-top: 4px;
        }

        .metric-card {
            position: relative;
            overflow: hidden;
            min-height: 128px;
            border: 1px solid rgba(45, 242, 230, 0.18);
            border-radius: 8px;
            padding: 16px 18px;
            background:
                linear-gradient(180deg, rgba(24, 25, 26, 0.98), rgba(12, 13, 14, 0.98));
            box-shadow: 0 14px 34px rgba(0, 0, 0, 0.36);
        }

        .metric-topline {
            position: relative;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin-bottom: 12px;
        }

        .metric-icon {
            display: inline-grid;
            place-items: center;
            width: 34px;
            height: 34px;
            border: 1px solid rgba(45, 242, 230, 0.22);
            border-radius: 8px;
            background: rgba(45, 242, 230, 0.1);
            color: var(--route-cyan);
            font-size: 0.95rem;
            font-weight: 900;
        }

        .metric-delta {
            border: 1px solid rgba(49, 217, 122, 0.22);
            border-radius: 8px;
            padding: 4px 8px;
            background: rgba(49, 217, 122, 0.08);
            color: #b9ffd1;
            font-size: 0.72rem;
            font-weight: 800;
            white-space: nowrap;
        }

        .metric-card::before {
            content: "";
            position: absolute;
            inset: 0 auto 0 0;
            width: 4px;
            background: linear-gradient(180deg, var(--route-cyan), var(--route-orange));
        }

        .metric-shine {
            position: absolute;
            inset: 0 0 auto auto;
            width: 112px;
            height: 112px;
            background: radial-gradient(circle, rgba(45, 242, 230, 0.15), transparent 68%);
        }

        .metric-label {
            position: relative;
            color: #9aa4af;
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
            border: 1px solid rgba(45, 242, 230, 0.2);
            border-radius: 8px;
            overflow: hidden;
            background: #080909;
            box-shadow: 0 18px 44px rgba(0, 0, 0, 0.42);
        }

        .section-panel {
            border: 1px solid rgba(45, 242, 230, 0.15);
            border-radius: 8px;
            padding: 16px 18px;
            background:
                linear-gradient(180deg, rgba(23, 24, 25, 0.96), rgba(13, 14, 15, 0.96));
            box-shadow: 0 14px 34px rgba(0, 0, 0, 0.34);
        }

        .panel-head {
            display: flex;
            align-items: flex-start;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 12px;
        }

        .panel-kicker {
            color: var(--route-cyan);
            font-size: 0.7rem;
            font-weight: 850;
            letter-spacing: 0.08em;
            text-transform: uppercase;
            margin-bottom: 4px;
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

        .ops-toolbar {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            flex-wrap: wrap;
            border: 1px solid rgba(45, 242, 230, 0.14);
            border-radius: 8px;
            padding: 12px 14px;
            margin-bottom: 12px;
            background: linear-gradient(180deg, rgba(17, 18, 19, 0.94), rgba(10, 11, 12, 0.96));
        }

        .legend-chip-row {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .legend-chip {
            display: inline-flex;
            align-items: center;
            gap: 7px;
            border: 1px solid #292e33;
            border-radius: 8px;
            padding: 6px 9px;
            color: #cbd5df;
            font-size: 0.76rem;
            font-weight: 750;
            background: rgba(255, 255, 255, 0.03);
        }

        .legend-dot {
            width: 9px;
            height: 9px;
            border-radius: 50%;
            box-shadow: 0 0 12px currentColor;
        }

        .route-card {
            border: 1px solid #2a2f33;
            border-radius: 8px;
            padding: 12px;
            margin-bottom: 10px;
            background: linear-gradient(180deg, rgba(24, 25, 26, 0.94), rgba(12, 13, 14, 0.94));
        }

        .route-card-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
            margin-bottom: 8px;
        }

        .route-name {
            display: flex;
            align-items: center;
            gap: 8px;
            color: #f8fafc;
            font-weight: 850;
        }

        .route-swatch {
            width: 11px;
            height: 11px;
            border-radius: 3px;
            box-shadow: 0 0 14px currentColor;
        }

        .route-status {
            border-radius: 8px;
            padding: 4px 7px;
            background: rgba(49, 217, 122, 0.1);
            color: #9effbd;
            font-size: 0.68rem;
            font-weight: 850;
            letter-spacing: 0.04em;
        }

        .route-card-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 8px;
        }

        .route-stat span,
        .insight-row span {
            display: block;
            color: #7e8993;
            font-size: 0.68rem;
            font-weight: 750;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .route-stat strong,
        .insight-row strong {
            display: block;
            color: #f8fafc;
            font-size: 0.88rem;
            margin-top: 2px;
        }

        .insight-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            border-bottom: 1px solid #282c30;
            padding: 10px 0;
        }

        .insight-row:last-child {
            border-bottom: 0;
        }

        .incident-panel {
            border: 1px solid rgba(255, 87, 87, 0.32);
            border-radius: 8px;
            padding: 16px 18px;
            margin-bottom: 12px;
            background:
                linear-gradient(135deg, rgba(255, 87, 87, 0.14), transparent 36%),
                linear-gradient(180deg, rgba(23, 17, 17, 0.98), rgba(12, 13, 14, 0.98));
            box-shadow: 0 16px 36px rgba(0, 0, 0, 0.38);
        }

        .incident-title {
            color: #fff1f1;
            font-size: 1.05rem;
            font-weight: 850;
            margin-bottom: 4px;
        }

        .incident-copy {
            color: #ffc7c7;
            font-size: 0.88rem;
            line-height: 1.48;
        }

        .incident-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin-top: 14px;
        }

        .incident-stat {
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 8px;
            padding: 10px;
            background: rgba(0, 0, 0, 0.22);
        }

        .incident-stat span {
            color: #aab3bc;
            font-size: 0.68rem;
            font-weight: 800;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }

        .incident-stat strong {
            display: block;
            color: #ffffff;
            font-size: 1.2rem;
            margin-top: 2px;
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
            border-bottom: 1px solid #282c30;
            padding: 8px 0;
            color: #aab3bc;
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
            border-bottom: 1px solid #24282c;
        }

        .stTabs [data-baseweb="tab"] {
            border-radius: 8px 8px 0 0;
            padding: 10px 14px;
            background: rgba(14, 15, 16, 0.92);
            border: 1px solid #25292d;
            border-bottom: 0;
            color: #9aa4af;
            font-weight: 700;
        }

        .stTabs [aria-selected="true"] {
            background: #151719;
            color: var(--route-cyan);
            box-shadow: 0 -2px 16px rgba(45, 242, 230, 0.08);
        }

        [data-testid="stDataFrame"] {
            border: 1px solid rgba(45, 242, 230, 0.15);
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.32);
        }

        div[data-testid="stAlert"] {
            border-radius: 8px;
            border: 1px solid rgba(45, 242, 230, 0.14);
            background: #111315;
            color: #e5e7eb;
        }

        .stButton > button {
            border-radius: 8px;
            font-weight: 800;
            min-height: 2.65rem;
            background: #17191b;
            border: 1px solid #2a2f33;
            color: #f8fafc;
        }

        .stButton > button[kind="primary"] {
            background: linear-gradient(135deg, var(--route-cyan), #15b8c5);
            border: 0;
            color: #041011;
            box-shadow: 0 10px 26px rgba(45, 242, 230, 0.24);
        }

        [data-testid="stMetric"] {
            background: linear-gradient(180deg, #171819, #0f1011);
            border: 1px solid rgba(45, 242, 230, 0.14);
            border-radius: 8px;
            padding: 12px 14px;
        }

        [data-testid="stWidgetLabel"] p,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stCaptionContainer"] {
            color: #aab3bc;
        }

        input,
        textarea,
        [data-baseweb="select"] > div {
            background-color: #171819;
            border-color: #30353a;
            color: #f8fafc;
        }

        [data-testid="stSlider"] [role="slider"] {
            background: var(--route-cyan);
            border-color: var(--route-cyan);
        }

        hr {
            border-color: #24282c;
        }

        @media (max-width: 760px) {
            .hero-title {
                font-size: 2rem;
            }

            .route-hero {
                padding: 22px 20px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _chip(label: str, active: bool = False, warn: bool = False) -> str:
    """Return a status chip."""
    modifier = " active" if active else " warn" if warn else ""
    return f'<span class="status-chip{modifier}">{_escape(label)}</span>'


def _render_sidebar_brand() -> None:
    """Render the sidebar product identity."""
    st.sidebar.markdown(
        """
        <div class="sidebar-brand">
            <div class="brand-mark">RW</div>
            <div class="brand-title">RouteWise Hillingdon</div>
            <div class="brand-subtitle">Waste Operations Console<br>Live Planning Dashboard</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _sidebar_section(title: str, helper: str) -> None:
    """Render a sidebar control section heading."""
    st.sidebar.markdown(
        f"""
        <div class="control-section">
            <div class="control-kicker">{_escape(title)}</div>
            <div class="control-helper">{_escape(helper)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_header() -> None:
    """Render the dashboard header."""
    closure = st.session_state.closure_scenario != NO_CLOSURE_LABEL
    roads_label = "Road graph requested" if st.session_state.use_osmnx else "Offline distance mode"
    recovery_label = "Recovery active" if st.session_state.breakdown_plan else "Recovery standby"
    chips = "".join([
        _chip("Synthetic demo mode", active=True),
        _chip(roads_label, active=bool(st.session_state.use_osmnx)),
        _chip("Closure active" if closure else "No closure", warn=closure),
        _chip(recovery_label, warn=bool(st.session_state.breakdown_plan)),
    ])
    st.markdown(
        f"""
        <section class="route-hero">
            <div class="hero-eyebrow">RouteWise Hillingdon / Live Planning Dashboard</div>
            <h1 class="hero-title">Waste operations console</h1>
            <div class="hero-copy">
                Premium dispatch view for synthetic collection planning, routing performance,
                road closures, school-window sensitivity, and incident recovery.
            </div>
            <div class="hero-chip-row">
                {chips}
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_map_toolbar(title: str, subtitle: str, solution: VrpSolution) -> None:
    """Render the command bar above the map."""
    active_routes = sum(1 for route in solution.routes if route)
    source = "OSMnx road graph" if st.session_state.matrix_source == "osmnx" else "Haversine fallback"
    st.markdown(
        f"""
        <div class="ops-toolbar">
            <div>
                <div class="panel-kicker">Live planning reference</div>
                <div class="section-title">{_escape(title)}</div>
                <div class="section-copy">{_escape(subtitle)}</div>
            </div>
            <div class="legend-chip-row">
                <span class="legend-chip"><span class="legend-dot" style="color:#2df2e6;background:#2df2e6;"></span>{active_routes} active routes</span>
                <span class="legend-chip"><span class="legend-dot" style="color:#ff5757;background:#ff5757;"></span>Closure</span>
                <span class="legend-chip"><span class="legend-dot" style="color:#facc15;background:#facc15;"></span>School zone</span>
                <span class="legend-chip"><span class="legend-dot" style="color:#ff8a1f;background:#ff8a1f;"></span>{_escape(source)}</span>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _route_cards(solution: VrpSolution) -> str:
    """Build vehicle summary cards."""
    cards = []
    for vehicle_id, route in enumerate(solution.routes):
        colour = VEHICLE_COLOURS[vehicle_id % len(VEHICLE_COLOURS)]
        status = "ACTIVE" if route else "STANDBY"
        cards.append(
            f"""
            <div class="route-card">
                <div class="route-card-top">
                    <div class="route-name"><span class="route-swatch" style="color:{colour};background:{colour};"></span>Truck {vehicle_id + 1}</div>
                    <div class="route-status">{status}</div>
                </div>
                <div class="route-card-grid">
                    <div class="route-stat"><span>Stops</span><strong>{len(route)}</strong></div>
                    <div class="route-stat"><span>Distance</span><strong>{solution.distances_m[vehicle_id] / METRES_PER_KM:.1f} km</strong></div>
                    <div class="route-stat"><span>Finish</span><strong>{format_minutes(solution.finish_clock_minutes[vehicle_id])}</strong></div>
                </div>
            </div>
            """
        )
    return "".join(cards)


def _render_vehicle_summary_panel(solution: VrpSolution) -> None:
    """Render right-side vehicle operations panel."""
    st.markdown(
        f"""
        <div class="section-panel">
            <div class="panel-head">
                <div>
                    <div class="panel-kicker">Fleet allocation</div>
                    <div class="section-title">Active routes</div>
                    <div class="section-copy">Per-vehicle route loadout and ETA snapshot.</div>
                </div>
                {_chip("Optimised", active=True)}
            </div>
            {_route_cards(solution)}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_scenario_insights_panel(
    peak_active: bool,
    school_active: bool,
) -> None:
    """Render scenario state and planning insights."""
    closure_name = st.session_state.closure_scenario
    closure_active = closure_name != NO_CLOSURE_LABEL
    breakdown_active = bool(st.session_state.breakdown_plan)
    rows = [
        ("Road mode", "Road graph" if st.session_state.matrix_source == "osmnx" else "Fallback", st.session_state.matrix_source == "osmnx"),
        ("Closure", closure_name if closure_active else "None active", closure_active),
        ("Peak traffic", "Active window" if peak_active else "Inactive", peak_active),
        ("School overlay", "Active window" if school_active else "Inactive", school_active),
        ("Recovery", "Incident plan ready" if breakdown_active else "Standby", breakdown_active),
    ]
    body = "".join(
        f"""
        <div class="insight-row">
            <div><span>{_escape(label)}</span><strong>{_escape(value)}</strong></div>
            {_chip("Active", active=True) if active else _chip("Ready")}
        </div>
        """
        for label, value, active in rows
    )
    st.markdown(
        f"""
        <div class="section-panel">
            <div class="panel-kicker">Scenario intelligence</div>
            <div class="section-title">Planning conditions</div>
            <div class="section-copy">Live switches that influence the current dispatch view.</div>
            {body}
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_incident_panel(plan: BreakdownPlan) -> None:
    """Render a premium incident response summary."""
    absorbed = sum(len(stops) for truck, stops in plan.reassigned_by_vehicle.items() if truck != plan.broken_truck)
    recovery_km = 0.0 if plan.recovery_solution is None else plan.recovery_solution.total_distance_m / METRES_PER_KM
    st.markdown(
        f"""
        <div class="incident-panel">
            <div class="panel-kicker">Incident response</div>
            <div class="incident-title">Truck {plan.broken_truck + 1} breakdown recovery</div>
            <div class="incident-copy">
                Truck {plan.broken_truck + 1} completed {len(plan.completed_stops)} stops before going out of service.
                Remaining work has been redistributed across {len(plan.active_trucks)} active trucks.
            </div>
            <div class="incident-grid">
                <div class="incident-stat"><span>Unserved stops</span><strong>{len(plan.unfinished_stops)}</strong></div>
                <div class="incident-stat"><span>Absorbed stops</span><strong>{absorbed}</strong></div>
                <div class="incident-stat"><span>Recovery distance</span><strong>{recovery_km:.1f} km</strong></div>
            </div>
        </div>
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
        _metric_card(
            "Kilometres saved",
            f"{km_saved:.1f} km",
            f"{baseline_km:.1f} to {optimised_km:.1f} km",
            "KM",
            "Route delta",
        )
    with col2:
        _metric_card("CO2 saved", f"{co2_saved_kg:.1f} kg", "Based on 1.3 kg per km", "CO2", "Lower emissions")
    with col3:
        _metric_card(
            "Hours saved",
            f"{hours_saved:.1f} h",
            f"Baseline {_duration_label(int(baseline_minutes))}",
            "HR",
            "Shift impact",
        )
    with col4:
        _metric_card("Trucks used", str(trucks_used), f"{st.session_state.num_vehicles} available", "RCV", "Fleet active")


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
    st.markdown(
        f"""
        <div class="section-panel">
            <div class="panel-kicker">Recovery report</div>
            <div class="section-title">Redistribution plan</div>
            <div class="section-copy">{_escape(markdown.replace("**", "").replace(chr(10), " "))}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
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
        f"""
        <div class="section-panel">
            <div class="panel-kicker">Operational note</div>
            <div class="section-title">Current dispatch summary</div>
            <div class="section-copy">
                The current plan serves <strong>{stop_count}</strong> synthetic stops using
                <strong>{truck_count}</strong> active trucks. Routes include depot returns
                for tipping where the load threshold requires it.
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_sidebar() -> None:
    """Render sidebar controls."""
    _render_sidebar_brand()
    _sidebar_section("Scenario", "Set round size, start time, tipping threshold, and repeatable seed.")
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

    _sidebar_section("Routing", "Toggle road graph, zoning, school-window, and peak-traffic behaviour.")
    st.sidebar.toggle("Use real road network", key="use_osmnx")
    st.sidebar.toggle("Plan by geographic zones", key="use_zoning")
    st.sidebar.toggle("Avoid school-sensitive times", key="use_school_windows")
    st.sidebar.toggle("Apply peak traffic timing", key="use_peak_hours")

    _sidebar_section("Disruptions", "Model closures and vehicle recovery without changing the base data.")
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

    _sidebar_section("Demo tools", "Use preset values for a reliable judging walkthrough.")
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
        _render_map_toolbar(
            "Live route map",
            "Optimised service routes for the current synthetic collection round.",
            solution,
        )
        map_col, ops_col = st.columns([3, 1], gap="medium")
        with map_col:
            _render_folium_map(fmap, "normal_plan_map")
        with ops_col:
            _render_vehicle_summary_panel(solution)
            _render_scenario_insights_panel(peak_zones_active, school_zones_active)
        render_operational_summary(solution)

    with disruption_tab:
        if st.session_state.closure_scenario == NO_CLOSURE_LABEL:
            _render_section_intro(
                "Closure scenario plan",
                "Select a closure scenario in the sidebar and optimise to show a disrupted route.",
            )
        else:
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
            _render_map_toolbar(
                "Closure scenario map",
                (
                    f"Active closure: {st.session_state.closure_scenario}. "
                    f"Closed segments: {len(st.session_state.closed_edges)}."
                ),
                solution,
            )
            map_col, ops_col = st.columns([3, 1], gap="medium")
            with map_col:
                _render_folium_map(fmap, "closure_plan_map")
            with ops_col:
                _render_scenario_insights_panel(peak_zones_active, school_zones_active)
                _render_vehicle_summary_panel(solution)
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
            _render_map_toolbar(
                "Breakdown recovery map",
                f"Truck {plan.broken_truck + 1} is out of service and unfinished stops are reassigned.",
                solution,
            )
            map_col, ops_col = st.columns([3, 1], gap="medium")
            with map_col:
                _render_folium_map(fmap, "breakdown_plan_map")
            with ops_col:
                _render_incident_panel(plan)
                _render_scenario_insights_panel(peak_zones_active, school_zones_active)
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
