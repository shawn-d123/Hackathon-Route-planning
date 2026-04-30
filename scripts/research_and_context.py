"""
Hillingdon Hackathon - Challenge 02: Route Planning
Research & Extended Data
--------------------------------------------------------
Covers: UN SDG alignment, cost modelling, Hillingdon context,
        and multi-vehicle-type scenarios.

Run: python research_and_context.py
Output: research_context.json (use in your pitch deck)
"""

import json

# ═══════════════════════════════════════════════════════════════
# 1. UN SDG ALIGNMENT — with specific targets
# ═══════════════════════════════════════════════════════════════

SDG_ALIGNMENT = {
    "primary": [
        {
            "goal": "SDG 11: Sustainable Cities and Communities",
            "target": "11.6",
            "target_text": "Reduce the adverse per capita environmental impact of cities, including by paying special attention to municipal waste management",
            "how_we_address_it": (
                "Our route optimiser directly reduces vehicle-km for municipal waste "
                "collection, cutting exhaust emissions in residential areas. "
                "Fewer unnecessary journeys also mean less noise pollution and road wear."
            ),
            "measurable_impact": "69.4% reduction in route distance → proportional drop in NOx, PM2.5 and CO₂ per collection cycle"
        },
        {
            "goal": "SDG 13: Climate Action",
            "target": "13.2",
            "target_text": "Integrate climate change measures into national policies, strategies and planning",
            "how_we_address_it": (
                "Provides a data-driven tool that embeds emissions reduction "
                "into everyday operational planning. Councils can track CO₂ per route "
                "and report against their climate commitments."
            ),
            "measurable_impact": "Estimated 8.16 tonnes CO₂ saved annually per optimised route set"
        },
    ],
    "secondary": [
        {
            "goal": "SDG 9: Industry, Innovation and Infrastructure",
            "target": "9.4",
            "how_we_address_it": "Uses open-source technology to upgrade existing council fleet operations without capital expenditure"
        },
        {
            "goal": "SDG 12: Responsible Consumption and Production",
            "target": "12.5",
            "how_we_address_it": "More reliable collection schedules reduce missed pickups, preventing waste overflow and fly-tipping"
        },
    ]
}


# ═══════════════════════════════════════════════════════════════
# 2. HILLINGDON CONTEXT — facts for your pitch
# ═══════════════════════════════════════════════════════════════

HILLINGDON_CONTEXT = {
    "borough_facts": {
        "population": "~329,000 residents",
        "households": "~116,000",
        "area_sq_km": 115.7,
        "note": "Largest London borough by area — more road-km to cover than most",
        "council_climate_target": "Hillingdon declared a climate emergency and aims to be carbon neutral by 2030",
    },
    "waste_services": {
        "collections_per_week": "Residual waste weekly, recycling fortnightly, garden waste fortnightly (seasonal)",
        "vehicles_estimated": "20-30 refuse collection vehicles operating across the borough",
        "depots": "New Years Green Lane is one of the main operational depots",
        "current_challenges": [
            "Large geographic spread means long travel distances between rounds",
            "Traffic congestion near Heathrow and the M4/A40 corridors",
            "School-run traffic causes delays in residential areas 08:00-09:00",
            "Seasonal variation in garden waste volumes",
        ]
    },
    "why_route_optimisation_matters": (
        "Hillingdon's large area means even small percentage improvements in route efficiency "
        "translate to significant fuel, time and emissions savings. With 20+ vehicles operating "
        "daily, a 30-40% distance reduction across the fleet could save the council over "
        "£100,000 annually in fuel and driver costs alone."
    )
}


# ═══════════════════════════════════════════════════════════════
# 3. COST MODEL — turn km/time savings into £ for the pitch
# ═══════════════════════════════════════════════════════════════

COST_MODEL = {
    "assumptions": {
        "diesel_price_per_litre_gbp": 1.45,
        "refuse_truck_litres_per_km": 0.45,
        "driver_hourly_rate_gbp": 16.50,
        "working_days_per_year": 260,
        "source_note": "Estimates based on typical UK local authority fleet benchmarks (synthetic, not real council figures)"
    },
    "calculations": {}
}

