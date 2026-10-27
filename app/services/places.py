"""Índice local de lugares en PostgreSQL con búsqueda full-text (tsvector + GIN).

Para las zonas indexadas (p. ej. Rafaela) los lugares salen de la base en vez de
consultar Overpass/Wikidata en vivo, y la búsqueda de texto la resuelve el motor
full-text de PostgreSQL: con raíces de palabras, sin tildes y con ranking.
"""
from __future__ import annotations

import re

import asyncpg

from app.models import Attraction
from app.services import geo, text

TS_CONFIG = "es_sin_tildes"  # definida en app/schema.sql
_WORD = re.compile(r"[a-z0-9]+")

# OSM etiqueta la cocina en inglés (cuisine=mexican); se indexa también en español
# para que "restaurante mexicano" encuentre cuisine=mexican.
CUISINE_ES = {
    "mexican": "mexicana mexicano tacos", "italian": "italiana italiano pastas", "pizza": "pizza pizzería",
    "burger": "hamburguesas hamburguesería", "sushi": "sushi japonesa", "japanese": "japonesa japonés",
    "chinese": "china chino", "regional": "regional típica", "argentinian": "argentina parrilla",
    "argentine": "argentina parrilla", "steak_house": "parrilla asado carnes", "barbecue": "parrilla asado",
    "grill": "parrilla", "ice_cream": "helado heladería", "coffee_shop": "café cafetería",
    "sandwich": "sándwiches", "empanada": "empanadas", "chicken": "pollo", "seafood": "pescados mariscos",
    "vegetarian": "vegetariana", "vegan": "vegana", "arab": "árabe", "peruvian": "peruana",
    "spanish": "española", "german": "alemana", "breakfast": "desayunos", "cake": "tortas pastelería",
    "bakery": "panadería", "kebab": "kebab árabe", "fast_food": "comida rápida",
}


def keywords_for(tags: dict) -> str:
    """Palabras extra para el índice: cocina traducida y servicios útiles para buscar."""
    words: list[str] = []
    for cuisine in re.split(r"[;,]", tags.get("cuisine", "")):
        cuisine = cuisine.strip().lower()
        if cuisine:
            words += [cuisine.replace("_", " "), CUISINE_ES.get(cuisine, "")]
    if tags.get("diet:vegetarian") in {"yes", "only"}:
        words.append("vegetariano")
    if tags.get("diet:vegan") in {"yes", "only"}:
        words.append("vegano")
    if tags.get("amenity") == "fast_food":
        words.append("comida rápida")
    if tags.get("amenity") in {"bar", "pub", "biergarten"}:
        words.append("bar tragos cerveza cervecería birra")
    if tags.get("microbrewery") == "yes" or "brewery" in tags:
        words.append("cerveza artesanal")
    if tags.get("outdoor_seating") == "yes":
        words.append("mesas afuera al aire libre")
    return " ".join(w for w in words if w)


def build_tsquery(query: str | None, mode: str = "and") -> str | None:
    """Texto libre -> expresión segura para to_tsquery.

    "Museo Histórico" -> "museo:* & historico:*". Solo se dejan letras y números
    (nada de operadores que mande el usuario) y cada palabra lleva `:*` para que
    también coincida mientras se escribe ("muse" -> museo). PostgreSQL aplica a
    cada palabra la misma raíz y quita de tildes que al indexar.
    """
    words = _WORD.findall(text.normalize(query))
    if not words:
        return None
    joiner = " & " if mode == "and" else " | "
    return joiner.join(f"{w}:*" for w in words[:12])


async def store_zone(conn: asyncpg.Connection, zone: dict, places: list[dict]) -> int:
    """Reemplaza los lugares de una zona. Cada lugar: {"attraction": {...}, "keywords": "..."}."""
    async with conn.transaction():
        await conn.execute(
            """INSERT INTO zones (slug, name, lat, lon, radius_m) VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (slug) DO UPDATE SET name = $2, lat = $3, lon = $4, radius_m = $5, indexed_at = now()""",
            zone["slug"], zone["name"], zone["lat"], zone["lon"], zone["radius_m"],
        )
        await conn.execute("DELETE FROM places WHERE zone = $1", zone["slug"])
        # Pesos del documento: A = nombre, B = cocina/categoría/tipo, C = descripción.
        # Un acierto en el nombre rankea más alto que uno en la descripción.
        await conn.executemany(
            f"""INSERT INTO places (id, zone, name, category, lat, lon, keywords, data, search)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                    setweight(to_tsvector('{TS_CONFIG}', $3), 'A') ||
                    setweight(to_tsvector('{TS_CONFIG}', $7 || ' ' || $9), 'B') ||
                    setweight(to_tsvector('{TS_CONFIG}', $10), 'C'))
                ON CONFLICT (id) DO NOTHING""",
            [
                (
                    a["id"], zone["slug"], a["name"], a["category"], a["lat"], a["lon"], p.get("keywords", ""),
                    a, f"{a['category_label']} {a.get('kind') or ''}", a.get("description", ""),
                )
                for p in places
                for a in [p["attraction"]]
            ],
        )
    return len(places)


async def zone_at(pool: asyncpg.Pool, lat: float, lon: float) -> dict | None:
    """La zona indexada que contiene a ese punto, si hay alguna."""
    for zone in await pool.fetch("SELECT slug, name, lat, lon, radius_m FROM zones"):
        if geo.haversine_km((zone["lat"], zone["lon"]), (lat, lon)) * 1000 <= zone["radius_m"]:
            return dict(zone)
    return None


def _to_attraction(row: asyncpg.Record, origin: geo.Point) -> Attraction:
    attraction = Attraction.model_validate(row["data"])
    attraction.distance_km = round(geo.haversine_km(origin, (attraction.lat, attraction.lon)), 3)
    return attraction


async def nearby(pool: asyncpg.Pool, zone: str, lat: float, lon: float, radius_m: int) -> list[Attraction]:
    rows = await pool.fetch("SELECT data FROM places WHERE zone = $1", zone)
    found = [_to_attraction(r, (lat, lon)) for r in rows]
    return sorted((a for a in found if a.distance_km * 1000 <= radius_m), key=lambda a: a.distance_km)


async def search(
    pool: asyncpg.Pool,
    zone: str,
    query: str,
    lat: float,
    lon: float,
    radius_m: int,
    mode: str = "and",
    categories: list[str] | None = None,
    limit: int = 30,
) -> list[tuple[Attraction, float]]:
    """Búsqueda full-text rankeada. Devuelve [(lugar, relevancia)] de mayor a menor.

    `search @@ query` usa el índice GIN (no recorre la tabla) y ts_rank_cd puntúa
    según cuántos términos coinciden, qué tan juntos aparecen y su peso (A > B > C).
    """
    tsquery = build_tsquery(query, mode)
    if not tsquery:
        return []
    rows = await pool.fetch(
        f"""SELECT data, ts_rank_cd(search, q) AS rank
            FROM places, to_tsquery('{TS_CONFIG}', $2) AS q
            WHERE zone = $1 AND search @@ q AND ($3::text[] IS NULL OR category = ANY($3))
            ORDER BY rank DESC
            LIMIT 200""",
        zone, tsquery, categories or None,
    )
    origin = (lat, lon)
    results = [(_to_attraction(r, origin), float(r["rank"])) for r in rows]
    results = [(a, rank) for a, rank in results if a.distance_km * 1000 <= radius_m]
    # Ante empate de relevancia, primero lo más cercano al hotel.
    results.sort(key=lambda item: (-item[1], item[0].distance_km))
    return results[:limit]
