"""Normalización de texto para búsquedas y filtros.

El mismo algoritmo está replicado en static/js/text.js para que el filtrado
instantáneo del cliente y el del servidor den exactamente el mismo resultado.
"""
from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")


def normalize(text: str | None) -> str:
    """Convierte "  Museo de la  Nación " en "museo de la nacion".

    1. NFD separa cada letra acentuada en letra base + marca combinante
       ("ó" -> "o" + U+0301).
    2. Se descartan las marcas combinantes (categoría Unicode "Mn").
    3. Minúsculas y colapso de espacios redundantes.
    """
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFD", text)
    without_marks = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return _WHITESPACE.sub(" ", without_marks.lower()).strip()


def tokens(text: str | None) -> list[str]:
    normalized = normalize(text)
    return normalized.split(" ") if normalized else []


def matches(query: str | None, *fields: str | None) -> bool:
    """True si cada palabra de la consulta aparece en alguno de los campos."""
    query_tokens = tokens(query)
    if not query_tokens:
        return True
    haystack = " ".join(normalize(f) for f in fields if f)
    return all(token in haystack for token in query_tokens)
