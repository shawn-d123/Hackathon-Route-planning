# RouteIQ — Disruption Handling & Alternate Routes

## Overview

Real-world route planning isn't static. Roads close, vehicles break down, weather changes, and new urgent jobs appear mid-shift. RouteIQ is designed to handle these disruptions by re-optimising routes dynamically, ensuring council services continue with minimal delay.

This document covers how the system responds to two major disruption categories: **road closures** and **vehicle breakdowns**.

---

## 1. Road Closures

### How It Works

Road closures are modelled as constraints in the distance matrix. When a closure is reported, the system sets the cost of travelling through the affected area to infinity, forcing the optimiser to route around it.

### Detection

There are three ways a road closure enters the system:

**Pre-planned closures** are loaded at the start of the day from the constraints dataset. These include scheduled roadworks, utility works, and planned events. The optimiser avoids them from the outset — the route is built around them, not adjusted after the fact.

**Real-time closures** are reported mid-shift by a driver or dispatcher. The system receives the closure location and radius, updates the distance matrix by setting all edges passing through the affected zone to infinity, and re-runs the 2-opt optimiser on the remaining unvisited stops only. The vehicle's current position becomes the new "depot" for the re-optimisation.

**Time-based restrictions** such as school zones (avoid 08:00–09:00) are modelled as conditional penalties. During restricted hours, edges near schools are inflated by a 5× multiplier. Outside those hours, the penalty is removed. This means a route planned for 07:30 might avoid a school zone, but a re-optimised route at 09:15 can pass through it freely.

### Re-routing Process

```
1. Driver reports closure or system receives alert
2. System identifies affected edges in the distance matrix
3. Affected edges set to infinity (impassable)
4. Remaining unvisited stops extracted from the route
5. Driver's current GPS position used as new start point
6. Nearest-neighbour + 2-opt re-optimisation runs (~1 second)
7. New route pushed to driver's device
8. Dashboard updates with revised ETA and stats
```

### What the Driver Sees

The map updates in real-time. The closed road appears as a red zone on the map. The remaining route re-draws as a green line that avoids the closure. The stop order may change — if the closure blocks the next planned stop, the system redirects to the nearest accessible stop and returns to the blocked area later if the closure lifts.

### Impact on Stats

Re-routing adds distance and time compared to the original optimised route, but the system always finds the best available alternative. The dashboard shows a comparison: original planned distance vs. adjusted distance after closure. This transparency lets dispatchers assess whether the closure caused significant disruption or whether the alternate route absorbed it with minimal impact.

---

## 2. Vehicle Breakdowns

### How It Works

A vehicle breakdown removes one truck from service. The system needs to redistribute its remaining unvisited stops across the other active vehicles — or dispatch a replacement.

### Scenario A: Redistribute to Other Vehicles

When a vehicle breaks down mid-route, the system identifies which stops the broken-down vehicle has already completed (these are marked done), extracts the remaining unvisited stops, checks which other active vehicles have spare capacity (based on bin count for waste, or time remaining for inspections), assigns unvisited stops to the nearest vehicle with capacity using a greedy allocation approach, and re-optimises each affected vehicle's route with the newly assigned stops.

```
Broken vehicle: Vehicle 2 (11 stops planned, 5 completed, 6 remaining)

Vehicle 1: 3 stops remaining, 12 bins spare capacity
Vehicle 3: 2 stops remaining, 20 bins spare capacity

→ Allocate 4 stops to Vehicle 3 (closer, more capacity)
→ Allocate 2 stops to Vehicle 1
→ Re-optimise both routes
```

### Scenario B: Dispatch Replacement Vehicle

If no active vehicle has enough spare capacity, the system calculates the optimal route for a replacement vehicle starting from the depot. It includes only the unvisited stops from the broken-down vehicle. The replacement gets a fresh optimised route covering just those stops, minimising delay.

### Priority Handling During Breakdowns

Not all stops are equal. When redistributing, the system prioritises urgent stops first (missed collections, complaints), then high-priority stops (time-sensitive windows), and finally standard stops. If capacity is tight, standard-priority stops may be deferred to the next day. The system flags these so the dispatcher can make a call.

### What the Dispatcher Sees

The dashboard shows a breakdown alert with the vehicle ID, last known location, stops completed, and stops remaining. A panel shows the proposed redistribution: which stops go to which vehicle, with updated ETAs. The dispatcher can accept the automatic redistribution or manually override assignments before confirming.

---

## 3. Other Disruption Types

### Adverse Weather

Heavy rain, snow, or flooding can make certain roads slower or impassable. The system handles this similarly to road closures: affected edges get a speed penalty (e.g., 0.5× speed for heavy rain) or are blocked entirely for flooding. Routes are re-optimised with the adjusted travel times.

### New Urgent Jobs Mid-Shift

