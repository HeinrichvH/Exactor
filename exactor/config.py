from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class Worker:
    command: str
    description: str = ""


@dataclass
class InterceptRule:
    tool: str
    route_to: Optional[str] = None
    action: Optional[str] = None       # "summarize"
    match: Optional[str] = None        # regex on tool input
    unless: Optional[str] = None       # heuristic: "single_file_absolute_path"
    output_lines_gt: Optional[int] = None


@dataclass
class MemoryConfig:
    backend: str = "file"
    path: str = ".exactor/session"


@dataclass
class Config:
    workers: dict[str, Worker]
    intercept: list[InterceptRule]
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    guards: dict = field(default_factory=dict)


def load_config(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())

    workers = {
        name: Worker(**w) if isinstance(w, dict) else Worker(command=w)
        for name, w in (raw.get("workers") or {}).items()
    }

    intercept = [InterceptRule(**r) for r in (raw.get("intercept") or [])]

    memory_raw = raw.get("memory") or {}
    memory = MemoryConfig(**memory_raw) if memory_raw else MemoryConfig()

    return Config(
        workers=workers,
        intercept=intercept,
        memory=memory,
        guards=raw.get("guards") or {},
    )


def find_config(start: Path = Path.cwd()) -> Optional[Path]:
    for directory in [start, *start.parents]:
        candidate = directory / ".exactor.yml"
        if candidate.exists():
            return candidate
    return None
