import time
from pathlib import Path

import pytest

from exactor.cache import Cache, make_key, normalize_query


def test_put_and_get_roundtrip(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put("research:hello world", "some report", ttl_seconds=60)
    assert cache.get("research:hello world") == "some report"


def test_get_returns_none_for_missing(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    assert cache.get("unknown") is None


def test_expired_entry_returns_none(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put("k", "v", ttl_seconds=-1)  # already expired
    assert cache.get("k") is None


def test_put_overwrites_previous(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put("k", "old", ttl_seconds=60)
    cache.put("k", "new", ttl_seconds=60)
    assert cache.get("k") == "new"


def test_purge_expired(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put("fresh", "v", ttl_seconds=60)
    cache.put("stale", "v", ttl_seconds=-1)
    removed = cache.purge_expired()
    assert removed == 1
    assert cache.get("fresh") == "v"


def test_normalize_query_lowercase_and_whitespace():
    assert normalize_query("  Hello   World  ") == "hello world"
    assert normalize_query("SAME") == normalize_query("same")


def test_make_key_includes_worker_name():
    assert make_key("research", "foo") == "research:foo"
    assert make_key("research", "foo") != make_key("explore", "foo")


def test_clear_all(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put("a", "1", ttl_seconds=60)
    cache.put("b", "2", ttl_seconds=60)
    assert cache.clear_all() == 2
    assert cache.get("a") is None


def test_clear_by_worker(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put(make_key("research", "foo"), "r1", ttl_seconds=60)
    cache.put(make_key("research", "bar"), "r2", ttl_seconds=60)
    cache.put(make_key("explore", "foo"), "e1", ttl_seconds=60)
    assert cache.clear_by_worker("research") == 2
    assert cache.get(make_key("research", "foo")) is None
    assert cache.get(make_key("explore", "foo")) == "e1"


def test_clear_by_query_substring(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put(make_key("research", "SQLite WAL mode"), "a", ttl_seconds=60)
    cache.put(make_key("research", "Nuxt 4 changes"), "b", ttl_seconds=60)
    assert cache.clear_by_query_substring("sqlite") == 1
    assert cache.get(make_key("research", "Nuxt 4 changes")) == "b"


def test_list_entries(tmp_path):
    cache = Cache(tmp_path / "cache.db")
    cache.put("k1", "value1", ttl_seconds=60)
    cache.put("k2", "longer value two", ttl_seconds=120)
    entries = cache.list_entries()
    assert len(entries) == 2
    assert entries[0][0] == "k1"
    assert entries[0][1] == len("value1")
