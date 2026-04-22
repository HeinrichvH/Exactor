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
