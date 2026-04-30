# Hillingdon Waste Route Optimiser

Streamlit demo for planning synthetic waste-collection routes around the London Borough of Hillingdon. The app uses OR-Tools for vehicle routing, OSMnx and NetworkX for OpenStreetMap road distances, scikit-learn for geographic zoning, and Folium for the interactive map.

All collection stops are synthetic. The app does not use resident data, council records, API keys, or personally identifiable information.

## Quick Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
streamlit run app.py
```

For package-style development, install the project in editable mode:

```powershell
python -m pip install -e ".[dev]"
python -m pytest
```

## Demo Flow

1. Click `Load stage demo preset`.
2. Click `Optimise routes`.
3. Review kilometres, CO2, hours, and trucks used.
4. Show the map with per-vehicle routes.
5. Toggle zoning, school-window, peak-hour, closure, and breakdown options.

## Project Layout

```text
.
|-- app.py                         # Streamlit entry point
|-- src/hillingdon_routes/          # Application package
|   |-- app.py                      # Streamlit UI
|   |-- config.py                   # Constants and demo defaults
|   |-- generate_stops.py           # Synthetic stop generation
|   |-- graph_utils.py              # OSMnx and haversine distance matrices
|   |-- solver.py                   # OR-Tools VRP implementation
|   |-- disruptions.py              # Closures and breakdown recovery
|   `-- viz.py                      # Folium map rendering
|-- data/mock/                      # Small synthetic JSON examples
|-- scripts/                        # Data and research context generators
|-- docs/                           # Supporting design notes
|-- tests/                          # Lightweight regression tests
|-- pyproject.toml                  # Package metadata
`-- requirements.txt                # Runtime dependency pins
```

Generated OpenStreetMap caches are written to `cache/` and ignored by git.

## Notes

- The app falls back to haversine distances when OSMnx is unavailable.
- `data/mock/` contains synthetic sample data extracted from the original archive.
- `docs/disruption_handling.md` explains the intended disruption and re-routing model.
