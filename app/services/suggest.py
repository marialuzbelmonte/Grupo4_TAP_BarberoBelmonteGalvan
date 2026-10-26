"""Búsqueda abierta: "algún restaurante mexicano" -> la IA elige un lugar REAL.

Patrón RAG (retrieval-augmented generation) en tres pasos:
  1. Gemini interpreta el pedido: palabras clave y categorías.
  2. El buscador trae candidatos reales (índice full-text si la zona está
     indexada; si no, los lugares cercanos de OpenStreetMap).
  3. Gemini elige entre ESOS candidatos, por id, y explica por qué.
La IA nunca inventa un lugar: solo puede devolver ids de la lista que recibió,
y se valida igual que con el itinerario. Sin Gemini, se usa el mejor resultado
de la búsqueda.
"""
from __future__ import annotations

import json
import logging

import aiohttp
import asyncpg

from app.models import Attraction, SuggestRequest, SuggestResponse, Suggestion
from app.services import attractions, gemini, osm, places, text

log = logging.getLogger(__name__)

MAX_CANDIDATES = 15
GEMINI_BUDGET = 20  # segundos por cada llamada; son dos

INTERPRET_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "keywords": {"type": "STRING"},
        "categories": {"type": "ARRAY", "items": {"type": "STRING", "enum": list(osm.CATEGORIES)}},
        "count": {"type": "INTEGER"},
        "summary": {"type": "STRING"},
    },
    "required": ["keywords", "categories", "count", "summary"],
}

INTERPRET_PROMPT = f"""Convertís pedidos de viajeros en una búsqueda de lugares.
Devolvé:
- "keywords": 2 a 8 palabras para buscar en nombres y descripciones, en español y sin
  relleno ("algún", "quiero", "cerca"). Agregá sinónimos y, si es comida, el tipo de
  cocina en inglés tal como lo etiqueta OpenStreetMap (mexican, italian, pizza, burger,
  sushi, ice_cream...). Ej.: "mexicana mexicano tacos mexican".
- "categories": las que correspondan de {", ".join(osm.CATEGORIES)}; vacío si no está claro.
- "count": cuántos lugares pide (1 por defecto, máximo 5).
- "summary": el pedido reformulado en pocas palabras, en español."""

CHOOSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "picks": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {"id": {"type": "STRING"}, "reason": {"type": "STRING"}},
                "required": ["id", "reason"],
            },
        }
    },
    "required": ["picks"],
}

CHOOSE_PROMPT = """Elegís lugares para un viajero entre una lista de candidatos reales.
Reglas:
- Usá SOLO ids de la lista. Si ningún candidato cumple el pedido, devolvé "picks" vacío.
- Elegí como máximo la cantidad pedida, priorizando que cumpla el pedido; ante varios
  que cumplan, el más cercano al hotel y con mejor calificación.
- "reason": por qué lo elegiste, en una frase de máximo 20 palabras, en español
  rioplatense. No inventes datos que no estén en el candidato."""

# Respaldo sin Gemini: categorías que se deducen de palabras del pedido.
_CATEGORY_HINTS = {
    "restaurantes": ("restaurant", "comer", "comida", "cenar", "almorz", "parrill", "pizz", "hamburg", "sushi"),
    "cafes": ("cafe", "helad", "merienda", "desayun"),
    "museos": ("museo", "galeria", "arte"),
    "parques": ("parque", "plaza", "jardin", "verde"),
    "templos": ("iglesia", "templo", "catedral", "capilla", "parroquia"),
    "monumentos": ("monumento", "estatua", "historic", "memorial"),
    "atracciones": ("mirador", "atraccion", "zoo", "acuario", "escultura"),
    "cultura": ("teatro", "cine", "espectacul", "cultural", "obra", "show"),
}
_FILLER = {
    "algun", "alguna", "alguno", "algunos", "algunas", "un", "una", "unos", "unas", "el", "la", "los", "las",
    "de", "del", "para", "con", "que", "quiero", "busco", "me", "gustaria", "cerca", "hotel", "lugar",
    "lugares", "donde", "ir", "por", "favor", "y", "o", "a", "en", "al", "lo", "mas", "bueno", "buena", "rico", "rica",
}


def fallback_interpretation(request: str) -> dict:
    words = [w for w in text.tokens(request) if w not in _FILLER]
    categories = [slug for slug, hints in _CATEGORY_HINTS.items() if any(w.startswith(h) for w in words for h in hints)]
    return {"keywords": " ".join(words) or request, "categories": categories, "count": 1, "summary": request.strip()}


