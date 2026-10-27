"""Perfil con moneda preferida, tipos de cambio y ubicación actual como base."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import auth, currency, osm, pdf
from tests.test_accounts import PASSWORD, itinerary_payload, register

BLUE = {"rate": 1575.0, "kind": "blue", "label": "dólar blue (venta)", "source": "DolarApi.com",
        "updated": "2026-10-27T20:59:00.000Z"}
RATES = {"base": "USD", "rates": {"USD": 1.0, "ARS": 1575.0, "EUR": 0.9}, "ars": BLUE,
         "updated": "Tue, 27 Oct 2026 00:02:31 +0000", "source": "ExchangeRate-API", "source_url": "https://www.exchangerate-api.com"}

WORLD_API = {"result": "success", "time_last_update_utc": "x", "rates": {"USD": 1, "ARS": 1540.5, "EUR": 0.9, "XYZ": 3}}
BLUE_API = {"moneda": "USD", "casa": "blue", "compra": 1555, "venta": 1575, "fechaActualizacion": "2026-10-27T20:59:00.000Z"}


# ---------------------------------------------------------------- tipos de cambio


class FakeResponse:
    def __init__(self, status, payload):
        self.status, self._payload = status, payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Responde según la URL: ExchangeRate-API o DolarApi."""

    def __init__(self, world=(200, WORLD_API), blue=(200, BLUE_API)):
        self.routes = {currency.RATES_URL: world, currency.BLUE_URL: blue}
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResponse(*self.routes[url])


@pytest.fixture
def fresh_rates(monkeypatch):
    monkeypatch.setattr(currency, "_cache", {"at": 0.0, "data": None})


def test_pesos_use_the_blue_rate_not_the_official(fresh_rates):
    session = FakeSession()
    first = asyncio.run(currency.get_rates(session))
    second = asyncio.run(currency.get_rates(session))
    assert first["rates"] == {"USD": 1.0, "ARS": 1575.0, "EUR": 0.9}  # blue (venta), no el oficial 1540,5
    assert first["ars"]["label"] == "dólar blue (venta)" and first["ars"]["source"] == "DolarApi.com"
    assert second is first and session.calls == 2  # una consulta a cada fuente; la segunda vez, de memoria


def test_without_blue_there_are_no_pesos(fresh_rates):
    data = asyncio.run(currency.get_rates(FakeSession(blue=(503, {}))))
    assert "ARS" not in data["rates"] and data["ars"] is None and data["rates"]["EUR"] == 0.9


def test_rates_fall_back_to_last_known_or_fail(fresh_rates, monkeypatch):
    with pytest.raises(currency.RatesUnavailable):
        asyncio.run(currency.get_rates(FakeSession(world=(503, {}), blue=(503, {}))))
    monkeypatch.setattr(currency, "_cache", {"at": -1e9, "data": RATES})  # vencida
    data = asyncio.run(currency.get_rates(FakeSession(world=(503, {}), blue=(503, {}))))
    assert data["rates"] == RATES["rates"] and data["ars"] == BLUE  # se usa la última conocida


def test_rates_endpoint(monkeypatch):
    async def fake(session):
        return RATES

    monkeypatch.setattr(currency, "get_rates", fake)
    with TestClient(app) as client:
        data = client.get("/api/rates").json()
    assert data["rates"]["ARS"] == 1575 and data["ars"]["kind"] == "blue"
    assert {"code": "ARS", "name": "Peso argentino (dólar blue)"} in data["currencies"]


def test_pdf_money_formats():
    ars = pdf.Money("ARS", 1500.0, BLUE["updated"], "DolarApi.com", "dólar blue (venta)")
    assert ars(12) == "$ 18.000" and ars(0.5) == "$ 750"
    assert pdf.Money("EUR", 0.9)(12) == "€ 10,80"
    assert pdf.Money()(12.5) == "US$ 12,50" and pdf.Money().note() is None
    assert ars.note().startswith("Cotización dólar blue (venta): 1 US$ = 1.500 ARS (DolarApi.com, 27/10/2026)")
    eur = pdf.Money("EUR", 0.9, RATES["updated"]).note()
    assert "ExchangeRate-API, 27/10/2026" in eur and "blue" not in eur


