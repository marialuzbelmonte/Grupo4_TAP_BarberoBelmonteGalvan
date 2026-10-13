"""Orquestación del itinerario: heurística TSP -> Gemini -> validación -> presupuesto."""
from __future__ import annotations

import logging
import re

import aiohttp

from app.config import settings
from app.models import Attraction, Budget, BudgetRow, DayPlan, Itinerary, ItineraryRequest, Stop
from app.services import gemini, geo
from app.services.osm import CATEGORIES, KINDS

log = logging.getLogger(__name__)

_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DAY_START_MIN = 9 * 60 + 30
LUNCH_MIN, DINNER_MIN = 12 * 60 + 30, 20 * 60
WALK_KMH, TRANSIT_KMH, WALK_LIMIT_KM = 4.5, 18.0, 2.0


def travel_minutes(km: float) -> int:
    """Caminando hasta 2 km; más allá se asume transporte público o taxi."""
    minutes = km / WALK_KMH * 60 if km <= WALK_LIMIT_KM else 10 + km / TRANSIT_KMH * 60
    return max(5, round(minutes / 5) * 5)


def _fmt(minutes: int) -> str:
    minutes = min(minutes, 23 * 60 + 55)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _to_minutes(value: str) -> int:
    hours, mins = value.split(":")
    return int(hours) * 60 + int(mins)


def _visit_minutes(a: Attraction) -> int:
    if a.kind in KINDS:
        return KINDS[a.kind][1]
    cat = CATEGORIES.get(a.category)
    return cat.visit_minutes if cat else 60


def _prompt_stop(a: Attraction, prev: geo.Point) -> dict:
    return {
        "id": a.id,
        "nombre": a.name,
        "categoria": a.category_label,
        "horario": a.hours or "sin datos",
        "duracion_sugerida_min": _visit_minutes(a),
        "km_desde_anterior": round(geo.haversine_km(prev, (a.lat, a.lon)), 2),
    }


def reconcile(raw: dict, groups: list[list[Attraction]]) -> tuple[list[dict], str, str, list[str]]:
    """Valida la respuesta del LLM contra los datos reales.

    - Descarta ids inventados o repetidos y días fuera de rango.
    - Reinserta en su día heurístico toda atracción que el LLM haya omitido.
    - Normaliza horarios y duraciones inválidos.
    Devuelve (días, título, resumen, consejos). Cada día: {theme, items:[(attr, time|None, dur, tip)]}.
    """
    by_id = {a.id: a for g in groups for a in g}
    heuristic_day = {a.id: d for d, g in enumerate(groups) for a in g}
    days = [{"theme": "", "items": []} for _ in groups]
    used: set[str] = set()

    for day in raw.get("days") or []:
        if not isinstance(day, dict):
            continue
        idx = day.get("day")
        if not isinstance(idx, int) or not 1 <= idx <= len(groups):
            continue
        target = days[idx - 1]
        target["theme"] = str(day.get("theme") or "")[:80]
        for stop in day.get("stops") or []:
            if not isinstance(stop, dict):
                continue
            sid = stop.get("id")
            if sid not in by_id or sid in used:
                continue
            used.add(sid)
            time_value = stop.get("time") if isinstance(stop.get("time"), str) else ""
            time_value = time_value if _TIME_RE.match(time_value.strip()) else None
            duration = stop.get("duration_min")
            duration = min(300, max(15, duration)) if isinstance(duration, int) else _visit_minutes(by_id[sid])
            target["items"].append((by_id[sid], time_value and time_value.strip(), duration, str(stop.get("tip") or "")[:240]))

    for sid, attraction in by_id.items():
        if sid not in used:
            days[heuristic_day[sid]]["items"].append((attraction, None, _visit_minutes(attraction), ""))

    tips = [str(t)[:240] for t in (raw.get("tips") or []) if isinstance(t, str)][:6]
    return days, str(raw.get("title") or "")[:120], str(raw.get("summary") or "")[:600], tips


def _place_meals(items: list[tuple]) -> list[tuple]:
    """Sin horarios del LLM: ubica los restaurantes cerca del almuerzo y la cena.

    Las visitas conservan el orden geográfico; cada restaurante se inserta en el
    primer hueco en que el reloj simulado pasa de LUNCH_MIN (y el siguiente, de DINNER_MIN).
    """
    meals = [it for it in items if it[0].category == "restaurantes"]
    if not meals:
        return items
    visits = [it for it in items if it[0].category != "restaurantes"]
    ordered, clock, targets = [], DAY_START_MIN, [LUNCH_MIN, DINNER_MIN]
    for item in visits:
        if meals and clock >= targets[0]:
            ordered.append(meals.pop(0))
            targets = targets[1:] or [DINNER_MIN]
        ordered.append(item)
        clock += item[2] + 20
    return ordered + meals



