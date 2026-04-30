"""
Hillingdon Hackathon - Challenge 02: Route Planning
Complete Data Generator — All 3 Vehicle Types
--------------------------------------------------------------
Generates separate datasets for:
  1. Waste Collection (refuse trucks)
  2. Scarab Sweepers (street cleaning)
  3. Solo Operatives (inspections)

Run: python generate_all_data.py
Output: data_waste.json, data_sweeper.json, data_inspections.json
"""

import json
import random
import math

random.seed(42)

# ═══════════════════════════════════════════════════════════════
# SHARED DEPOT
# ═══════════════════════════════════════════════════════════════

DEPOT = {
    "id": "DEPOT",
    "name": "New Years Green Lane Depot",
    "lat": 51.5535,
    "lng": -0.4415,
    "type": "depot"
}

# ═══════════════════════════════════════════════════════════════
# HILLINGDON LOCATIONS (real streets, grouped by area)
# ═══════════════════════════════════════════════════════════════

LOCATIONS_POOL = [
    # Uxbridge
    {"name": "High St, Uxbridge",              "lat": 51.5465, "lng": -0.4783, "area": "Uxbridge"},
    {"name": "Hillingdon Rd, Uxbridge",         "lat": 51.5390, "lng": -0.4620, "area": "Uxbridge"},
    {"name": "Cowley Rd, Uxbridge",             "lat": 51.5340, "lng": -0.4700, "area": "Cowley"},
    {"name": "Park Rd, Uxbridge",               "lat": 51.5420, "lng": -0.4800, "area": "Uxbridge"},
    {"name": "Kingston Ln, Uxbridge",           "lat": 51.5445, "lng": -0.4850, "area": "Uxbridge"},
    {"name": "Vine St, Uxbridge",               "lat": 51.5460, "lng": -0.4810, "area": "Uxbridge"},
    # Hayes & Harlington
    {"name": "Station Rd, Hayes",               "lat": 51.5127, "lng": -0.4208, "area": "Hayes"},
    {"name": "Uxbridge Rd, Hayes",              "lat": 51.5140, "lng": -0.4130, "area": "Hayes"},
    {"name": "Coldharbour Ln, Hayes",           "lat": 51.5085, "lng": -0.4050, "area": "Hayes"},
    {"name": "Bath Rd, Harlington",             "lat": 51.4890, "lng": -0.4350, "area": "Harlington"},
    {"name": "High St, Harlington",             "lat": 51.4920, "lng": -0.4290, "area": "Harlington"},
    {"name": "Nestles Ave, Hayes",              "lat": 51.5165, "lng": -0.4165, "area": "Hayes"},
    {"name": "Judge Heath Ln, Hayes",           "lat": 51.5060, "lng": -0.4230, "area": "Hayes"},
    {"name": "Botwell Ln, Hayes",               "lat": 51.5105, "lng": -0.4190, "area": "Hayes"},
    {"name": "Springfield Rd, Hayes",           "lat": 51.5070, "lng": -0.4100, "area": "Hayes"},
    {"name": "Pump Ln, Hayes",                  "lat": 51.5115, "lng": -0.4075, "area": "Hayes"},
    {"name": "Albert Rd, Hayes",                "lat": 51.5095, "lng": -0.4155, "area": "Hayes"},
    # Yiewsley & West Drayton
    {"name": "Falling Ln, Yiewsley",            "lat": 51.5130, "lng": -0.4750, "area": "Yiewsley"},
    {"name": "High St, Yiewsley",               "lat": 51.5125, "lng": -0.4720, "area": "Yiewsley"},
    {"name": "Horton Rd, West Drayton",         "lat": 51.5090, "lng": -0.4680, "area": "West Drayton"},
    {"name": "Station Approach, West Drayton",  "lat": 51.5098, "lng": -0.4726, "area": "West Drayton"},
    {"name": "Swan Rd, West Drayton",           "lat": 51.5075, "lng": -0.4700, "area": "West Drayton"},
    # Sipson
    {"name": "Sipson Rd, Sipson",               "lat": 51.4830, "lng": -0.4410, "area": "Sipson"},
    {"name": "Harmondsworth Ln, Sipson",        "lat": 51.4855, "lng": -0.4460, "area": "Sipson"},
    # Ickenham
    {"name": "Long Ln, Ickenham",               "lat": 51.5580, "lng": -0.4480, "area": "Ickenham"},
    {"name": "Swakeleys Rd, Ickenham",          "lat": 51.5605, "lng": -0.4530, "area": "Ickenham"},
    {"name": "Glebe Ave, Ickenham",             "lat": 51.5565, "lng": -0.4510, "area": "Ickenham"},
    # Ruislip & Eastcote
    {"name": "Field End Rd, Eastcote",          "lat": 51.5720, "lng": -0.3970, "area": "Eastcote"},
    {"name": "Eastcote Rd, Ruislip",            "lat": 51.5760, "lng": -0.4150, "area": "Ruislip"},
    {"name": "High St, Ruislip",                "lat": 51.5735, "lng": -0.4210, "area": "Ruislip"},
    {"name": "Victoria Rd, Ruislip Manor",      "lat": 51.5705, "lng": -0.4110, "area": "Ruislip Manor"},
    {"name": "Kingsend, Ruislip",               "lat": 51.5710, "lng": -0.4280, "area": "Ruislip"},
    {"name": "West End Rd, Ruislip",            "lat": 51.5680, "lng": -0.4320, "area": "Ruislip"},
    # Northwood
    {"name": "Joel St, Northwood Hills",        "lat": 51.5930, "lng": -0.4060, "area": "Northwood Hills"},
    {"name": "Pinner Rd, Northwood",            "lat": 51.6050, "lng": -0.4230, "area": "Northwood"},
    {"name": "Green Ln, Northwood",             "lat": 51.6010, "lng": -0.4180, "area": "Northwood"},
    # Hillingdon village
    {"name": "Harefield Rd, Hillingdon",        "lat": 51.5500, "lng": -0.4570, "area": "Hillingdon"},
    {"name": "Royal Ln, Hillingdon",            "lat": 51.5360, "lng": -0.4550, "area": "Hillingdon"},
    {"name": "Uxbridge Rd, Hillingdon",         "lat": 51.5380, "lng": -0.4500, "area": "Hillingdon"},
    # Northolt
    {"name": "Church Rd, Northolt",             "lat": 51.5460, "lng": -0.3700, "area": "Northolt"},
    {"name": "Mandeville Rd, Northolt",         "lat": 51.5440, "lng": -0.3740, "area": "Northolt"},
    # Harefield
    {"name": "High St, Harefield",              "lat": 51.5970, "lng": -0.4810, "area": "Harefield"},
    {"name": "Moorhall Rd, Harefield",          "lat": 51.5920, "lng": -0.4750, "area": "Harefield"},
]

