"""OpenStreetMap: geocodificación (Nominatim) y puntos de interés (Overpass)."""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass

import aiohttp

from app.config import settings
from app.services.cache import TTLCache
from app.services.geo import haversine_km

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"


@dataclass(frozen=True)
class Category:
    slug: str
    label: str
    rules: tuple[tuple[str, tuple[str, ...]], ...]  # (clave OSM, valores aceptados)
    base_cost_usd: float  # costo por persona cuando no hay dato real
    visit_minutes: int
    require_wikidata: bool = False
    max_items: int = 18
    # Solo en zonas indexadas: en la búsqueda en vivo de una ciudad grande serían cientos
    # y empujarían fuera del tope de Overpass a museos o monumentos.
    index_only: bool = False


CATEGORIES: dict[str, Category] = {
    c.slug: c
    for c in (
        Category("museos", "Museos", (("tourism", ("museum", "gallery")),), 12.0, 120),
        Category(
            "monumentos",
            "Monumentos",
            (("historic", ("monument", "memorial", "castle", "ruins", "fort", "archaeological_site", "city_gate")),),
            0.0,
            30,
        ),
        Category(
            "atracciones",
            "Atracciones",
            (("tourism", ("attraction", "viewpoint", "artwork", "zoo", "aquarium", "theme_park")),),
            0.0,
            45,
        ),
        Category("parques", "Parques", (("leisure", ("park", "garden", "nature_reserve")),), 0.0, 60),
        Category("cultura", "Cultura", (("amenity", ("theatre", "cinema", "arts_centre")),), 10.0, 120, max_items=10),
        Category("bares", "Bares", (("amenity", ("bar", "pub", "biergarten")),), 12.0, 90, max_items=20, index_only=True),
        Category("templos", "Templos", (("amenity", ("place_of_worship",)),), 0.0, 30, require_wikidata=True),
        Category("restaurantes", "Restaurantes", (("amenity", ("restaurant", "fast_food")),), 20.0, 75, max_items=14),
        Category("cafes", "Cafés", (("amenity", ("cafe", "ice_cream")),), 7.0, 40, max_items=12),
    )
}

# Tipo concreto de lugar -> (costo estimado USD por persona, minutos de visita).
# Afina la estimación de la categoría: "Atracciones" mezcla un parque temático pago
# con una estatua en la vereda (p. ej. Mafalda en San Telmo es tourism=attraction).
KINDS: dict[str, tuple[float, int]] = {
    "artwork": (0.0, 15),  # estatuas y esculturas en el espacio público
    "attraction": (0.0, 45),
    "viewpoint": (0.0, 30),
    "zoo": (20.0, 180),
    "aquarium": (20.0, 120),
    "theme_park": (35.0, 300),
    "gallery": (0.0, 60),
    "memorial": (0.0, 20),
    "monument": (0.0, 20),
    "ice_cream": (5.0, 25),
    "fast_food": (10.0, 45),
    "theatre": (15.0, 120),
    "cinema": (8.0, 120),
    "arts_centre": (0.0, 60),
}


def kind_of(cat: Category, tags: dict) -> str | None:
    """Valor OSM que define qué es el lugar ("zoo", "viewpoint"…); "artwork" si es una obra pública."""
    if "artwork_type" in tags or tags.get("tourism") == "artwork":
        return "artwork"
    for key, values in cat.rules:
        if tags.get(key) in values:
            return tags[key]
    return None


class UpstreamError(RuntimeError):
    """Una API externa no respondió de forma utilizable."""


# --------------------------------------------------------------------------- Nominatim

_geocode_cache = TTLCache(ttl_seconds=24 * 3600)
_nominatim_lock = asyncio.Lock()
_nominatim_last = 0.0


async def _nominatim(session: aiohttp.ClientSession, url: str, params: dict):
    global _nominatim_last
    # Política de uso de Nominatim: como máximo 1 pedido por segundo.
    async with _nominatim_lock:
        wait = 1.0 - (time.monotonic() - _nominatim_last)
        if wait > 0:
            await asyncio.sleep(wait)
        try:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    raise UpstreamError(f"Nominatim respondió {resp.status}")
                return await resp.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise UpstreamError(f"No se pudo consultar Nominatim: {exc}") from exc
        finally:
            _nominatim_last = time.monotonic()


