"""Indexa una ciudad/zona completa para la búsqueda full-text local.

Uso:
    python -m scripts.index_zone                 # Rafaela (por defecto) -> data/rafaela.json
    python -m scripts.index_zone --db            # además la carga en DATABASE_URL

La app carga data/*.json en la base al arrancar si la zona todavía no está, así que
alcanza con regenerar el JSON y commitearlo. Con --db se reindexa una base existente.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp

from app.config import settings
from app.services import attractions, osm, places

ZONES = {
    "rafaela": {"slug": "rafaela", "name": "Rafaela, Santa Fe", "lat": -31.2526, "lon": -61.4916, "radius_m": 6000},
}
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


async def build(zone: dict) -> list[dict]:
    async with aiohttp.ClientSession(headers={"User-Agent": settings.user_agent}) as session:
        start = time.perf_counter()
        query = osm.build_query(zone["lat"], zone["lon"], zone["radius_m"], list(osm.CATEGORIES), exhaustive=True)
        elements = await osm._hedged_overpass(session, query)
        pois = osm.select_candidates(elements, (zone["lat"], zone["lon"]), zone["radius_m"], exhaustive=True)
        print(f"Overpass: {len(elements)} elementos -> {len(pois)} lugares ({time.perf_counter() - start:.1f} s)")

        sem = asyncio.Semaphore(4)  # moderado: son muchos pedidos a Wikimedia
        enriched = await asyncio.gather(*(attractions._enrich(session, sem, p) for p in pois))
        print(f"Wikidata/Commons: {sum(1 for wd, _, _ in enriched if wd)} con Wikidata, "
              f"{sum(1 for _, img, _ in enriched if img)} con foto ({time.perf_counter() - start:.1f} s)")

    origin = (zone["lat"], zone["lon"])
    return [
        {
            "attraction": attractions.build_attraction(p, wd, img, origin).model_dump(mode="json"),
            "keywords": places.keywords_for(p["tags"]),
        }
        for p, (wd, img, _) in zip(pois, enriched)
    ]


async def store(zone: dict, items: list[dict]) -> None:
    from app import db

    pool = await db.connect()
    if pool is None:
        raise SystemExit("No hay base de datos (revisá DATABASE_URL)")
    async with pool.acquire() as conn:
        count = await places.store_zone(conn, zone, items)
    await pool.close()
    print(f"Base actualizada: {count} lugares en la zona {zone['slug']}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zone", nargs="?", default="rafaela", choices=sorted(ZONES))
    parser.add_argument("--db", action="store_true", help="cargar también en la base de datos")
    args = parser.parse_args()

    zone = ZONES[args.zone]
    items = await build(zone)
    DATA_DIR.mkdir(exist_ok=True)
    out = DATA_DIR / f"{zone['slug']}.json"
    out.write_text(json.dumps({"zone": zone, "places": items}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Guardado {out.relative_to(DATA_DIR.parent)} ({len(items)} lugares)")
    if args.db:
        await store(zone, items)


if __name__ == "__main__":
    asyncio.run(main())
