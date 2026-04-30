"""Central configuration for the Hillingdon route optimiser.

All tunable constants live here so the solver, generator, and UI never carry
magic numbers. British spelling throughout.
"""

# Depot
DEPOT_LAT: float = 51.6055
DEPOT_LNG: float = -0.4750
DEPOT_NAME: str = "Harefield HWRC"

# Hillingdon bounding box (rough envelope around the borough)
HILLINGDON_BBOX = {
    "north": 51.620,
    "south": 51.500,
    "east": -0.400,
    "west": -0.520,
}

# Vehicle defaults
DEFAULT_VEHICLE_CAPACITY_KG: int = 10000
TIP_THRESHOLD: float = 0.8
# 26-tonne RCV running a kerbside collection round, well above a saloon car
CO2_GRAMS_PER_KM: int = 1300

# Shift
SHIFT_HOURS: int = 8
SHIFT_MINUTES: int = SHIFT_HOURS * 60
LUNCH_MINUTES: int = 30

# Time-of-day windows expressed as minutes from midnight
PEAK_HOUR_WINDOWS = [
    (7 * 60, 9 * 60 + 30),     # 07:00 to 09:30
    (16 * 60, 18 * 60 + 30),   # 16:00 to 18:30
]
PEAK_HOUR_MULTIPLIER: float = 1.4

SCHOOL_WINDOWS = [
    (8 * 60 + 30, 9 * 60),     # 08:30 to 09:00 drop-off
    (15 * 60, 15 * 60 + 30),   # 15:00 to 15:30 pick-up
]
SCHOOL_OVERLAY_WINDOWS = [
    (8 * 60, 9 * 60 + 15),     # 08:00 to 09:15 drop-off
    (14 * 60 + 45, 16 * 60 + 15),  # 14:45 to 16:15 pick-up
]
SCHOOL_PROXIMITY_METRES: int = 100
SCHOOL_ZONE_RADIUS_METRES: int = 120
SCHOOL_ZONE_MAX_MARKERS: int = 60

# Ward centres with relative weights for synthetic stop density.
# Hayes and Yiewsley sit in the dense south of the borough.
# Harefield and Ickenham are the lower-density north and west.
WARDS = {
    "Hayes":     {"lat": 51.510, "lng": -0.420, "weight": 3.0},
    "Yiewsley":  {"lat": 51.515, "lng": -0.467, "weight": 3.0},
    "Uxbridge":  {"lat": 51.546, "lng": -0.478, "weight": 2.0},
    "Ruislip":   {"lat": 51.572, "lng": -0.421, "weight": 2.0},
    "Harefield": {"lat": 51.600, "lng": -0.483, "weight": 1.0},
    "Ickenham":  {"lat": 51.563, "lng": -0.444, "weight": 1.0},
}

# Roughly 900 m at this latitude, so cluster spread stays inside ward bounds
WARD_SPREAD_DEGREES: float = 0.008

# Demand sampling
DEMAND_KG_MIN: int = 8
DEMAND_KG_MAX: int = 18
SERVICE_MINUTES_MIN: int = 1
SERVICE_MINUTES_MAX: int = 3

# Solver
SOLVER_TIME_LIMIT_SECONDS: int = 5
ZONED_SOLVER_TIME_LIMIT_SECONDS: int = 2
MAX_DISTANCE_METRES: int = 10_000_000
DISTANCE_SPAN_COST_COEFFICIENT: int = 100
CLOCK_DAY_MINUTES: int = 24 * 60

# Travel speed assumed by the time dimension. Urban kerbside collection
# averages well below the limit because of stops, manoeuvres, and traffic.
AVERAGE_SPEED_KMH: int = 30

# Soft penalty (per minute over the 08:30 bound) for school-adjacent stops.
# Biases the solver towards serving them before the morning drop-off window.
SCHOOL_PENALTY_PER_MINUTE: int = 50

# Vehicle types catalogue (extensible for later phases)
VEHICLE_TYPES = {
    "26t_rcv": {
        "name": "26-tonne RCV",
        "capacity_kg": 10000,
        "co2_g_per_km": 1300,
    },
    "7_5t_satellite": {
        "name": "7.5-tonne satellite",
        "capacity_kg": 3500,
        "co2_g_per_km": 700,
    },
}