async def geocode(
    session: aiohttp.ClientSession,
    query: str,
    near: tuple[float, float] | None = None,
    limit: int = 6,
) -> list[dict]:
    """Busca lugares por nombre. Con `near`, restringe la búsqueda a esa zona."""
    key = (query.lower(), near and (round(near[0], 2), round(near[1], 2)), limit)
    if (cached := _geocode_cache.get(key)) is not None:
        return cached

    params = {"q": query, "format": "jsonv2", "limit": str(limit), "accept-language": "es", "addressdetails": "0"}
    if near:
        lat, lon = near
        params |= {"viewbox": f"{lon - 0.25},{lat + 0.25},{lon + 0.25},{lat - 0.25}", "bounded": "1"}

    data = await _nominatim(session, NOMINATIM_URL, params)

    results = []
    for item in data:
        display = item.get("display_name", "")
        bbox = item.get("boundingbox")
        results.append(
            {
                "name": item.get("name") or display.split(",")[0],
                "display_name": display,
                "lat": float(item["lat"]),
                "lon": float(item["lon"]),
                "kind": item.get("type"),
                "bbox": [float(v) for v in bbox] if bbox else None,
            }
        )
    _geocode_cache.set(key, results)
    return results


def address_label(item: dict) -> str | None:
    """Respuesta de Nominatim reverse -> "Calle 123, Ciudad" (lo que sirve para ubicarse)."""
    address = item.get("address") or {}
    street = address.get("road") or address.get("pedestrian") or address.get("footway")
    if street and address.get("house_number"):
        street = f"{street} {address['house_number']}"
    city = next((address[k] for k in ("city", "town", "village", "suburb") if address.get(k)), None)
    parts = [p for p in (street or item.get("name"), city) if p]
    return ", ".join(parts) or (item.get("display_name") or "").split(",")[0] or None


async def reverse(session: aiohttp.ClientSession, lat: float, lon: float) -> str | None:
    """Coordenadas -> dirección aproximada. None si Nominatim no conoce el lugar."""
    key = ("reverse", round(lat, 4), round(lon, 4))  # ~10 m: mismo punto, misma respuesta
    if (cached := _geocode_cache.get(key)) is not None:
        return cached or None
    params = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "zoom": "18",
              "addressdetails": "1", "accept-language": "es"}
    data = await _nominatim(session, NOMINATIM_REVERSE_URL, params)
    label = None if not isinstance(data, dict) or "error" in data else address_label(data)
    _geocode_cache.set(key, label or "")
    return label


# --------------------------------------------------------------------------- Overpass

_overpass_cache = TTLCache(ttl_seconds=6 * 3600, maxsize=128)


def _tag_filter(key: str, values: tuple[str, ...]) -> str:
    if len(values) == 1:
        return f'["{key}"="{values[0]}"]'
    return f'["{key}"~"^({"|".join(values)})$"]'


def bounding_box(lat: float, lon: float, radius_m: int) -> tuple[float, float, float, float]:
    """Cuadrado (sur, oeste, norte, este) que contiene al círculo de radio `radius_m`."""
    dlat = radius_m / 111_320
    dlon = radius_m / (111_320 * max(0.01, math.cos(math.radians(lat))))
    return lat - dlat, lon - dlon, lat + dlat, lon + dlon


def build_query(lat: float, lon: float, radius_m: int, slugs: list[str], exhaustive: bool = False) -> str:
    """Una sola consulta Overpass: unión de todos los filtros dentro de un bbox.

    Filtrar por bbox es mucho más barato para Overpass que `around` (medido:
    ~10 s contra ~55 s en el servidor público); el recorte exacto al círculo
    se hace después en Python con Haversine.
    """
    south, west, north, east = bounding_box(lat, lon, radius_m)
    stmts = []
    for slug in slugs:
        cat = CATEGORIES[slug]
        if cat.index_only and not exhaustive:
            continue
        extra = '["wikidata"]' if cat.require_wikidata and not exhaustive else ""
        stmts += [f'nwr{_tag_filter(k, v)}["name"]{extra};' for k, v in cat.rules]
    return (
        f"[out:json][timeout:40][bbox:{south:.6f},{west:.6f},{north:.6f},{east:.6f}];"
        f"({''.join(stmts)});out center tags 1500;"
    )