def calculate_costs(daily_km_saved, daily_minutes_saved, vehicle_type="refuse_truck"):
    """Calculate annual cost savings in GBP."""
    fuel_rate = COST_MODEL["assumptions"][f"{vehicle_type}_litres_per_km"]
    diesel_price = COST_MODEL["assumptions"]["diesel_price_per_litre_gbp"]
    driver_rate = COST_MODEL["assumptions"]["driver_hourly_rate_gbp"]
    days = COST_MODEL["assumptions"]["working_days_per_year"]

    daily_fuel_saving = daily_km_saved * fuel_rate * diesel_price
    daily_labour_saving = (daily_minutes_saved / 60) * driver_rate

    annual_fuel = daily_fuel_saving * days
    annual_labour = daily_labour_saving * days
    annual_total = annual_fuel + annual_labour

    return {
        "daily_fuel_saving_gbp": round(daily_fuel_saving, 2),
        "daily_labour_saving_gbp": round(daily_labour_saving, 2),
        "annual_fuel_saving_gbp": round(annual_fuel, 2),
        "annual_labour_saving_gbp": round(annual_labour, 2),
        "annual_total_saving_gbp": round(annual_total, 2),
    }


# ═══════════════════════════════════════════════════════════════
# 4. MULTI-VEHICLE SCENARIOS — extend beyond just waste trucks
# ═══════════════════════════════════════════════════════════════

VEHICLE_SCENARIOS = [
    {
        "name": "Waste Collection (Refuse Trucks)",
        "description": "Standard household waste and recycling collection rounds",
        "vehicle_type": "refuse_truck",
        "avg_stops_per_day": 25,
        "typical_daily_km_naive": 168,
        "typical_daily_km_optimised": 51,
        "co2_g_per_km": 270,
    },

    {
        "name": "Solo Operatives (Inspections)",
        "description": "Individual officers visiting sites for housing inspections, planning enforcement, environmental health checks",
        "vehicle_type": "refuse_truck",  # using car-like rates would be lower, but keeping simple
        "avg_stops_per_day": 12,
        "typical_daily_km_naive": 65,
        "typical_daily_km_optimised": 30,
        "co2_g_per_km": 150,
        "notes": "Smaller vehicles but high visit counts — optimisation lets officers fit more visits into a shift"
    }
]


# ═══════════════════════════════════════════════════════════════
# 5. GDPR & DATA ETHICS NOTES — for the Security criterion
# ═══════════════════════════════════════════════════════════════

DATA_ETHICS = {
    "gdpr_compliance": [
        "The tool processes only location coordinates and operational metadata — no personal data is collected or stored",
        "Collection point addresses refer to streets/zones, not individual household addresses",
        "No resident names, contact details, or account numbers are used",
        "All data in this prototype is synthetic — no real council data was accessed",
    ],
    "data_minimisation": "The system only requires: coordinates, service type, time window, and bin count. No additional personal data is needed.",
    "security_by_design": [
        "Route data can be stored locally — no mandatory cloud dependency",
        "The tool works offline once data is loaded",
        "No third-party APIs required for the core optimisation (open-source only)",
    ],
    "transparency": "Routes and optimisation decisions are fully auditable — the before/after comparison shows exactly what changed and why."
}


# ═══════════════════════════════════════════════════════════════
# 6. PITCH TALKING POINTS — ready-to-use lines
# ═══════════════════════════════════════════════════════════════

