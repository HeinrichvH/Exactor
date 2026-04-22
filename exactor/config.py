from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


MODE_STRICT = "strict"
MODE_LOOSE = "loose"
VALID_MODES = {MODE_STRICT, MODE_LOOSE}

STDIN_DEVNULL = "devnull"
STDIN_INHERIT = "inherit"
STDIN_CLOSE = "close"
VALID_STDIN = {STDIN_DEVNULL, STDIN_INHERIT, STDIN_CLOSE}


@dataclass
class Worker:
    command: str
    description: str = ""
    timeout: Optional[int] = None      # seconds; None = no inner timeout
    mode: Optional[str] = None         # strict | loose; None inherits Config.mode
    cache: bool = False                # opt-in to working-memory cache
    cache_ttl_hours: Optional[int] = None  # None inherits CacheConfig.default_ttl_hours
    args: Optional[list] = None        # structured args — when present, shell=False
    env: Optional[dict] = None         # subprocess env overlay; ${VAR} expanded from host env
    stdin: str = STDIN_DEVNULL         # devnull | inherit | close
    cwd: Optional[str] = None          # working directory for the subprocess


@dataclass
class InterceptRule:
    tool: str
    route_to: Optional[str] = None
    action: Optional[str] = None       # "summarize"
    query_field: Optional[str] = None  # tool_input key to extract (e.g. "query" for WebSearch).
                                       # Falls back to str(tool_input). Also used as the subject
                                       # for `match:` regex, so both stay consistent.
    match: Optional[str] = None        # regex on the query_field value (or str(tool_input))
    unless: Optional[str] = None       # heuristic: "single_file_absolute_path"
    output_lines_gt: Optional[int] = None


@dataclass
class CacheConfig:
    path: str = ".exactor/cache.db"
    default_ttl_hours: int = 24


@dataclass
class Config:
    workers: dict[str, Worker]
    intercept: list[InterceptRule]
    cache: CacheConfig = field(default_factory=CacheConfig)
    guards: dict = field(default_factory=dict)
    mode: str = MODE_STRICT            # default failure policy for all workers
    source: Optional[Path] = None      # path to the loaded .exactor.yml (None if constructed in-memory)


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())

    workers = {
        name: Worker(**w) if isinstance(w, dict) else Worker(command=w)
        for name, w in (raw.get("workers") or {}).items()
    }

    for name, worker in workers.items():
        if worker.mode and worker.mode not in VALID_MODES:
            raise ValueError(f"worker '{name}': mode must be one of {sorted(VALID_MODES)}, got '{worker.mode}'")
        if worker.stdin not in VALID_STDIN:
            raise ValueError(f"worker '{name}': stdin must be one of {sorted(VALID_STDIN)}, got '{worker.stdin}'")

    intercept = [InterceptRule(**r) for r in (raw.get("intercept") or [])]

    cache_raw = raw.get("cache") or {}
    cache = CacheConfig(**cache_raw) if cache_raw else CacheConfig()

    mode = raw.get("mode", MODE_STRICT)
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}, got '{mode}'")

    return Config(
        workers=workers,
        intercept=intercept,
        cache=cache,
        guards=raw.get("guards") or {},
        mode=mode,
        source=path,
    )


def find_config(start: Optional[Path] = None) -> Optional[Path]:
    start = start if start is not None else Path.cwd()
    for directory in [start, *start.parents]:
        candidate = directory / ".exactor.yml"
        if candidate.exists():
            return candidate
    return None
