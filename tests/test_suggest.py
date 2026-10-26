import asyncio

from app.models import Attraction, Cost, SuggestRequest
from app.services import gemini, suggest


def attraction(pid, name, category="restaurantes", description="Restaurante · cocina mexican", km=0.5):
    return Attraction(
        id=pid, name=name, category=category, category_label=category.capitalize(), lat=0, lon=0,
        description=description, cost=Cost(amount_usd=10, source="estimado", label="x"), rating=3.5, distance_km=km,
    )


CANDIDATES = [attraction("node/1", "Taquería Azteca"), attraction("node/2", "Pizzería Napoli", description="pizza")]
REQUEST = SuggestRequest(request="algún restaurante mexicano", lat=0, lon=0)


def test_fallback_interpretation_drops_filler_and_guesses_category():
    intent = suggest.fallback_interpretation("Quiero algún restaurante mexicano cerca del hotel")
    assert intent["keywords"] == "restaurante mexicano"
    assert intent["categories"] == ["restaurantes"]


def test_overlap_score_matches_by_root():
    taqueria, pizzeria = CANDIDATES
    assert suggest.overlap_score("mexicana tacos", taqueria) == 1  # "mexic" en "cocina mexican"
    assert suggest.overlap_score("mexicana tacos", pizzeria) == 0


def fake_pipeline(monkeypatch, gemini_answers):
    """Gemini devuelve respuestas fijas (o falla si no quedan) y los candidatos son CANDIDATES."""
    answers = list(gemini_answers)

    async def fake_generate(session, prompt, **kwargs):
        if not answers:
            raise gemini.GeminiError("sin respuesta")
        return answers.pop(0)

    async def fake_candidates(session, pool, req, intent):
        return list(CANDIDATES)

    monkeypatch.setattr(suggest.gemini, "generate", fake_generate)
    monkeypatch.setattr(suggest, "candidates", fake_candidates)


INTENT = {"keywords": "mexicana mexican", "categories": ["restaurantes"], "count": 1, "summary": "Restaurante mexicano"}


def test_ai_can_only_pick_real_candidates(monkeypatch):
    fake_pipeline(monkeypatch, [INTENT, {"picks": [
        {"id": "node/999", "reason": "inventado"},  # no está entre los candidatos: se descarta
        {"id": "node/1", "reason": "Es mexicano y queda cerca."},
        {"id": "node/1", "reason": "repetido"},
    ]}])
    res = asyncio.run(suggest.suggest(None, None, REQUEST))
    assert res.source == "gemini" and res.searched == 2
    assert [(s.attraction.id, s.reason) for s in res.suggestions] == [("node/1", "Es mexicano y queda cerca.")]


def test_ai_may_answer_that_nothing_matches(monkeypatch):
    fake_pipeline(monkeypatch, [INTENT, {"picks": []}])
    assert asyncio.run(suggest.suggest(None, None, REQUEST)).suggestions == []


def test_without_gemini_uses_best_search_result(monkeypatch):
    fake_pipeline(monkeypatch, [])  # Gemini no responde nunca
    res = asyncio.run(suggest.suggest(None, None, REQUEST))
    assert res.source == "busqueda"
    assert [s.attraction.id for s in res.suggestions] == ["node/1"]


def test_already_selected_places_are_excluded(monkeypatch):
    async def fake_search(session, lat, lon, radius, slugs):
        return list(CANDIDATES), {}

    monkeypatch.setattr(suggest.attractions, "search", fake_search)
    req = REQUEST.model_copy(update={"exclude": ["node/1"]})
    found = asyncio.run(suggest.candidates(None, None, req, {**INTENT, "keywords": "restaurante mexicana pizza"}))
    assert [a.id for a in found] == ["node/2"]


def test_garbled_reason_is_replaced(monkeypatch):
    fake_pipeline(monkeypatch, [INTENT, {"picks": [{"id": "node/1", "reason": "la mejor calificaci%n"}]}])
    reason = asyncio.run(suggest.suggest(None, None, REQUEST)).suggestions[0].reason
    assert "%" not in reason and "coincide con tu pedido" in reason
