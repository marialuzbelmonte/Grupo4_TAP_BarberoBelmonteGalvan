"""Armado de las tarjetas de atracciones: OSM -> Wikidata -> Commons, en paralelo."""
from __future__ import annotations

import asyncio
import math
import re
import time

import aiohttp

from app.config import settings
from app.models import Attraction, Cost, Image
from app.services import commons, geo, osm, text, wikidata

_DAY_NAMES = {"Mo": "Lu", "Tu": "Ma", "We": "Mi", "Th": "Ju", "Fr": "Vi", "Sa": "Sá", "Su": "Do", "PH": "Feriados"}
_DAY_RE = re.compile(r"\b(Mo|Tu|We|Th|Fr|Sa|Su|PH)\b")
_CHARGE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([A-Z]{3})\s*$")
_USD_RATES = {code: rate for code, rate in wikidata.CURRENCIES.values()}
_SINGULAR = {
    "museos": "Museo", "monumentos": "Monumento", "atracciones": "Atracción", "parques": "Parque",
    "templos": "Templo", "restaurantes": "Restaurante", "cafes": "Café", "cultura": "Espacio cultural",
}


def hours_label(raw: str | None) -> str | None:
    """Traduce la sintaxis `opening_hours` de OSM a algo legible en español."""
    if not raw:
        return None
    if raw.strip() == "24/7":
        return "Abierto 24 h"
    label = _DAY_RE.sub(lambda m: _DAY_NAMES[m.group(1)], raw)
    label = label.replace(" off", " cerrado").replace("; ", " · ")
    return label[:300]


def estimate_cost(cat: osm.Category, tags: dict, wd: dict | None) -> Cost:
    """Prioridad: tarifa de Wikidata > etiquetas fee/charge de OSM > estimación por tipo o categoría.

    Es solo la entrada o el consumo en el lugar: los traslados no se incluyen.
    """
    fee = wd and wd.get("fee")
    if fee:
        return Cost(
            amount_usd=fee["usd"],
            source="wikidata",
            label=f"Entrada según Wikidata ({fee['amount']:g} {fee['currency']})",
        )
    if match := _CHARGE_RE.match(tags.get("charge", "")):
        amount, code = float(match.group(1).replace(",", ".")), match.group(2)
        if code in _USD_RATES:
            return Cost(amount_usd=round(amount * _USD_RATES[code], 2), source="osm", label=f"Entrada según OSM ({tags['charge']})")
    if tags.get("fee") == "no":
        return Cost(amount_usd=0.0, source="osm", label="Gratuito según OSM")
    kind = osm.kind_of(cat, tags)
    base = osm.KINDS[kind][0] if kind in osm.KINDS else cat.base_cost_usd
    if tags.get("fee") == "yes" and base == 0:
        return Cost(amount_usd=8.0, source="estimado", label="Con entrada según OSM (monto estimado)")
    if base == 0:
        label = "Gratuito (espacio público)" if kind == "artwork" else "Sin costo de entrada (estimado)"
    elif cat.slug in {"restaurantes", "cafes"}:
        label = "Consumo por persona estimado"
    else:
        label = "Estimación por tipo de lugar"
    return Cost(amount_usd=base, source="estimado", label=label)


def rating(tags: dict, wd: dict | None) -> float:
    """Índice de relevancia de 0 a 5 (OSM no publica calificaciones de usuarios).

    Se basa en cuántas Wikipedias tienen un artículo sobre el lugar, si está
    declarado patrimonio y qué tan completa es su ficha en OSM.
    """
    score = 2.6
    if wd:
        score += min(1.5, math.log10(1 + wd.get("sitelinks", 0)) * 0.8)
        score += 0.4 if wd.get("heritage") else 0.0
    score += 0.2 if "wikipedia" in tags else 0.0
    score += 0.15 if "opening_hours" in tags else 0.0
    score += 0.15 if ("website" in tags or "contact:website" in tags) else 0.0
    return round(min(5.0, score), 1)