def _schedule(items: list[tuple], hotel: geo.Point) -> list[tuple]:
    """Completa horarios faltantes encadenando visita + traslado, sin retroceder en el tiempo."""
    if all(t for _, t, _, _ in items):
        items = sorted(items, key=lambda it: _to_minutes(it[1]))
    elif not any(t for _, t, _, _ in items):
        items = _place_meals(items)
    clock, prev, result = DAY_START_MIN, hotel, []
    for attraction, time_value, duration, tip in items:
        here = (attraction.lat, attraction.lon)
        arrival = clock + travel_minutes(geo.haversine_km(prev, here)) if result else clock
        start = max(arrival, _to_minutes(time_value)) if time_value else arrival
        if attraction.category == "restaurantes" and not time_value and LUNCH_MIN - 90 <= start < LUNCH_MIN:
            start = LUNCH_MIN
        result.append((attraction, _fmt(start), duration, tip))
        clock, prev = start + duration, here
    return result


def _default_theme(items: list[tuple], number: int) -> str:
    if not items:
        return "Día libre"
    labels = [a.category_label for a, *_ in items if a.category not in {"restaurantes", "cafes"}]
    distinct = list(dict.fromkeys(labels)) or ["Gastronomía"]
    return " y ".join([distinct[0], *(label.lower() for label in distinct[1:2])])


def build_days(day_items: list[dict], hotel: geo.Point) -> list[DayPlan]:
    plans = []
    for number, day in enumerate(day_items, start=1):
        items = _schedule(day["items"], hotel)
        stops, prev, total_km = [], hotel, 0.0
        for attraction, time_value, duration, tip in items:
            here = (attraction.lat, attraction.lon)
            leg = geo.haversine_km(prev, here)
            total_km += leg
            stops.append(
                Stop(
                    id=attraction.id,
                    name=attraction.name,
                    category=attraction.category,
                    category_label=attraction.category_label,
                    lat=attraction.lat,
                    lon=attraction.lon,
                    time=time_value,
                    duration_min=duration,
                    tip=tip,
                    hours=attraction.hours,
                    cost_usd=attraction.cost.amount_usd,
                    cost_source=attraction.cost.source,
                    from_prev_km=round(leg, 2),
                )
            )
            prev = here
        if stops:
            total_km += geo.haversine_km(prev, hotel)
        plans.append(
            DayPlan(
                day=number,
                theme=day["theme"] or _default_theme(items, number),
                stops=stops,
                distance_km=round(total_km, 2),
                subtotal_usd=round(sum(s.cost_usd for s in stops), 2),
            )
        )
    return plans


def build_budget(days: list[DayPlan], travelers: int) -> Budget:
    rows = [
        BudgetRow(
            day=d.day,
            name=s.name,
            category_label=s.category_label,
            unit_usd=s.cost_usd,
            travelers=travelers,
            total_usd=round(s.cost_usd * travelers, 2),
            source=s.cost_source,
        )
        for d in days
        for s in d.stops
    ]
    return Budget(travelers=travelers, rows=rows, total_usd=round(sum(r.total_usd for r in rows), 2))


async def create(session: aiohttp.ClientSession, req: ItineraryRequest) -> Itinerary:
    hotel = (req.hotel.lat, req.hotel.lon)
    attractions = list({a.id: a for a in req.attractions}.values())
    order, stats = geo.optimize_route(hotel, [(a.lat, a.lon) for a in attractions])
    groups = [[attractions[i] for i in g] for g in geo.split_into_days(order, req.days)]

    raw: dict = {}
    source, warning = "heuristica", None
    if settings.gemini_api_key:
        prompt_groups = []
        for group in groups:
            prev, stops = hotel, []
            for a in group:
                stops.append(_prompt_stop(a, prev))
                prev = (a.lat, a.lon)
            prompt_groups.append(stops)
        try:
            raw = await gemini.generate(session, gemini.build_prompt(req.destination, req.hotel.name, prompt_groups))
            source = "gemini"
        except gemini.GeminiError as exc:
            log.warning("Gemini falló, se usa la heurística local: %s", exc)
            warning = f"No se pudo usar Gemini ({exc}). El itinerario se armó con la heurística local."
    else:
        warning = "GEMINI_API_KEY no está configurada: el itinerario se armó con la heurística local."

    day_items, title, summary, tips = reconcile(raw, groups)
    days = build_days(day_items, hotel)
    total_stops = sum(len(d.stops) for d in days)
    return Itinerary(
        title=title or f"{req.days} {'día' if req.days == 1 else 'días'} en {req.destination}",
        summary=summary
        or f"{total_stops} paradas ordenadas por cercanía, saliendo cada día desde {req.hotel.name}.",
        destination=req.destination,
        hotel=req.hotel,
        days=days,
        tips=tips,
        budget=build_budget(days, req.travelers),
        source=source,
        warning=warning,
        route_stats=stats | {"final_km": round(sum(d.distance_km for d in days), 2)},
    )