A resident reports a missed collection or an urgent fly-tipping incident. The system inserts the new stop into the active vehicle's route at the position that adds the least total distance (cheapest insertion heuristic). If inserting it would push the vehicle over capacity or past its shift end time, the job is assigned to the next available vehicle.

### Traffic Congestion

Peak-hour congestion is modelled as time-dependent speed adjustments on major corridors (A40, M4, Heathrow approach roads). Morning routes (06:00–09:00) use slower speeds on these corridors. Midday routes use standard speeds. The system can re-optimise if a vehicle reports being stuck in unexpected congestion, using real-time position as the new start.

---

## 4. Technical Implementation

### Distance Matrix Update

The key mechanism is modifying the distance matrix without rebuilding it from scratch.

```python
def apply_closure(dist_matrix, all_points, closure_lat, closure_lng, radius_m):
    """Set edges passing near a closure to infinity."""
    for i in range(len(all_points)):
        point_dist = haversine(all_points[i]["lat"], all_points[i]["lng"],
                                closure_lat, closure_lng) * 1000  # km to m
        if point_dist < radius_m:
            for j in range(len(all_points)):
                dist_matrix[i][j] = float('inf')
                dist_matrix[j][i] = float('inf')
    return dist_matrix
```

### Partial Re-optimisation

When disruption occurs mid-route, only unvisited stops are re-optimised. Completed stops are locked in.

```python
def reoptimise_from_current(current_lat, current_lng, remaining_stops, dist_matrix):
    """Re-optimise from the driver's current position."""
    # Create temporary point for current position
    temp_depot = {"lat": current_lat, "lng": current_lng}
    temp_points = [temp_depot] + remaining_stops

    # Build fresh distance matrix for remaining stops only
    temp_matrix = build_distance_matrix(temp_points)

    # Run optimisation
    nn_route = nearest_neighbour(temp_matrix)
    optimised = two_opt(nn_route, temp_matrix)

    return [remaining_stops[i - 1] for i in optimised]  # -1 to offset temp depot
```

### Capacity Check for Redistribution

```python
def redistribute_stops(broken_vehicle_remaining, active_vehicles):
    """Allocate remaining stops to vehicles with spare capacity."""
    assignments = {v["id"]: [] for v in active_vehicles}

    # Sort remaining stops by priority (urgent first)
    priority_order = {"urgent": 0, "high": 1, "standard": 2}
    sorted_stops = sorted(broken_vehicle_remaining,
                          key=lambda s: priority_order.get(s["priority"], 2))

    for stop in sorted_stops:
        # Find nearest vehicle with capacity
        best_vehicle = None
        best_dist = float('inf')
        for v in active_vehicles:
            if v["spare_capacity"] >= stop.get("bin_count", 1):
                d = haversine(v["current_lat"], v["current_lng"],
                              stop["lat"], stop["lng"])
                if d < best_dist:
                    best_dist = d
                    best_vehicle = v
        if best_vehicle:
            assignments[best_vehicle["id"]].append(stop)
            best_vehicle["spare_capacity"] -= stop.get("bin_count", 1)
        else:
            assignments["DEFERRED"].append(stop)  # No capacity — defer

    return assignments
```

---

## 5. Summary Table

| Disruption | Detection | Response | Time to Re-route |
|---|---|---|---|
| Pre-planned road closure | Loaded at start of day | Built into initial optimisation | 0 — already handled |
| Real-time road closure | Driver/dispatcher report | Re-optimise remaining stops around closure | < 2 seconds |
| School zone restriction | Time-based rule | Automatic penalty during restricted hours | Automatic |
| Vehicle breakdown | Driver report | Redistribute to other vehicles or dispatch replacement | < 5 seconds |
| Adverse weather | Weather feed or manual | Speed penalties or road blocks applied | < 2 seconds |
| New urgent job | Dispatcher input | Cheapest insertion into nearest vehicle route | < 1 second |
| Traffic congestion | Driver report or GPS | Re-optimise from current position with adjusted speeds | < 2 seconds |

---

## 6. Key Design Principles

**Fail gracefully.** If the system can't find a valid alternate route (e.g., all paths blocked), it alerts the dispatcher rather than producing a bad route. Human judgment always has the final say.

**Preserve completed work.** Stops already visited are never affected by re-optimisation. Only the future part of the route changes.

**Prioritise the urgent.** During any disruption, urgent and high-priority stops are protected. Standard stops absorb the impact — they're delayed or deferred, not the urgent ones.

**Transparency.** Every re-routing event is logged with a timestamp, reason, original route, and new route. This audit trail lets the council review disruption impact over time and identify patterns (e.g., a road that closes repeatedly might justify a permanent route adjustment).

**Speed.** Re-optimisation must complete in under 5 seconds. The nearest-neighbour + 2-opt approach achieves this for up to 50 stops on standard hardware. For larger sets, the algorithm degrades gracefully — it returns the nearest-neighbour result without 2-opt improvement, which is still far better than no re-routing.
