import asyncio
from dataclasses import replace

import pytest

from app.services import gemini


class FakeResponse:
    def __init__(self, status, data):
        self.status, self._data = status, data

    async def json(self, content_type=None):
        return self._data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Devuelve respuestas en orden y registra qué modelo se pidió en cada llamada."""

    def __init__(self, responses):
        self.responses, self.models = list(responses), []

    def post(self, url, **kwargs):
        self.models.append(url.split("/models/")[1].split(":")[0])
        return FakeResponse(*self.responses.pop(0))


OK = (200, {"candidates": [{"content": {"parts": [{"text": '{"title": "Plan", "days": []}'}]}}]})
BUSY = (503, {"error": {"message": "high demand"}})
GONE = (404, {"error": {"message": "no longer available"}})


@pytest.fixture(autouse=True)
def fast_settings(monkeypatch):
    monkeypatch.setattr(gemini, "settings", replace(gemini.settings, gemini_api_key="x", gemini_models=("a", "b", "c")))
    monkeypatch.setattr(gemini, "RETRY_DELAY", 0)


RATE = (429, {"error": {"message": "quota"}})


def test_rate_limit_retries_same_model():
    session = FakeSession([RATE, OK])
    assert asyncio.run(gemini.generate(session, "p"))["title"] == "Plan"
    assert session.models == ["a", "a"]


def test_busy_or_retired_model_skips_to_next():
    session = FakeSession([GONE, BUSY, OK])
    assert asyncio.run(gemini.generate(session, "p"))["title"] == "Plan"
    assert session.models == ["a", "b", "c"]


def test_all_models_fail_raises():
    session = FakeSession([BUSY] * 3)
    with pytest.raises(gemini.GeminiError):
        asyncio.run(gemini.generate(session, "p"))


def test_invalid_key_fails_fast():
    session = FakeSession([(403, {"error": {"message": "API key not valid"}})])
    with pytest.raises(gemini.GeminiError, match="403"):
        asyncio.run(gemini.generate(session, "p"))
    assert session.models == ["a"]