# UI defaults
DEFAULT_NUM_STOPS: int = 50
DEFAULT_NUM_VEHICLES: int = 3
DEFAULT_SEED: int = 42
DEFAULT_DEPARTURE_MINUTES: int = 7 * 60  # 07:00 start
MIN_NUM_STOPS: int = 10
MAX_NUM_STOPS: int = 80
MIN_NUM_VEHICLES: int = 1
MAX_NUM_VEHICLES: int = 5
TIP_THRESHOLD_MIN_PERCENT: int = 50
TIP_THRESHOLD_MAX_PERCENT: int = 95
TIP_THRESHOLD_STEP_PERCENT: int = 5
MAP_HEIGHT_PIXELS: int = 620
ROAD_CLOSURE_SEARCH_RADIUS_METRES: int = 90
ROAD_CLOSURE_EDGE_COUNT: int = 4
ROAD_CLOSURE_MARKER_RADIUS: int = 8
DEFAULT_ZOOM: int = 12
DEMO_PRESET_STOPS: int = 50
DEMO_PRESET_VEHICLES: int = 3
DEMO_PRESET_SEED: int = 42
METRES_PER_KM: int = 1000
GRAMS_PER_KG: int = 1000
MINUTES_PER_HOUR: int = 60
EMPTY_ROUTE_FINISH_LOAD_PERCENT: int = 0

# Map colours
VEHICLE_COLOURS = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#f97316",
]
WARD_COLOURS = {
    "Hayes": "#0f766e",
    "Yiewsley": "#0369a1",
    "Uxbridge": "#7c3aed",
    "Ruislip": "#c2410c",
    "Harefield": "#15803d",
    "Ickenham": "#be123c",
}
DEFAULT_STOP_COLOUR: str = "#475569"
DEPOT_ICON_COLOUR: str = "green"
TIP_ICON_COLOUR: str = "orange"
ROAD_CLOSURE_COLOUR: str = "#111827"
ROAD_CLOSURE_LINE_COLOUR: str = "#dc2626"
BROKEN_TRUCK_COLOUR: str = "#111827"
BREAKDOWN_REASSIGN_TIME_LIMIT_SECONDS: int = 2
PEAK_TRAFFIC_ZONE_COLOUR: str = "#dc2626"
PEAK_TRAFFIC_ZONE_FILL_OPACITY: float = 0.14
SCHOOL_ZONE_COLOUR: str = "#facc15"
SCHOOL_ZONE_FILL_OPACITY: float = 0.16

# Visual traffic zones for the map overlay. The travel-time model uses a
# borough-wide peak multiplier, so these circles are an explainable visual
# approximation of likely congestion areas.
PEAK_TRAFFIC_ZONES = [
    {
        "name": "Uxbridge town centre peak traffic zone",
        "lat": 51.5460,
        "lng": -0.4780,
        "radius_m": 950,
    },
    {
        "name": "Hayes corridor peak traffic zone",
        "lat": 51.5105,
        "lng": -0.4200,
        "radius_m": 850,
    },
    {
        "name": "Ruislip high street peak traffic zone",
        "lat": 51.5720,
        "lng": -0.4210,
        "radius_m": 750,
    },
]

# Deterministic closure presets for stage-safe demos.
NO_CLOSURE_LABEL: str = "No closure"
ROAD_CLOSURE_SCENARIOS = {
    NO_CLOSURE_LABEL: None,
    "School access road closure": {
        "lat": 51.5120,
        "lng": -0.4200,
        "description": "A short access disruption near the Hayes school cluster.",
    },
    "Town centre roadworks": {
        "lat": 51.5460,
        "lng": -0.4780,
        "description": "Roadworks around central Uxbridge.",
    },
    "Depot exit disruption": {
        "lat": 51.6055,
        "lng": -0.4750,
        "description": "A disruption close to Harefield HWRC.",
    },
    "Ruislip high street closure": {
        "lat": 51.5720,
        "lng": -0.4210,
        "description": "A local closure in the Ruislip cluster.",
    },
}

# Graph and feature caches (ignored by git, created on first download)
GRAPH_CACHE_PATH: str = "cache/hillingdon_graph.pkl"
SCHOOLS_CACHE_PATH: str = "cache/hillingdon_schools.pkl"