# ── Constraints (shared across all scenarios) ────────────────
SCHOOL_ZONES = [
    {"name": "Vyners School zone",      "lat": 51.5580, "lng": -0.4450, "radius_m": 200, "avoid_start": "08:00", "avoid_end": "09:00"},
    {"name": "Ruislip High School zone", "lat": 51.5735, "lng": -0.4190, "radius_m": 200, "avoid_start": "08:00", "avoid_end": "09:00"},
    {"name": "Hayes Park School zone",   "lat": 51.5110, "lng": -0.4170, "radius_m": 200, "avoid_start": "08:00", "avoid_end": "09:00"},
    {"name": "Harlington School zone",   "lat": 51.4910, "lng": -0.4310, "radius_m": 200, "avoid_start": "08:00", "avoid_end": "09:00"},
]

ROAD_CLOSURES = [
    {"name": "High St Uxbridge partial closure", "lat": 51.5465, "lng": -0.4783, "radius_m": 100, "reason": "roadworks", "date": "2026-04-29"},
    {"name": "Sipson Rd temporary closure",      "lat": 51.4830, "lng": -0.4410, "radius_m": 150, "reason": "utility works", "date": "2026-04-29"},
]


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def jitter(lat, lng, meters=60):
    d_lat = random.uniform(-meters, meters) / 111_320
    d_lng = random.uniform(-meters, meters) / (111_320 * math.cos(math.radians(lat)))
    return round(lat + d_lat, 6), round(lng + d_lng, 6)


def pick_locations(n, seed_offset=0):
    rng = random.Random(42 + seed_offset)
    selected = rng.sample(LOCATIONS_POOL, min(n, len(LOCATIONS_POOL)))
    return selected


# ═══════════════════════════════════════════════════════════════
# SCENARIO 1: WASTE COLLECTION
# ═══════════════════════════════════════════════════════════════

