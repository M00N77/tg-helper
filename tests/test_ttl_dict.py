"""Тесты для TTLDict: expiry, LRU eviction, get, pop, setitem."""
import time
import pytest

from src.core.ttl_dict import TTLDict


def test_ttl_dict_basic_get_set():
    d = TTLDict(ttl=10, max_size=5)
    d["a"] = 1
    d.set("b", 2)
    assert d.get("a") == 1
    assert d["b"] == 2
    assert "a" in d
    assert "c" not in d
    assert len(d) == 2


def test_ttl_dict_pop():
    d = TTLDict(ttl=10, max_size=5)
    d["a"] = 100
    assert d.pop("a") == 100
    assert d.pop("a") is None
    assert "a" not in d


def test_ttl_dict_expiration():
    d = TTLDict(ttl=0.1, max_size=5)
    d["short"] = "val"
    assert d["short"] == "val"
    time.sleep(0.15)
    assert d.get("short") is None
    assert "short" not in d
    assert len(d) == 0


def test_ttl_dict_max_size_eviction():
    d = TTLDict(ttl=60, max_size=3)
    d["1"] = 1
    d["2"] = 2
    d["3"] = 3
    assert len(d) == 3
    d["4"] = 4
    # '1' should have been evicted
    assert len(d) == 3
    assert "1" not in d
    assert d["4"] == 4
