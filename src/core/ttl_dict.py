"""In-memory хранилище с TTL и ограничением максимального размера."""
import time
from collections import OrderedDict
from typing import Any, TypeVar

T = TypeVar("T")


class TTLDict:
    """Словарь с автоматической очисткой устаревших записей (TTL в секундах)
    и ограничением максимального размера (LRU eviction).
    """

    __slots__ = ("_data", "_ttl", "_max_size")

    def __init__(self, ttl: int = 600, max_size: int = 500) -> None:
        self._data: OrderedDict[Any, tuple[float, Any]] = OrderedDict()
        self._ttl = ttl
        self._max_size = max_size

    def _cleanup(self) -> None:
        now = time.monotonic()
        while self._data:
            key, (ts, _) = next(iter(self._data.items()))
            if now - ts > self._ttl:
                del self._data[key]
            else:
                break

    def set(self, key: Any, value: Any) -> None:
        self._cleanup()
        if len(self._data) >= self._max_size:
            self._data.popitem(last=False)
        self._data[key] = (time.monotonic(), value)

    def __setitem__(self, key: Any, value: Any) -> None:
        self.set(key, value)

    def get(self, key: Any, default: Any = None) -> Any:
        self._cleanup()
        entry = self._data.get(key)
        if entry is None:
            return default
        ts, val = entry
        if time.monotonic() - ts > self._ttl:
            del self._data[key]
            return default
        return val

    def __getitem__(self, key: Any) -> Any:
        val = self.get(key)
        if val is None and key not in self._data:
            raise KeyError(key)
        return val

    def pop(self, key: Any, default: Any = None) -> Any:
        self._cleanup()
        entry = self._data.pop(key, None)
        if entry is None:
            return default
        ts, val = entry
        if time.monotonic() - ts > self._ttl:
            return default
        return val

    def __contains__(self, key: Any) -> bool:
        return self.get(key) is not None

    def __len__(self) -> int:
        self._cleanup()
        return len(self._data)
