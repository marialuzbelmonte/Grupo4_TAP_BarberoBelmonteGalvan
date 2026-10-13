import asyncio

import pytest

from app.models import Attraction, Cost, ItineraryRequest, Place
from app.services import attractions, itinerary, osm, wikidata


def make_attraction(i, lat, lon, category="museos", cost=10.0):
    cat = osm.CATEGORIES[category]
    return Attraction(
        id=f"node/{i}",
        name=f"Lugar {i}",
        category=category,
        category_label=cat.label,
        lat=lat,
        lon=lon,
        description="desc",
        cost=Cost(amount_usd=cost, source="estimado", label="x"),
        rating=3.5,
        distance_km=0.5,
    )


def test_classify_and_select_candidates():
    elements = [
        {"type": "node", "id": 1, "lat": 1, "lon": 1, "tags": {"name": "A", "tourism": "museum"}},
        {"type": "node", "id": 1, "lat": 1, "lon": 1, "tags": {"name": "A", "tourism": "museum"}},  # duplicado
        {"type": "way", "id": 2, "center": {"lat": 2, "lon": 2}, "tags": {"name": "B", "leisure": "park", "wikidata": "Q1"}},
        {"type": "node", "id": 3, "lat": 3, "lon": 3, "tags": {"name": "C", "amenity": "place_of_worship"}},  # sin wikidata
        {"type": "node", "id": 4, "lat": 4, "lon": 4, "tags": {"tourism": "museum"}},  # sin nombre
    ]
    selected = osm.select_candidates(elements)
    assert [p["id"] for p in selected] == ["node/1", "way/2"]
    assert selected[1]["category"].slug == "parques"


def test_build_query_contains_every_category():
    query = osm.build_query(-34.6, -58.4, 2000, ["museos", "cafes"])
    assert query.startswith("[out:json]")
    assert '["tourism"~"^(museum|gallery)$"]' in query
    assert '["amenity"~"^(cafe|ice_cream)$"]' in query
    south, west, north, east = osm.bounding_box(-34.6, -58.4, 2000)
    assert south < -34.6 < north and west < -58.4 < east
    assert f"[bbox:{south:.6f}," in query


def test_select_candidates_clips_to_radius():
    elements = [
        {"type": "node", "id": 1, "lat": -34.600, "lon": -58.40, "tags": {"name": "Cerca", "tourism": "museum"}},
        {"type": "node", "id": 2, "lat": -34.625, "lon": -58.40, "tags": {"name": "Lejos", "tourism": "museum"}},
    ]
    selected = osm.select_candidates(elements, center=(-34.6, -58.4), radius_m=2000)
    assert [p["tags"]["name"] for p in selected] == ["Cerca"]


def test_parse_entity_fee_and_sitelinks():
    entity = {
        "descriptions": {"es": {"value": "museo de arte"}},
        "claims": {
            "P18": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": "Foto.jpg"}}}],
            "P2555": [{"mainsnak": {"snaktype": "value", "datavalue": {"value": {"amount": "+10", "unit": "http://www.wikidata.org/entity/Q4916"}}}}],
            "P1435": [{}],
        },
        "sitelinks": {"eswiki": {}, "enwiki": {}},
    }
    parsed = wikidata.parse_entity(entity)
    assert parsed["image"] == "Foto.jpg"
    assert parsed["fee"] == {"amount": 10.0, "currency": "EUR", "usd": 10.8}
    assert parsed["heritage"] and parsed["sitelinks"] == 2


def test_cost_priority():
    cat = osm.CATEGORIES["museos"]
    assert attractions.estimate_cost(cat, {}, {"fee": {"amount": 5, "currency": "USD", "usd": 5}}).source == "wikidata"
    assert attractions.estimate_cost(cat, {"fee": "no"}, None).amount_usd == 0
    charged = attractions.estimate_cost(cat, {"charge": "20 EUR"}, None)
    assert charged.source == "osm" and charged.amount_usd == pytest.approx(21.6)
    assert attractions.estimate_cost(cat, {}, None).source == "estimado"


def test_hours_label_translates_days():
    assert attractions.hours_label("Mo-Fr 09:00-18:00; Su off") == "Lu-Vi 09:00-18:00 · Do cerrado"
    assert attractions.hours_label("24/7") == "Abierto 24 h"


def _groups():
    a = [make_attraction(i, -34.60 + i * 0.002, -58.38) for i in range(5)]
    return a, [a[:3], a[3:]]


def test_reconcile_drops_invented_and_restores_missing():
    atts, groups = _groups()
    raw = {
        "title": "Plan",
        "days": [
            {"day": 1, "theme": "Centro", "stops": [
                {"id": "node/0", "time": "10:00", "duration_min": 60},
                {"id": "node/999", "time": "11:00", "duration_min": 60},  # inventado
                {"id": "node/0", "time": "12:00", "duration_min": 60},  # repetido
                {"id": "node/1", "time": "25:99", "duration_min": 9999},  # inválidos
            ]},
            {"day": 7, "stops": [{"id": "node/3", "time": "09:00", "duration_min": 30}]},  # día inexistente
        ],
    }
    days, title, _, _ = itinerary.reconcile(raw, groups)
    ids = [a.id for d in days for a, *_ in d["items"]]
    assert sorted(ids) == sorted(a.id for a in atts)
    first = days[0]["items"]
    assert first[1][1] is None and first[1][2] == 300
    assert title == "Plan"


def test_schedule_is_monotonic():
    atts, groups = _groups()
    days, *_ = itinerary.reconcile({}, groups)
    plans = itinerary.build_days(days, (-34.60, -58.38))
    for plan in plans:
        minutes = [int(s.time[:2]) * 60 + int(s.time[3:]) for s in plan.stops]
        assert minutes == sorted(minutes)
        assert plan.stops[0].time == "09:30"


def test_create_without_gemini_uses_heuristic():
    atts, _ = _groups()
    req = ItineraryRequest(destination="Buenos Aires", days=2, travelers=3, hotel=Place(name="Hotel", lat=-34.6, lon=-58.38), attractions=atts)
    result = asyncio.run(itinerary.create(None, req))
    assert result.source == "heuristica" and result.warning
    assert sum(len(d.stops) for d in result.days) == 5
    assert result.budget.total_usd == pytest.approx(5 * 10 * 3)
    assert "no está incluido" in result.budget.note


def test_heuristic_places_restaurant_near_lunch():
    visits = [make_attraction(i, -34.60 + i * 0.001, -58.38) for i in range(4)]
    lunch = make_attraction(9, -34.60, -58.38, category="restaurantes")
    days, *_ = itinerary.reconcile({}, [[lunch, *visits]])
    stops = itinerary.build_days(days, (-34.60, -58.38))[0].stops
    meal = next(s for s in stops if s.category == "restaurantes")
    assert stops[0].category != "restaurantes"
    assert "12:30" <= meal.time <= "14:30"


def test_default_description_uses_proper_singular():
    for slug, expected in [("atracciones", "Atracción"), ("cafes", "Café"), ("museos", "Museo")]:
        assert attractions._describe(osm.CATEGORIES[slug], {}, None) == f"{expected} en la zona"