def classify(tags: dict, exhaustive: bool = False) -> Category | None:
    for cat in CATEGORIES.values():
        if cat.index_only and not exhaustive:
            continue
        if cat.require_wikidata and not exhaustive and "wikidata" not in tags:
            continue
        if any(tags.get(key) in values for key, values in cat.rules):
            return cat
    return None


def _relevance(tags: dict) -> tuple:
    return ("wikidata" in tags, "wikipedia" in tags, len(tags))


def select_candidates(
    elements: list[dict],
    center: tuple[float, float] | None = None,
    radius_m: int | None = None,
    exhaustive: bool = False,
) -> list[dict]:
    """Deduplica, clasifica, recorta al radio y deja los N más relevantes por categoría.

    Con `exhaustive=True` (indexador de zonas) no hay tope por categoría ni se exige
    Wikidata a los templos: en una ciudad chica interesa tenerlos todos.
    """
    seen: set[str] = set()
    by_cat: dict[str, list[dict]] = {}
    for el in elements:
        el_id = f"{el['type']}/{el['id']}"
        tags = el.get("tags") or {}
        if el_id in seen or not tags.get("name"):
            continue
        lat = el.get("lat", (el.get("center") or {}).get("lat"))
        lon = el.get("lon", (el.get("center") or {}).get("lon"))
        cat = classify(tags, exhaustive)
        if lat is None or lon is None or cat is None:
            continue
        if center and radius_m and haversine_km(center, (lat, lon)) * 1000 > radius_m:
            continue
        seen.add(el_id)
        by_cat.setdefault(cat.slug, []).append({"id": el_id, "lat": lat, "lon": lon, "tags": tags, "category": cat})

    selected: list[dict] = []
    for slug, items in by_cat.items():
        items.sort(key=lambda p: _relevance(p["tags"]), reverse=True)
        selected.extend(items if exhaustive else items[: CATEGORIES[slug].max_items])
    return selected


async def fetch_pois(session: aiohttp.ClientSession, lat: float, lon: float, radius_m: int, slugs: list[str]) -> list[dict]:
    key = (round(lat, 4), round(lon, 4), radius_m, tuple(sorted(slugs)))
    if (cached := _overpass_cache.get(key)) is not None:
        return cached

    elements = await _hedged_overpass(session, build_query(lat, lon, radius_m, slugs))
    _overpass_cache.set(key, elements)
    return elements


async def _post_overpass(session: aiohttp.ClientSession, url: str, query: str) -> list[dict]:
    async with session.post(url, data={"data": query}, timeout=aiohttp.ClientTimeout(total=OVERPASS_TIMEOUT)) as resp:
        if resp.status != 200:
            raise UpstreamError(f"{url} respondió {resp.status}")
        payload = await resp.json(content_type=None)
    return payload.get("elements", [])


OVERPASS_TIMEOUT = 45
HEDGE_DELAY = 6.0


async def _hedged_overpass(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Pedido "cubierto" (hedged request) contra los servidores de Overpass.

    Se lanza el primer servidor; si falla o no respondió en HEDGE_DELAY
    segundos, se lanza además el siguiente, sin cancelar el anterior. Gana la
    primera respuesta válida y las demás tareas se cancelan. Así un mirror
    lento o caído no bloquea al usuario, sin triplicar la carga en el caso normal.
    """
    pending: set[asyncio.Task] = set()
    urls = list(settings.overpass_urls)
    errors: list[str] = []
    try:
        while urls or pending:
            if urls:
                url = urls.pop(0)
                pending.add(asyncio.create_task(_post_overpass(session, url, query), name=url))
            done, pending = await asyncio.wait(
                pending, timeout=HEDGE_DELAY if urls else None, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                try:
                    return task.result()
                except (UpstreamError, aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    errors.append(f"{task.get_name()}: {exc!r}")
                    log.warning("Overpass falló: %s", errors[-1])
    finally:
        for task in pending:
            task.cancel()
    raise UpstreamError("Overpass no disponible en este momento. Probá de nuevo en unos segundos.")