def _stem(word: str) -> str:
    return word[:5]  # raíz aproximada: "mexicano"/"mexicana"/"mexican" -> "mexic"


def overlap_score(keywords: str, a: Attraction) -> int:
    """Cuántas palabras clave aparecen (por raíz) en la tarjeta. Para zonas no indexadas."""
    haystack = {_stem(w) for w in text.tokens(f"{a.name} {a.description} {a.category_label} {a.kind or ''}")}
    return sum(1 for w in set(text.tokens(keywords)) if len(w) > 2 and _stem(w) in haystack)


async def candidates(
    session: aiohttp.ClientSession, pool: asyncpg.Pool | None, req: SuggestRequest, intent: dict
) -> list[Attraction]:
    exclude = set(req.exclude)
    categories = [c for c in intent["categories"] if c in osm.CATEGORIES]
    zone = await places.zone_at(pool, req.lat, req.lon) if pool else None
    if zone:
        found = await places.search(
            pool, zone["slug"], intent["keywords"], req.lat, req.lon, req.radius,
            mode="or", categories=categories or None, limit=MAX_CANDIDATES + len(exclude),
        )
        ranked = [a for a, _ in found]
    else:
        nearby, _ = await attractions.search(session, req.lat, req.lon, req.radius, categories or list(osm.CATEGORIES))
        scored = [(overlap_score(intent["keywords"], a), a) for a in nearby]
        ranked = [a for score, a in sorted(scored, key=lambda s: (-s[0], s[1].distance_km)) if score > 0]
    return [a for a in ranked if a.id not in exclude][:MAX_CANDIDATES]


def _candidate_card(a: Attraction) -> dict:
    return {
        "id": a.id, "nombre": a.name, "categoria": a.category_label, "descripcion": a.description,
        "km_del_hotel": round(a.distance_km, 2), "calificacion": a.rating, "horario": a.hours or "sin datos",
    }


async def suggest(session: aiohttp.ClientSession, pool: asyncpg.Pool | None, req: SuggestRequest) -> SuggestResponse:
    source = "gemini"
    try:
        intent = await gemini.generate(
            session, f"Pedido: {req.request}", system=INTERPRET_PROMPT, schema=INTERPRET_SCHEMA,
            temperature=0.2, budget=GEMINI_BUDGET,
        )
        intent["count"] = min(5, max(1, int(intent.get("count") or 1)))
    except (gemini.GeminiError, TypeError, ValueError) as exc:
        log.info("Pedido interpretado sin Gemini: %s", exc)
        intent, source = fallback_interpretation(req.request), "busqueda"

    found = await candidates(session, pool, req, intent)
    if not found and intent["categories"]:  # la categoría deducida pudo ser demasiado estricta
        found = await candidates(session, pool, req, {**intent, "categories": []})
    if not found:
        return SuggestResponse(suggestions=[], interpreted_as=intent["summary"], source=source, searched=0)

    by_id = {a.id: a for a in found}
    picks: list[Suggestion] = []
    if source == "gemini":
        prompt = json.dumps(
            {"pedido": req.request, "cantidad": intent["count"], "candidatos": [_candidate_card(a) for a in found]},
            ensure_ascii=False,
        )
        try:
            chosen = await gemini.generate(
                session, prompt, system=CHOOSE_PROMPT, schema=CHOOSE_SCHEMA, temperature=0.3, budget=GEMINI_BUDGET
            )
            for pick in chosen.get("picks") or []:
                pid = pick.get("id") if isinstance(pick, dict) else None
                if pid in by_id and all(s.attraction.id != pid for s in picks):  # solo ids reales, sin repetir
                    picks.append(Suggestion(attraction=by_id[pid], reason=str(pick.get("reason") or "")[:200]))
            return SuggestResponse(
                suggestions=picks[: intent["count"]], interpreted_as=intent["summary"], source="gemini", searched=len(found)
            )
        except gemini.GeminiError as exc:
            log.info("Elección sin Gemini: %s", exc)
            source = "busqueda"

    picks = [
        Suggestion(attraction=a, reason=f"Es el resultado que mejor coincide con tu pedido, a {a.distance_km:.1f} km del hotel.")
        for a in found[: intent["count"]]
    ]
    return SuggestResponse(suggestions=picks, interpreted_as=intent["summary"], source=source, searched=len(found))
