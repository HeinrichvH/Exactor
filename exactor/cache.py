"""
Working-memory cache. SQLite-backed, stdlib only.

One table, three columns. Indexed by key. TTL expressed as an absolute
expires_at timestamp so every read is a single indexed lookup.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional


_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    expires_at INTEGER NOT NULL
)
"""


def normalize_query(query: str) -> str:
    return " ".join(query.strip().lower().split())


def make_key(worker_name: str, query: str) -> str:
    return f"{worker_name}:{normalize_query(query)}"


class Cache:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, key: str) -> Optional[str]:
        row = self._conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if expires_at < int(time.time()):
            return None
        return value

    def put(self, key: str, value: str, ttl_seconds: int) -> None:
        expires_at = int(time.time()) + ttl_seconds
        self._conn.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, value, expires_at),
        )
        self._conn.commit()

    def purge_expired(self) -> int:
        cur = self._conn.execute(
            "DELETE FROM cache WHERE expires_at < ?",
            (int(time.time()),),
        )
        self._conn.commit()
        return cur.rowcount

    def clear_all(self) -> int:
        cur = self._conn.execute("DELETE FROM cache")
        self._conn.commit()
        return cur.rowcount

    def clear_by_worker(self, worker_name: str) -> int:
        cur = self._conn.execute(
            "DELETE FROM cache WHERE key LIKE ? || ':%'",
            (worker_name,),
        )
        self._conn.commit()
        return cur.rowcount

    def clear_by_query_substring(self, substring: str) -> int:
        pattern = f"%{normalize_query(substring)}%"
        cur = self._conn.execute("DELETE FROM cache WHERE key LIKE ?", (pattern,))
        self._conn.commit()
        return cur.rowcount

    def list_entries(self) -> list[tuple[str, int, int]]:
        """Return [(key, value_length, expires_at), ...] ordered by expiry."""
        rows = self._conn.execute(
            "SELECT key, length(value), expires_at FROM cache ORDER BY expires_at"
        ).fetchall()
        return [(k, n, e) for k, n, e in rows]

    def close(self) -> None:
        self._conn.close()