def generate_waste_data(n=25):
    locations = pick_locations(n, seed_offset=0)
    waste_types = ["general", "recycling", "garden", "bulky"]
    priorities = ["standard", "high", "urgent"]

    points = []
    for i, loc in enumerate(locations):
        lat, lng = jitter(loc["lat"], loc["lng"])
        start_h = random.choice([6, 7, 7, 8])
        end_h = start_h + random.choice([3, 4, 5])

        points.append({
            "id": f"WC-{i+1:03d}",
            "name": loc["name"],
            "area": loc["area"],
            "lat": lat,
            "lng": lng,
            "waste_type": random.choice(waste_types),
            "priority": random.choices(priorities, weights=[70, 20, 10])[0],
            "estimated_service_minutes": random.choice([3, 5, 5, 5, 8, 10]),
            "time_window_start": f"{start_h:02d}:00",
            "time_window_end": f"{min(end_h, 16):02d}:00",
            "bin_count": random.randint(1, 6),
            "access_notes": random.choice([
                None, None, None,
                "Narrow lane — small vehicle only",
                "Rear access only",
                "Double-yellow — quick stop only",
            ]),
        })

    return {
        "metadata": {
            "scenario": "Waste Collection",
            "vehicle_type": "Refuse Truck",
            "description": "Household waste and recycling collection rounds across Hillingdon",
            "vehicle_capacity_bins": 40,
            "vehicle_start_time": "06:30",
            "vehicle_end_time": "16:00",
            "avg_speed_kmh": 25,
            "co2_g_per_km": 270,
            "fuel_litres_per_km": 0.45,
            "note": "All data is synthetic — no real council data used"
        },
        "depot": DEPOT,
        "collection_points": points,
        "constraints": {"school_zones": SCHOOL_ZONES, "road_closures": ROAD_CLOSURES},
    }


# ═══════════════════════════════════════════════════════════════
# SCENARIO 2: SCARAB SWEEPER (street cleaning)
# ═══════════════════════════════════════════════════════════════

def generate_sweeper_data(n=15):
    locations = pick_locations(n, seed_offset=100)
    priorities = ["scheduled", "reactive", "urgent"]

    # Sweeper zones are streets/areas to clean, not bin stops
    zones = []
    for i, loc in enumerate(locations):
        lat, lng = jitter(loc["lat"], loc["lng"], meters=40)
        start_h = random.choice([6, 7, 8, 9])
        end_h = start_h + random.choice([3, 4])

        zones.append({
            "id": f"SW-{i+1:03d}",
            "name": loc["name"],
            "area": loc["area"],
            "lat": lat,
            "lng": lng,
            "sweep_type": random.choice(["kerb_channel", "full_road", "car_park", "market_area"]),
            "priority": random.choices(priorities, weights=[60, 30, 10])[0],
            "estimated_service_minutes": random.choice([10, 15, 15, 20, 25, 30]),
            "time_window_start": f"{start_h:02d}:00",
            "time_window_end": f"{min(end_h, 14):02d}:00",
            "road_width": random.choice(["narrow", "standard", "wide"]),
            "parking_restrictions": random.choice([
                None, None,
                "Residents parking — limited clearance",
                "Single yellow — morning sweep before 08:30",
                "Pay & display — gaps between parked cars",
            ]),
            "litter_level": random.choice(["low", "medium", "medium", "high"]),
            "notes": random.choice([
                None, None, None,
                "Market day — heavy litter expected",
                "Near school — avoid 08:00–09:00 and 15:00–15:30",
                "Leaf fall area — seasonal heavy load",
                "Near takeaway shops — increased litter evenings",
            ]),
        })

    return {
        "metadata": {
            "scenario": "Street Cleaning (Scarab Sweeper)",
            "vehicle_type": "Scarab Compact Sweeper",
            "description": "Mechanical road sweeping across designated zones in Hillingdon",
            "vehicle_capacity_litres_hopper": 1100,
            "vehicle_start_time": "06:00",
            "vehicle_end_time": "14:00",
            "operational_speed_kmh": 15,
            "travel_speed_kmh": 30,
            "co2_g_per_km": 180,
            "fuel_litres_per_km": 0.30,
            "note": "All data is synthetic — no real council data used"
        },
        "depot": DEPOT,
        "sweep_zones": zones,
        "constraints": {"school_zones": SCHOOL_ZONES, "road_closures": ROAD_CLOSURES},
    }


# ═══════════════════════════════════════════════════════════════
# SCENARIO 3: SOLO OPERATIVES (inspections)
# ═══════════════════════════════════════════════════════════════