def _describe(cat: osm.Category, tags: dict, wd: dict | None) -> str:
    if wd and wd.get("description"):
        desc = wd["description"]
        return desc[0].upper() + desc[1:]
    if tags.get("description"):
        return tags["description"][:280]
    cuisine = tags.get("cuisine")
    if cat.slug == "restaurantes" and cuisine:
        return f"Restaurante · cocina {cuisine.replace(';', ', ').replace('_', ' ')}"
    return f"{_SINGULAR.get(cat.slug, cat.label)} en la zona"


def _safe_url(value: str | None) -> str | None:
    return value if value and value.startswith("https://") else None


async def _enrich(session: aiohttp.ClientSession, sem: asyncio.Semaphore, poi: dict) -> tuple[dict | None, dict | None, float]:
    """Cadena de una atracción: Wikidata y luego Commons.

    Devuelve (wd, imagen, ms de red): el tiempo mide solo las esperas de red,
    no el tiempo en cola del semáforo, para estimar el costo secuencial real.
    """
    tags = poi["tags"]
    busy = 0.0
    wd = None
    qid = tags.get("wikidata", "").split(";")[0].strip()
    if re.fullmatch(r"Q\d+", qid):
        async with sem:
            start = time.perf_counter()
            wd = await wikidata.fetch_entity(session, qid)
            busy += time.perf_counter() - start
    filename = (wd or {}).get("image") or commons.filename_from_tag(tags.get("wikimedia_commons"))
    image = None
    if filename:
        async with sem:
            start = time.perf_counter()
            image = await commons.fetch_thumbnail(session, filename)
            busy += time.perf_counter() - start
    if image is None and (url := _safe_url(tags.get("image"))):
        image = {"url": url, "credit": None, "license": None, "page": None}
    return wd, image, busy * 1000


def build_attraction(poi: dict, wd: dict | None, image: dict | None, origin: geo.Point) -> Attraction:
    cat: osm.Category = poi["category"]
    tags = poi["tags"]
    return Attraction(
        id=poi["id"],
        name=tags.get("name:es") or tags["name"],
        category=cat.slug,
        category_label=cat.label,
        kind=osm.kind_of(cat, tags),
        lat=poi["lat"],
        lon=poi["lon"],
        description=_describe(cat, tags, wd),
        image=Image(**image) if image else None,
        cost=estimate_cost(cat, tags, wd),
        hours=hours_label(tags.get("opening_hours")),
        rating=rating(tags, wd),
        distance_km=round(geo.haversine_km(origin, (poi["lat"], poi["lon"])), 3),
        website=_safe_url((wd or {}).get("website") or tags.get("website") or tags.get("contact:website")),
        wikidata=tags.get("wikidata"),
    )


async def search(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
    radius_m: int,
    slugs: list[str],
    query: str | None = None,
) -> tuple[list[Attraction], dict]:
    t0 = time.perf_counter()
    elements = await osm.fetch_pois(session, lat, lon, radius_m, slugs)
    t1 = time.perf_counter()
    pois = osm.select_candidates(elements, center=(lat, lon), radius_m=radius_m)

    # Todas las cadenas Wikidata -> Commons se lanzan a la vez; el semáforo
    # limita cuántos pedidos simultáneos llegan a los servidores de Wikimedia.
    sem = asyncio.Semaphore(settings.max_concurrency)
    enriched = await asyncio.gather(*(_enrich(session, sem, p) for p in pois))
    t2 = time.perf_counter()

    origin = (lat, lon)
    attractions = [build_attraction(p, wd, img, origin) for p, (wd, img, _) in zip(pois, enriched)]
    if query:
        attractions = [a for a in attractions if text.matches(query, a.name, a.description, a.category_label)]
    attractions.sort(key=lambda a: a.distance_km)

    meta = {
        "count": len(attractions),
        "osm_elements": len(elements),
        "with_wikidata": sum(1 for wd, _, _ in enriched if wd),
        "with_image": sum(1 for a in attractions if a.image),
        "overpass_ms": round((t1 - t0) * 1000),
        "enrich_wall_ms": round((t2 - t1) * 1000),
        # Tiempo que habría tardado hacer cada cadena una detrás de otra.
        "enrich_sequential_ms": round(sum(ms for _, _, ms in enriched)),
    }
    return attractions, meta
