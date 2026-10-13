from fastapi.testclient import TestClient

from app.main import app
from app.models import Attraction, Cost
from app.services import attractions as attractions_service


def fake_attraction():
    return Attraction(
        id="node/1", name="Museo de la Nación", category="museos", category_label="Museos",
        lat=-34.6, lon=-58.38, description="Museo", cost=Cost(amount_usd=0, source="osm", label="Gratis"),
        rating=4.0, distance_km=0.2,
    )


def test_health_and_index():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "ok"
        page = client.get("/")
        assert page.status_code == 200 and "Rumbo" in page.text


def test_attractions_validates_categories():
    with TestClient(app) as client:
        res = client.get("/api/attractions", params={"lat": 0, "lon": 0, "categories": "ovnis"})
        assert res.status_code == 422


def test_attractions_endpoint(monkeypatch):
    async def fake_search(session, lat, lon, radius, slugs, q):
        return [fake_attraction()], {"count": 1}

    monkeypatch.setattr(attractions_service, "search", fake_search)
    with TestClient(app) as client:
        res = client.get("/api/attractions", params={"lat": -34.6, "lon": -58.38, "categories": "museos"})
        assert res.status_code == 200
        assert res.json()["attractions"][0]["name"] == "Museo de la Nación"


def test_itinerary_and_pdf_roundtrip():
    payload = {
        "destination": "Buenos Aires",
        "days": 1,
        "travelers": 2,
        "hotel": {"name": "Hotel", "lat": -34.6, "lon": -58.38},
        "attractions": [fake_attraction().model_dump()],
    }
    with TestClient(app) as client:
        itinerary = client.post("/api/itinerary", json=payload)
        assert itinerary.status_code == 200
        body = itinerary.json()
        assert body["days"][0]["stops"][0]["id"] == "node/1"
        pdf = client.post("/api/itinerary/pdf", json=body)
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF")
        assert "itinerario-buenos-aires.pdf" in pdf.headers["content-disposition"]