def generate_inspection_data(n=12):
    locations = pick_locations(n, seed_offset=200)

    inspection_types = [
        {"type": "housing_inspection",       "category": "Housing",            "avg_minutes": 30},
        {"type": "planning_enforcement",     "category": "Planning",           "avg_minutes": 20},
        {"type": "environmental_health",     "category": "Environmental Health","avg_minutes": 25},
        {"type": "noise_complaint",          "category": "Environmental Health","avg_minutes": 15},
        {"type": "food_hygiene",             "category": "Environmental Health","avg_minutes": 45},
        {"type": "fly_tipping_report",       "category": "Waste",              "avg_minutes": 10},
        {"type": "tree_survey",              "category": "Green Spaces",       "avg_minutes": 20},
        {"type": "licensing_check",          "category": "Licensing",           "avg_minutes": 25},
    ]

    visits = []
    for i, loc in enumerate(locations):
        lat, lng = jitter(loc["lat"], loc["lng"], meters=50)
        insp = random.choice(inspection_types)
        start_h = random.choice([9, 9, 10, 10, 11])
        end_h = start_h + random.choice([2, 3, 4])

        visits.append({
            "id": f"IN-{i+1:03d}",
            "name": loc["name"],
            "area": loc["area"],
            "lat": lat,
            "lng": lng,
            "inspection_type": insp["type"],
            "category": insp["category"],
            "priority": random.choices(["routine", "follow_up", "urgent"], weights=[50, 35, 15])[0],
            "estimated_service_minutes": insp["avg_minutes"] + random.randint(-5, 10),
            "time_window_start": f"{start_h:02d}:00",
            "time_window_end": f"{min(end_h, 17):02d}:00",
            "appointment_required": random.choice([True, True, False]),
            "resident_contact": random.choice([
                "Tenant notified — expects morning visit",
                "Landlord contact on file",
                "No appointment — reactive visit",
                None,
            ]),
            "case_reference": f"HIL-2026-{random.randint(10000, 99999)}",
            "follow_up_needed": random.choice([True, False, False]),
        })

    return {
        "metadata": {
            "scenario": "Solo Operative Inspections",
            "vehicle_type": "Council Pool Car (Small Van / Car)",
            "description": "Officers visiting sites for housing inspections, planning enforcement, environmental health, licensing checks",
            "vehicle_start_time": "08:30",
            "vehicle_end_time": "17:00",
            "avg_speed_kmh": 30,
            "co2_g_per_km": 150,
            "fuel_litres_per_km": 0.08,
            "max_visits_per_day": 12,
            "note": "All data is synthetic — no personal, sensitive or real council data used. Case references are fake."
        },
        "depot": {
            "id": "DEPOT-CIVIC",
            "name": "Hillingdon Civic Centre",
            "lat": 51.5465,
            "lng": -0.4783,
            "type": "depot",
            "note": "Officers start and end at the Civic Centre"
        },
        "inspection_visits": visits,
        "constraints": {"school_zones": SCHOOL_ZONES, "road_closures": ROAD_CLOSURES},
    }


# ═══════════════════════════════════════════════════════════════
# MAIN — generate all three datasets
# ═══════════════════════════════════════════════════════════════

def main():
    # Waste collection
    waste = generate_waste_data(25)
    with open("data_waste.json", "w") as f:
        json.dump(waste, f, indent=2)
    print(f"✅ Waste Collection: {len(waste['collection_points'])} stops → data_waste.json")
    print(f"   Vehicle: {waste['metadata']['vehicle_type']}")
    print(f"   Areas: {sorted(set(p['area'] for p in waste['collection_points']))}")
    print()

    # Inspections
    inspections = generate_inspection_data(12)
    with open("data_inspections.json", "w") as f:
        json.dump(inspections, f, indent=2)
    print(f"✅ Solo Inspections: {len(inspections['inspection_visits'])} visits → data_inspections.json")
    print(f"   Vehicle: {inspections['metadata']['vehicle_type']}")
    print(f"   Inspection types: {sorted(set(v['inspection_type'] for v in inspections['inspection_visits']))}")
    print(f"   Categories: {sorted(set(v['category'] for v in inspections['inspection_visits']))}")
    print()

    print("=" * 50)
    print("Both datasets generated. Each file includes:")
    print("  • Metadata (vehicle specs, fuel, CO₂ rates)")
    print("  • Depot location")
    print("  • Service points with priorities & time windows")
    print("  • Shared constraints (school zones, road closures)")
    print()
    print("Feed any of these into optimize_routes.py by")
    print("changing the input filename at the top of main().")


if __name__ == "__main__":
    main()
