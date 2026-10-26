"""Cuentas de usuario: hash de contraseñas, sesiones con JWT y límite de intentos."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import asyncpg
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import settings

SESSION_COOKIE = "rumbo_session"
JWT_ALGORITHM = "HS256"

# Argon2id: lento a propósito y con uso de memoria, para que probar contraseñas
# por fuerza bruta contra un hash robado sea muy costoso.
_hasher = PasswordHasher()
# Hash de una contraseña cualquiera: se verifica contra él cuando el email no existe,
# así la respuesta tarda lo mismo y no revela qué emails están registrados.
_DUMMY_HASH = _hasher.hash("contraseña-inexistente")


class EmailTaken(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_token(user_id: int) -> str:
    """JWT firmado con HMAC-SHA256: el servidor puede verificar que no fue alterado
    sin guardar las sesiones en la base. `exp` hace que venza solo."""
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(days=settings.session_days)}
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGORITHM)


def user_id_from_token(token: str | None) -> int | None:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGORITHM], options={"require": ["exp", "sub"]})
        return int(payload["sub"])
    except (jwt.PyJWTError, ValueError):
        return None


def _public(row: asyncpg.Record) -> dict:
    return {"id": row["id"], "email": row["email"], "name": row["name"]}


async def register(pool: asyncpg.Pool, email: str, name: str, password: str) -> dict:
    try:
        row = await pool.fetchrow(
            "INSERT INTO users (email, name, password_hash) VALUES ($1, $2, $3) RETURNING id, email, name",
            email.strip(), name.strip(), hash_password(password),
        )
    except asyncpg.UniqueViolationError as exc:
        raise EmailTaken from exc
    return _public(row)


async def authenticate(pool: asyncpg.Pool, email: str, password: str) -> dict | None:
    row = await pool.fetchrow(
        "SELECT id, email, name, password_hash FROM users WHERE lower(email) = lower($1)", email.strip()
    )
    if row is None:
        verify_password(_DUMMY_HASH, password)  # mismo tiempo de respuesta que con un email real
        return None
    if not verify_password(row["password_hash"], password):
        return None
    if _hasher.check_needs_rehash(row["password_hash"]):  # parámetros de Argon2 actualizados
        await pool.execute("UPDATE users SET password_hash = $1 WHERE id = $2", hash_password(password), row["id"])
    return _public(row)


async def get_user(pool: asyncpg.Pool, user_id: int) -> dict | None:
    row = await pool.fetchrow("SELECT id, email, name FROM users WHERE id = $1", user_id)
    return _public(row) if row else None


class RateLimiter:
    """Ventana deslizante en memoria: máximo `limit` intentos por clave cada `window` segundos."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if len(self._hits) > 10_000:  # que la memoria no crezca sin límite
            self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v and now - v[-1] <= self.window})
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            return False
        hits.append(now)
        return True


login_limiter = RateLimiter(limit=10, window=300)  # 10 intentos cada 5 minutos por IP
