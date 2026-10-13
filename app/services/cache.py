"""Caché en memoria con vencimiento, para no repetir consultas a APIs públicas."""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any, Hashable


class TTLCache:
    def __init__(self, ttl_seconds: float, maxsize: int = 1024) -> None:
        self.ttl = ttl_seconds
        self.maxsize = maxsize
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()

    def get(self, key: Hashable) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires, value = item
        if expires < time.monotonic():
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any) -> None:
        self._data[key] = (time.monotonic() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def clear(self) -> None:
        self._data.clear()
