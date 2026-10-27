-- Esquema de la base. Se ejecuta en cada arranque: todo es idempotente (IF NOT EXISTS).

-- Búsqueda full-text en español sin tildes ------------------------------------------
-- unaccent quita tildes ("Nación" -> "Nacion") y spanish_stem reduce cada palabra a su
-- raíz ("museos" -> "muse", "mexicana" -> "mexican"), así una búsqueda encuentra plurales,
-- femeninos y palabras escritas sin acento.
CREATE EXTENSION IF NOT EXISTS unaccent;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_ts_config WHERE cfgname = 'es_sin_tildes') THEN
        CREATE TEXT SEARCH CONFIGURATION es_sin_tildes (COPY = spanish);
        ALTER TEXT SEARCH CONFIGURATION es_sin_tildes
            ALTER MAPPING FOR hword, hword_part, word WITH unaccent, spanish_stem;
    END IF;
END
$$;

-- Usuarios e itinerarios guardados --------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id            BIGSERIAL PRIMARY KEY,
    email         TEXT        NOT NULL,
    name          TEXT        NOT NULL,
    password_hash TEXT        NOT NULL,  -- Argon2id, nunca la contraseña
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS users_email_key ON users (lower(email));

CREATE TABLE IF NOT EXISTS itineraries (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT      NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title       TEXT        NOT NULL,
    destination TEXT        NOT NULL,
    data        JSONB       NOT NULL,  -- el itinerario completo, tal como lo devuelve la API
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS itineraries_user_idx ON itineraries (user_id, created_at DESC);

-- Índice local de lugares (zonas indexadas, p. ej. Rafaela) -------------------------
CREATE TABLE IF NOT EXISTS zones (
    slug       TEXT PRIMARY KEY,
    name       TEXT             NOT NULL,
    lat        DOUBLE PRECISION NOT NULL,
    lon        DOUBLE PRECISION NOT NULL,
    radius_m   INTEGER          NOT NULL,
    indexed_at TIMESTAMPTZ      NOT NULL DEFAULT now()
);
-- Huella del archivo data/<zona>.json cargado: si el archivo cambia, se recarga la zona.
ALTER TABLE zones ADD COLUMN IF NOT EXISTS seed_hash TEXT;

CREATE TABLE IF NOT EXISTS places (
    id       TEXT PRIMARY KEY,  -- id de OSM, p. ej. "node/123"
    zone     TEXT             NOT NULL REFERENCES zones (slug) ON DELETE CASCADE,
    name     TEXT             NOT NULL,
    category TEXT             NOT NULL,
    lat      DOUBLE PRECISION NOT NULL,
    lon      DOUBLE PRECISION NOT NULL,
    keywords TEXT             NOT NULL DEFAULT '',  -- cocina en español, tipo de lugar, etc.
    data     JSONB            NOT NULL,             -- la tarjeta completa (Attraction)
    search   TSVECTOR         NOT NULL               -- documento de búsqueda ponderado
);
-- GIN: índice invertido (lexema -> filas que lo contienen). Es lo que evita recorrer
-- todas las filas en cada búsqueda.
CREATE INDEX IF NOT EXISTS places_search_idx ON places USING GIN (search);
CREATE INDEX IF NOT EXISTS places_zone_idx ON places (zone);