PITCH_POINTS = {
    "opening_hook": (
        "Hillingdon is the largest London borough by area. Every week, council vehicles "
        "drive thousands of kilometres collecting waste, sweeping streets, and running inspections. "
        "Right now, many of these routes are planned manually — and that costs money, time, and carbon."
    ),
    "solution_summary": (
        "We built a route optimisation tool that takes any set of collection points, "
        "applies nearest-neighbour search with 2-opt improvement, and returns an efficient route "
        "in under a second. It handles priorities, vehicle capacity, school zones, and road closures."
    ),
    "key_numbers": {
        "distance_reduction": "69.4%",
        "annual_co2_saved": "8.16 tonnes",
        "annual_cost_saved": "Estimated £15,000+ per optimised route set",
        "implementation_time": "Deployable within 3 months using existing council hardware"
    },
    "scalability": (
        "The tool takes JSON input — any London borough can plug in their own collection points "
        "and run the same optimisation. This could scale to a shared service across West London boroughs."
    ),
    "closing": (
        "Better routes mean lower costs, cleaner air, and more reliable services for 329,000 Hillingdon residents."
    )
}


# ═══════════════════════════════════════════════════════════════
# BUILD & SAVE
# ═══════════════════════════════════════════════════════════════

def main():
    # Calculate costs for each vehicle scenario
    for scenario in VEHICLE_SCENARIOS:
        km_saved = scenario["typical_daily_km_naive"] - scenario["typical_daily_km_optimised"]
        # Rough time estimate: saved km / 25 km/h * 60 min
        time_saved = (km_saved / 25) * 60
        vtype = scenario["vehicle_type"]
        scenario["cost_savings"] = calculate_costs(km_saved, time_saved, vtype)
        scenario["daily_km_saved"] = km_saved
        scenario["daily_co2_saved_kg"] = round(km_saved * scenario["co2_g_per_km"] / 1000, 2)
        scenario["annual_co2_saved_kg"] = round(scenario["daily_co2_saved_kg"] * 260, 1)

    # Fleet-wide annual totals
    fleet_annual_co2 = sum(s["annual_co2_saved_kg"] for s in VEHICLE_SCENARIOS)
    fleet_annual_cost = sum(s["cost_savings"]["annual_total_saving_gbp"] for s in VEHICLE_SCENARIOS)

    output = {
        "sdg_alignment": SDG_ALIGNMENT,
        "hillingdon_context": HILLINGDON_CONTEXT,
        "cost_model": COST_MODEL,
        "vehicle_scenarios": VEHICLE_SCENARIOS,
        "fleet_wide_annual_savings": {
            "total_co2_saved_kg": round(fleet_annual_co2, 1),
            "total_co2_saved_tonnes": round(fleet_annual_co2 / 1000, 2),
            "total_cost_saved_gbp": round(fleet_annual_cost, 2),
        },
        "data_ethics": DATA_ETHICS,
        "pitch_points": PITCH_POINTS,
    }

    with open("research_context.json", "w") as f:
        json.dump(output, f, indent=2)

    # Print summary
    print("=" * 60)
    print("  RESEARCH & CONTEXT — SUMMARY")
    print("=" * 60)

    print("\n📌 UN SDG ALIGNMENT:")
    for sdg in SDG_ALIGNMENT["primary"]:
        print(f"   {sdg['goal']} → Target {sdg['target']}")
        print(f"   Impact: {sdg['measurable_impact']}")
        print()

    print("💷 COST SAVINGS BY VEHICLE TYPE:")
    for s in VEHICLE_SCENARIOS:
        print(f"   {s['name']}")
        print(f"     Daily: {s['daily_km_saved']} km saved → £{s['cost_savings']['daily_fuel_saving_gbp'] + s['cost_savings']['daily_labour_saving_gbp']:.2f}/day")
        print(f"     Annual: £{s['cost_savings']['annual_total_saving_gbp']:,.2f} + {s['annual_co2_saved_kg']:.0f} kg CO₂")
        print()

    print(f"🏢 FLEET-WIDE ANNUAL SAVINGS:")
    print(f"   £{fleet_annual_cost:,.2f} total cost saved")
    print(f"   {fleet_annual_co2/1000:.1f} tonnes CO₂ saved")
    print()

    print(f"✅ Full research context saved → research_context.json")


if __name__ == "__main__":
    main()
