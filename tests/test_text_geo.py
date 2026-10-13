import math
import random

import pytest

from app.services import geo, text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Museo de la Nación", "museo de la nacion"),
        ("  CATEDRAL   Metropolitana \t", "catedral metropolitana"),
        ("Café Tortoni", "cafe tortoni"),
        ("São Paulo", "sao paulo"),
        ("Ñandú", "nandu"),
        ("", ""),
        (None, ""),
    ],
)
def test_normalize(raw, expected):
    assert text.normalize(raw) == expected


def test_matches_ignores_accents_and_order():
    assert text.matches("nacion museo", "Museo de la Nación")
    assert text.matches("  CAFÉ ", "Café Tortoni")
    assert not text.matches("teatro", "Museo de la Nación")
    assert text.matches("", "cualquier cosa")


def test_haversine_known_distance():
    obelisco = (-34.6037, -58.3816)
    cabildo = (-34.6088, -58.3740)
    km = geo.haversine_km(obelisco, cabildo)
    assert 0.8 < km < 1.0
    # Buenos Aires - Madrid ≈ 10.050 km
    assert geo.haversine_km(obelisco, (40.4168, -3.7038)) == pytest.approx(10050, rel=0.01)


def _random_points(n, seed):
    rnd = random.Random(seed)
    return [(-34.6 + rnd.uniform(-0.03, 0.03), -58.4 + rnd.uniform(-0.03, 0.03)) for _ in range(n)]


def test_nearest_neighbor_visits_everything_once():
    points = _random_points(15, 1)
    matrix = geo.distance_matrix((-34.6, -58.4), points)
    order = geo.nearest_neighbor(matrix)
    assert sorted(order) == list(range(15))


@pytest.mark.parametrize("seed", range(8))
def test_heuristic_close_to_brute_force(seed):
    start = (-34.6, -58.4)
    points = _random_points(7, seed)
    matrix = geo.distance_matrix(start, points)
    optimal = geo.tour_length(geo.brute_force(matrix), matrix)
    order, _ = geo.optimize_route(start, points)
    heuristic = geo.tour_length(order, matrix)
    assert heuristic >= optimal - 1e-9
    assert heuristic <= optimal * 1.15  # NN + 2-opt queda muy cerca del óptimo


def test_two_opt_never_worse_than_nearest_neighbor():
    start = (-34.6, -58.4)
    points = _random_points(30, 99)
    matrix = geo.distance_matrix(start, points)
    nn = geo.nearest_neighbor(matrix)
    improved = geo.two_opt(nn, matrix)
    assert sorted(improved) == list(range(30))
    assert geo.tour_length(improved, matrix) <= geo.tour_length(nn, matrix) + 1e-9


def test_brute_force_refuses_large_inputs():
    matrix = geo.distance_matrix((0, 0), _random_points(11, 3))
    with pytest.raises(ValueError):
        geo.brute_force(matrix)


def test_split_into_days_is_balanced_and_ordered():
    groups = geo.split_into_days(list(range(10)), 3)
    assert groups == [[0, 1, 2, 3], [4, 5, 6], [7, 8, 9]]
    assert geo.split_into_days([0, 1], 4) == [[0], [1], [], []]
    assert math.fsum(len(g) for g in geo.split_into_days(list(range(7)), 7)) == 7
