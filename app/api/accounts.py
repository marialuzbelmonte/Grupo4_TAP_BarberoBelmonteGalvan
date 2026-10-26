"""Registro, inicio de sesión e itinerarios guardados de cada usuario."""
from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.api.deps import current_user, db_pool
from app.config import settings
from app.models import Itinerary, LoginRequest, RegisterRequest, SavedItinerary, User
from app.services import auth

router = APIRouter()

MAX_SAVED_PER_USER = 100


def _start_session(response: Response, user_id: int) -> None:
    # httpOnly: el JavaScript de la página no puede leerla (un XSS no roba la sesión).
    # SameSite=Lax: el navegador no la manda en POST desde otros sitios (protege de CSRF).
    response.set_cookie(
        auth.SESSION_COOKIE,
        auth.create_token(user_id),
        max_age=settings.session_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def _client(request: Request) -> str:
    return request.client.host if request.client else "?"


@router.post("/auth/register", response_model=User, status_code=201)
async def register(body: RegisterRequest, request: Request, response: Response, pool: asyncpg.Pool = Depends(db_pool)):
    if not auth.login_limiter.allow(f"registro:{_client(request)}"):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Probá de nuevo en unos minutos.")
    try:
        user = await auth.register(pool, body.email, body.name, body.password)
    except auth.EmailTaken:
        raise HTTPException(status_code=409, detail="Ya hay una cuenta con ese email.") from None
    _start_session(response, user["id"])
    return user


@router.post("/auth/login", response_model=User)
async def login(body: LoginRequest, request: Request, response: Response, pool: asyncpg.Pool = Depends(db_pool)):
    if not auth.login_limiter.allow(f"login:{_client(request)}"):
        raise HTTPException(status_code=429, detail="Demasiados intentos. Probá de nuevo en unos minutos.")
    user = await auth.authenticate(pool, body.email, body.password)
    if user is None:
        # Mismo mensaje para email inexistente o contraseña incorrecta: no se revela cuál falló.
        raise HTTPException(status_code=401, detail="Email o contraseña incorrectos.")
    _start_session(response, user["id"])
    return user


@router.post("/auth/logout", status_code=204)
async def logout(response: Response) -> Response:
    response.delete_cookie(auth.SESSION_COOKIE, path="/", secure=settings.cookie_secure, samesite="lax")
    response.status_code = 204
    return response


@router.get("/auth/me", response_model=User | None)
async def me(request: Request, pool: asyncpg.Pool = Depends(db_pool)):
    """Usuario de la sesión actual, o null si no hay sesión (no es un error)."""
    user_id = auth.user_id_from_token(request.cookies.get(auth.SESSION_COOKIE))
    return await auth.get_user(pool, user_id) if user_id else None


# ---------------------------------------------------------------- mis itinerarios


def _summary(row: asyncpg.Record) -> SavedItinerary:
    data = row["data"]
    return SavedItinerary(
        id=row["id"],
        title=row["title"],
        destination=row["destination"],
        created_at=row["created_at"],
        days=len(data.get("days", [])),
        stops=sum(len(d.get("stops", [])) for d in data.get("days", [])),
        total_usd=data.get("budget", {}).get("total_usd", 0),
    )


@router.get("/me/itineraries", response_model=list[SavedItinerary])
async def list_itineraries(user: dict = Depends(current_user), pool: asyncpg.Pool = Depends(db_pool)):
    rows = await pool.fetch(
        "SELECT id, title, destination, data, created_at FROM itineraries WHERE user_id = $1 ORDER BY created_at DESC",
        user["id"],
    )
    return [_summary(r) for r in rows]


@router.post("/me/itineraries", response_model=SavedItinerary, status_code=201)
async def save_itinerary(data: Itinerary, user: dict = Depends(current_user), pool: asyncpg.Pool = Depends(db_pool)):
    count = await pool.fetchval("SELECT count(*) FROM itineraries WHERE user_id = $1", user["id"])
    if count >= MAX_SAVED_PER_USER:
        raise HTTPException(status_code=409, detail=f"Llegaste al máximo de {MAX_SAVED_PER_USER} itinerarios guardados.")
    row = await pool.fetchrow(
        """INSERT INTO itineraries (user_id, title, destination, data) VALUES ($1, $2, $3, $4)
           RETURNING id, title, destination, data, created_at""",
        user["id"], data.title, data.destination, data.model_dump(mode="json"),
    )
    return _summary(row)


@router.get("/me/itineraries/{itinerary_id}", response_model=Itinerary)
async def get_itinerary(itinerary_id: int, user: dict = Depends(current_user), pool: asyncpg.Pool = Depends(db_pool)):
    # El filtro por user_id es la autorización: nadie puede leer itinerarios ajenos
    # cambiando el número en la URL.
    data = await pool.fetchval("SELECT data FROM itineraries WHERE id = $1 AND user_id = $2", itinerary_id, user["id"])
    if data is None:
        raise HTTPException(status_code=404, detail="Itinerario no encontrado.")
    return data


@router.delete("/me/itineraries/{itinerary_id}", status_code=204)
async def delete_itinerary(itinerary_id: int, user: dict = Depends(current_user), pool: asyncpg.Pool = Depends(db_pool)):
    deleted = await pool.execute("DELETE FROM itineraries WHERE id = $1 AND user_id = $2", itinerary_id, user["id"])
    if deleted == "DELETE 0":
        raise HTTPException(status_code=404, detail="Itinerario no encontrado.")
    return Response(status_code=204)
