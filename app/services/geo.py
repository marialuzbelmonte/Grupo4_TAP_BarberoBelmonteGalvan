"""Distancias geográficas y ordenamiento de la ruta (Problema del Agente Viajero).

Convención: la matriz de distancias tiene al hotel en el índice 0 y a las
atracciones en 1..n. Los "órdenes" que devuelven las funciones son índices de
atracciones en base 0 (es decir, sin contar al hotel).
"""
from __future__ import annotations

import math
from itertools import permutations
from typing import Sequence

Point = tuple[float, float]  # (lat, lon)
Matrix = list[list[float]]

EARTH_RADIUS_KM = 6371.0088
BRUTE_FORCE_LIMIT = 10  # 10! = 3.628.800 permutaciones: más que eso es inviable


def haversine_km(a: Point, b: Point) -> float:
    """Distancia sobre la esfera terrestre (gran círculo) entre dos puntos."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(h)))


def distance_matrix(start: Point, points: Sequence[Point]) -> Matrix:
    nodes = [start, *points]
    return [[haversine_km(a, b) for b in nodes] for a in nodes]


def _path(order: Sequence[int], closed: bool) -> list[int]:
    path = [0, *(i + 1 for i in order)]
    return [*path, 0] if closed else path


def tour_length(order: Sequence[int], matrix: Matrix, closed: bool = True) -> float:
    path = _path(order, closed)
    return sum(matrix[a][b] for a, b in zip(path, path[1:]))


def nearest_neighbor(matrix: Matrix) -> list[int]:
    """Heurística del Vecino Más Cercano: O(n²).

    Desde el hotel, siempre se visita la atracción no visitada más cercana.
    """
    n = len(matrix) - 1
    unvisited = set(range(1, n + 1))
    current = 0
    order: list[int] = []
    while unvisited:
        nxt = min(unvisited, key=lambda j: (matrix[current][j], j))
        unvisited.remove(nxt)
        order.append(nxt - 1)
        current = nxt
    return order


def two_opt(order: Sequence[int], matrix: Matrix, closed: bool = True, max_rounds: int = 50) -> list[int]:
    """Mejora local 2-opt: invierte tramos mientras eso acorte la ruta.

    Elimina los cruces típicos que deja el Vecino Más Cercano. Cada ronda es
    O(n²); en la práctica converge en pocas rondas.
    """
    path = _path(order, closed)
    last = len(path) - 1 if closed else len(path)
    improved, rounds = True, 0
    while improved and rounds < max_rounds:
        improved, rounds = False, rounds + 1
        for i in range(1, last - 1):
            for k in range(i + 1, last):
                a, b = path[i - 1], path[i]
                c = path[k]
                d = path[k + 1] if k + 1 < len(path) else None
                before = matrix[a][b] + (matrix[c][d] if d is not None else 0.0)
                after = matrix[a][c] + (matrix[b][d] if d is not None else 0.0)
                if after < before - 1e-9:
                    path[i : k + 1] = reversed(path[i : k + 1])
                    improved = True
    inner = path[1:-1] if closed else path[1:]
    return [i - 1 for i in inner]


def brute_force(matrix: Matrix, closed: bool = True) -> list[int]:
    """Solución exacta probando las n! permutaciones. Solo para n pequeño."""
    n = len(matrix) - 1
    if n > BRUTE_FORCE_LIMIT:
        raise ValueError(f"Fuerza bruta con n={n} requiere {math.factorial(n):,} permutaciones")
    best = min(permutations(range(n)), key=lambda p: tour_length(p, matrix, closed), default=())
    return list(best)


def optimize_route(start: Point, points: Sequence[Point]) -> tuple[list[int], dict]:
    """Orden sugerido para visitar `points` saliendo y volviendo a `start`."""
    if not points:
        return [], {"nearest_neighbor_km": 0.0, "two_opt_km": 0.0}
    matrix = distance_matrix(start, points)
    nn = nearest_neighbor(matrix)
    improved = two_opt(nn, matrix)
    stats = {
        "nearest_neighbor_km": round(tour_length(nn, matrix), 3),
        "two_opt_km": round(tour_length(improved, matrix), 3),
    }
    return improved, stats


def split_into_days(order: Sequence[int], days: int) -> list[list[int]]:
    """Corta el recorrido ya ordenado en `days` tramos consecutivos y parejos.

    Como el recorrido es espacialmente continuo, cada tramo agrupa atracciones
    cercanas entre sí. Los tamaños difieren a lo sumo en 1.
    """
    days = max(1, days)
    base, extra = divmod(len(order), days)
    groups, cursor = [], 0
    for d in range(days):
        size = base + (1 if d < extra else 0)
        groups.append(list(order[cursor : cursor + size]))
        cursor += size
    return groups
