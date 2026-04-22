from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .base import MemoryBackend


class FileBackend(MemoryBackend):
    def __init__(self, path: str = ".exactor/session") -> None:
        self._path = Path(path)
        self._path.mkdir(parents=True, exist_ok=True)
        self._log = self._path / "memory.jsonl"

    def store(self, key: str, value: str) -> None:
        entry = {"ts": datetime.now(timezone.utc).isoformat(), "key": key, "value": value}
        with self._log.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def recall(self, query: str) -> str:
        if not self._log.exists():
            return ""
        query_lower = query.lower()
        matches = []
        for line in self._log.read_text().splitlines():
            entry = json.loads(line)
            if query_lower in entry.get("key", "").lower() or query_lower in entry.get("value", "").lower():
                matches.append(f"[{entry['ts']}] {entry['key']}: {entry['value']}")
        return "\n".join(matches[-20:])  # last 20 relevant entries

    def flush(self) -> None:
        if self._log.exists():
            self._log.unlink()
