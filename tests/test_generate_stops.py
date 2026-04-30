from hillingdon_routes.generate_stops import generate_stops
from hillingdon_routes.graph_utils import haversine_matrix


def test_generate_stops_shape_and_columns():
    stops = generate_stops(n_stops=12, seed=7)

    assert len(stops) == 12
    assert {
        "stop_id",
        "lat",
        "lng",
        "demand_kg",
        "service_minutes",
        "ward",
    }.issubset(stops.columns)
    assert stops["stop_id"].tolist() == list(range(12))


def test_haversine_matrix_includes_depot():
    stops = generate_stops(n_stops=3, seed=3)
    matrix = haversine_matrix(stops)

    assert matrix.shape == (4, 4)
    assert (matrix.diagonal() == 0).all()
