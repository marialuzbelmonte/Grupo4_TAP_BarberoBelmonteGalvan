import asyncio

import asyncpg
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import places

RAFAELA = {"lat": -31.2526, "lon": -61.4916}


# ---------------------------------------------------------------- unitarios


@pytest.mark.parametrize(
    ("query", "mode", "expected"),
    [
        ("Museo Histórico", "and", "museo:* & historico:*"),
        ("  PIZZERÍA ", "and", "pizzeria:*"),
        ("teatro | cine", "or", "teatro:* | cine:*"),
        ("'); DROP TABLE users; --", "and", "drop:* & table:* & users:*"),  # sin operadores ni comillas
        ("¿?¡!", "and", None),
        ("", "and", None),
    ],
)
def test_build_tsquery_is_sanitized(query, mode, expected):
    assert places.build_tsquery(query, mode) == expected


def test_keywords_translate_osm_cuisine():
    words = places.keywords_for({"cuisine": "mexican;burger", "diet:vegetarian": "yes", "amenity": "fast_food"})
    for expected in ("mexicana", "hamburguesas", "vegetariano", "comida rápida"):
        assert expected in words


# ---------------------------------------------------------------- integración (PostgreSQL)


def run(coro):
    return asyncio.run(coro)


async def _with_pool(database, fn):
    from app import db

    pool = await db.connect()  # aplica el esquema y carga data/rafaela.json
    try:
        return await fn(pool)
    finally:
        await pool.close()


def test_rafaela_seed_is_loaded_and_detected(database):
    async def check(pool):
        zone = await places.zone_at(pool, RAFAELA["lat"], RAFAELA["lon"])
        far = await places.zone_at(pool, -34.60, -58.38)  # Buenos Aires
        count = await pool.fetchval("SELECT count(*) FROM places WHERE zone = 'rafaela'")
        return zone, far, count

    zone, far, count = run(_with_pool(database, check))
    assert zone["slug"] == "rafaela" and far is None and count > 50


def test_full_text_handles_accents_plurals_and_prefixes(database):
    async def check(pool):
        search = lambda q: places.search(pool, "rafaela", q, RAFAELA["lat"], RAFAELA["lon"], 6000)  # noqa: E731
        return {q: [a.name for a, _ in await search(q)] for q in ("museos historicos", "MUSEO HISTÓRICO", "muse", "hamburguesa")}

    found = run(_with_pool(database, check))
    assert "Museo Histórico Municipal" in found["museos historicos"]
    assert found["museos historicos"] == found["MUSEO HISTÓRICO"]
    assert "Museo Histórico Municipal" in found["muse"]  # prefijo mientras se escribe
    assert "Mostaza" in found["hamburguesa"]  # solo figura en la cocina indexada (cuisine=burger)


def test_name_match_ranks_above_description_match(database):
    async def check(pool):
        zone = {"slug": "prueba", "name": "Prueba", "lat": 0.0, "lon": 0.0, "radius_m": 1000}
        def item(pid, name, desc):
            return {"attraction": {
                "id": pid, "name": name, "category": "museos", "category_label": "Museos", "kind": None,
                "lat": 0.0, "lon": 0.0, "description": desc, "cost": {"amount_usd": 0, "source": "estimado", "label": "x"},
                "rating": 3, "distance_km": 0,
            }, "keywords": ""}
        async with pool.acquire() as conn:
            await places.store_zone(conn, zone, [
                item("n/1", "Casa de la cultura", "Tiene un patio con una fuente"),
                item("n/2", "Fuente de los deseos", "Plaza con una fuente central"),
            ])
        results = await places.search(pool, "prueba", "fuente", 0.0, 0.0, 1000)
        await pool.execute("DELETE FROM zones WHERE slug = 'prueba'")
        return [a.name for a, _ in results]

    assert run(_with_pool(database, check)) == ["Fuente de los deseos", "Casa de la cultura"]


def test_attractions_endpoint_uses_index_for_rafaela(database):
    with TestClient(app) as client:
        data = client.get("/api/attractions", params={**RAFAELA, "radius": 3000}).json()
        assert data["meta"]["source"] == "indice" and data["meta"]["zone"] == "rafaela"
        assert data["attractions"] and all(a["distance_km"] <= 3 for a in data["attractions"])

        hits = client.get("/api/places/search", params={"zone": "rafaela", "q": "pizzería", **RAFAELA}).json()
        assert hits["tsquery"] == "pizzeria:*" and hits["results"]


def test_zone_reloads_when_seed_file_changes(database):
    async def check(pool):
        async with pool.acquire() as conn:
            await conn.execute("UPDATE zones SET seed_hash = 'viejo' WHERE slug = 'rafaela'")
            await conn.execute("DELETE FROM places WHERE zone = 'rafaela' AND category = 'bares'")
            from app import db
            await db.load_seeds(conn)  # el hash no coincide: recarga desde data/rafaela.json
            bars = await conn.fetchval("SELECT count(*) FROM places WHERE zone = 'rafaela' AND category = 'bares'")
            before = await conn.fetchval("SELECT indexed_at FROM zones WHERE slug = 'rafaela'")
            await db.load_seeds(conn)  # mismo archivo: no hace nada
            after = await conn.fetchval("SELECT indexed_at FROM zones WHERE slug = 'rafaela'")
        return bars, before == after

    bars, unchanged = run(_with_pool(database, check))
    assert bars > 0 and unchanged


def test_bars_are_searchable(database):
    async def check(pool):
        return [a.name for a, _ in await places.search(pool, "rafaela", "cerveza", RAFAELA["lat"], RAFAELA["lon"], 6000)]

    found = run(_with_pool(database, check))
    assert "Pinta Bar" in found and "Luck Beer Bar" in found