def test_pdf_in_another_currency(monkeypatch):
    async def fake(session):
        return RATES

    monkeypatch.setattr(currency, "get_rates", fake)
    seen = []
    real_render = pdf.render
    monkeypatch.setattr(pdf, "render", lambda it, money=pdf.Money(): seen.append(money) or real_render(it, money))
    with TestClient(app) as client:
        res = client.post("/api/itinerary/pdf?currency=ars", json=itinerary_payload())
        assert res.status_code == 200 and res.content.startswith(b"%PDF")
        assert client.post("/api/itinerary/pdf?currency=XX1", json=itinerary_payload()).status_code == 422
    assert seen[0].code == "ARS" and seen[0].rate == 1575 and seen[0].label == "dólar blue (venta)"


def test_pdf_falls_back_to_dollars_without_rates(monkeypatch):
    async def broken(session):
        raise currency.RatesUnavailable("sin datos")

    monkeypatch.setattr(currency, "get_rates", broken)
    with TestClient(app) as client:
        assert client.post("/api/itinerary/pdf?currency=EUR", json=itinerary_payload()).status_code == 200


# ---------------------------------------------------------------- ubicación actual


def test_address_label_prefers_street_and_city():
    item = {"name": "Grupo Scout", "address": {"road": "Bv. Santa Fe", "house_number": "123", "city": "Rafaela"}}
    assert osm.address_label(item) == "Bv. Santa Fe 123, Rafaela"
    assert osm.address_label({"name": "Plaza 25 de Mayo", "address": {"town": "Sunchales"}}) == "Plaza 25 de Mayo, Sunchales"
    assert osm.address_label({"display_name": "Ruta 34, Santa Fe"}) == "Ruta 34"


def test_reverse_endpoint_is_post_and_tolerates_failures(monkeypatch):
    async def fake(session, lat, lon):
        return f"Calle {lat:.0f}"

    async def broken(session, lat, lon):
        raise osm.UpstreamError("caído")

    with TestClient(app) as client:
        monkeypatch.setattr(osm, "reverse", fake)
        assert client.post("/api/reverse", json={"lat": -31, "lon": -61}).json() == {"label": "Calle -31"}
        assert client.post("/api/reverse", json={"lat": 120, "lon": 0}).status_code == 422
        assert client.get("/api/reverse?lat=-31&lon=-61").status_code == 405  # no por URL: quedaría en los logs
        monkeypatch.setattr(osm, "reverse", broken)
        assert client.post("/api/reverse", json={"lat": -31, "lon": -61}).json() == {"label": None}


# ---------------------------------------------------------------- perfil (PostgreSQL)


def test_profile_updates_name_and_currency(database, monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", auth.RateLimiter(limit=100, window=60))
    with TestClient(app) as client:
        assert client.patch("/api/me", json={"currency": "ARS"}).status_code == 401  # sin sesión
        assert register(client).json()["currency"] == "USD"  # por defecto, dólares

        res = client.patch("/api/me", json={"currency": "ars"})
        assert res.status_code == 200 and res.json()["currency"] == "ARS" and res.json()["name"] == "Ana"
        assert client.patch("/api/me", json={"name": "  Ana María "}).json() == {
            "id": 1, "email": "ana@example.com", "name": "Ana María", "currency": "ARS",
        }
        assert client.patch("/api/me", json={"currency": "BTC"}).status_code == 422
        assert client.patch("/api/me", json={"name": "   "}).status_code == 422

        client.post("/api/auth/logout")
        login = client.post("/api/auth/login", json={"email": "ana@example.com", "password": PASSWORD})
        assert login.json()["currency"] == "ARS"  # queda guardada en la cuenta
