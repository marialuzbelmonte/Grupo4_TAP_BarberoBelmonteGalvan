from dataclasses import replace

import pytest

from app.services import gemini, itinerary


@pytest.fixture(autouse=True)
def no_real_gemini(monkeypatch):
    """Los tests nunca llaman a Gemini real aunque haya una clave en .env."""
    offline = replace(itinerary.settings, gemini_api_key="")
    monkeypatch.setattr(itinerary, "settings", offline)
    monkeypatch.setattr(gemini, "settings", offline)
