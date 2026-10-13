"""Compara Fuerza Bruta (O(n!)) contra Vecino Más Cercano + 2-opt (O(n²)).

Uso:  python -m scripts.benchmark_tsp
"""
from __future__ import annotations

import math
import random
import time

from app.services import geo

HOTEL = (-34.6037, -58.3816)  # Obelisco, Buenos Aires


def random_points(n: int, seed: int) -> list[geo.Point]:
    rnd = random.Random(seed)
    return [(HOTEL[0] + rnd.uniform(-0.04, 0.04), HOTEL[1] + rnd.uniform(-0.04, 0.04)) for _ in range(n)]


def timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, (time.perf_counter() - start) * 1000


def main() -> None:
    print(f"{'n':>3} | {'permutaciones':>13} | {'fuerza bruta':>12} | {'NN':>8} | {'NN+2opt':>8} | {'km óptimo':>9} | {'km NN':>7} | {'km NN+2opt':>10}")
    print("-" * 92)
    for n in range(4, 11):
        matrix = geo.distance_matrix(HOTEL, random_points(n, seed=n))
        best, t_bf = timed(geo.brute_force, matrix)
        nn, t_nn = timed(geo.nearest_neighbor, matrix)
        opt, t_2 = timed(geo.two_opt, nn, matrix)
        print(
            f"{n:>3} | {math.factorial(n):>13,} | {t_bf:>9.1f} ms | {t_nn:>5.2f} ms | {t_nn + t_2:>5.2f} ms | "
            f"{geo.tour_length(best, matrix):>9.2f} | {geo.tour_length(nn, matrix):>7.2f} | {geo.tour_length(opt, matrix):>10.2f}"
        )

    print("\nSolo heurística (la fuerza bruta ya sería inviable):")
    for n in (20, 40, 100):
        matrix = geo.distance_matrix(HOTEL, random_points(n, seed=n))
        nn, t_nn = timed(geo.nearest_neighbor, matrix)
        opt, t_2 = timed(geo.two_opt, nn, matrix)
        print(
            f"n={n:>3}: NN {t_nn:6.2f} ms ({geo.tour_length(nn, matrix):6.2f} km) · "
            f"NN+2opt {t_nn + t_2:7.2f} ms ({geo.tour_length(opt, matrix):6.2f} km) · "
            f"fuerza bruta: {math.factorial(n):.2e} permutaciones"
        )


if __name__ == "__main__":
    main()
