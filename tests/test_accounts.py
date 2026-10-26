from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.main import app
from app.services import auth

PASSWORD = "clave-de-prueba-123"


def itinerary_payload(title="Plan de prueba"):
    return {
        "title": title, "summary": "s", "destination": "Rafaela",
        "hotel": {"name": "Hotel", "lat": -31.25, "lon": -61.49},
        "days": [], "tips": [], "source": "heuristica",
        "budget": {"travelers": 1, "rows": [], "total_usd": 0},
    }


# ---------------------------------------------------------------- unitarios (sin base)


def test_password_hash_is_not_the_password_and_verifies():
    hashed = auth.hash_password(PASSWORD)
    assert PASSWORD not in hashed and hashed.startswith("$argon2id$")
    assert auth.verify_password(hashed, PASSWORD)
    assert not auth.verify_password(hashed, "otra")


def test_token_roundtrip_and_rejections():
    assert auth.user_id_from_token(auth.create_token(42)) == 42
    assert auth.user_id_from_token("basura") is None
    expired = jwt.encode(
        {"sub": "1", "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, auth.settings.jwt_secret, algorithm="HS256"
    )
    assert auth.user_id_from_token(expired) is None
    forged = jwt.encode({"sub": "1", "exp": datetime.now(timezone.utc) + timedelta(days=1)}, "otro-secreto-de-al-menos-32-caracteres!", algorithm="HS256")
    assert auth.user_id_from_token(forged) is None


def test_rate_limiter_blocks_after_limit():
    limiter = auth.RateLimiter(limit=3, window=60)
    assert [limiter.allow("ip") for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("otra-ip")


def test_accounts_unavailable_without_database():
    with TestClient(app) as client:
        res = client.post("/api/auth/login", json={"email": "a@example.com", "password": "x"})
        assert res.status_code == 503
        assert client.get("/api/health").json()["database"] is False


# ---------------------------------------------------------------- integración (PostgreSQL)


def register(client, email="ana@example.com", name="Ana"):
    return client.post("/api/auth/register", json={"email": email, "name": name, "password": PASSWORD})


def test_register_login_logout_flow(database, monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", auth.RateLimiter(limit=100, window=60))
    with TestClient(app) as client:
        res = register(client)
        assert res.status_code == 201
        cookie = res.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=lax" in cookie
        assert client.get("/api/auth/me").json()["email"] == "ana@example.com"

        assert register(client, email="ANA@example.com").status_code == 409  # email sin distinguir mayúsculas

        client.post("/api/auth/logout")
        assert client.get("/api/auth/me").json() is None

        wrong = client.post("/api/auth/login", json={"email": "ana@example.com", "password": "incorrecta"})
        unknown = client.post("/api/auth/login", json={"email": "nadie@example.com", "password": PASSWORD})
        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json()["detail"] == unknown.json()["detail"]  # no revela qué falló

        ok = client.post("/api/auth/login", json={"email": "Ana@Example.com", "password": PASSWORD})
        assert ok.status_code == 200 and ok.json()["name"] == "Ana"


def test_validation_rejects_short_password_and_bad_email(database):
    with TestClient(app) as client:
        short = client.post("/api/auth/register", json={"email": "b@example.com", "name": "B", "password": "corta"})
        bad = client.post("/api/auth/register", json={"email": "no-es-email", "name": "B", "password": PASSWORD})
        assert short.status_code == bad.status_code == 422


def test_saved_itineraries_are_private(database, monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", auth.RateLimiter(limit=100, window=60))
    with TestClient(app) as client:
        assert client.get("/api/me/itineraries").status_code == 401  # sin sesión
        register(client)
        ana = client.cookies.get(auth.SESSION_COOKIE)
        client.cookies.clear()
        register(client, email="beto@example.com", name="Beto")
        beto = client.cookies.get(auth.SESSION_COOKIE)

        def as_user(token):
            client.cookies.set(auth.SESSION_COOKIE, token)
            return client

        saved = as_user(ana).post("/api/me/itineraries", json=itinerary_payload())
        assert saved.status_code == 201
        item_id = saved.json()["id"]
        assert [i["title"] for i in as_user(ana).get("/api/me/itineraries").json()] == ["Plan de prueba"]
        assert as_user(ana).get(f"/api/me/itineraries/{item_id}").json()["destination"] == "Rafaela"

        # Beto no ve, no lee y no borra lo de Ana aunque sepa el id.
        assert as_user(beto).get("/api/me/itineraries").json() == []
        assert as_user(beto).get(f"/api/me/itineraries/{item_id}").status_code == 404
        assert as_user(beto).delete(f"/api/me/itineraries/{item_id}").status_code == 404

        assert as_user(ana).delete(f"/api/me/itineraries/{item_id}").status_code == 204
        assert as_user(ana).get("/api/me/itineraries").json() == []


def test_login_is_rate_limited(database, monkeypatch):
    monkeypatch.setattr(auth, "login_limiter", auth.RateLimiter(limit=2, window=60))
    with TestClient(app) as client:
        attempts = [client.post("/api/auth/login", json={"email": "x@example.com", "password": "y"}).status_code for _ in range(3)]
        assert attempts == [401, 401, 429]
