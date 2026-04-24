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
    query_template: Optional[str] = None  # Python str.format-style template interpolating
                                          # multiple tool_input fields, e.g.
                                          #   "find '{pattern}' in path '{path}'"
                                          # Takes precedence over query_field. Missing keys
                                          # render as the empty string.
    match: Optional[str] = None        # regex on the extracted query; rule skipped if it fails
    unless: Optional[str] = None       # named predicate, e.g. "single_file_absolute_path";
                                       # rule skipped if the predicate fires
    unless_match: Optional[str] = None # regex on the extracted query; rule skipped if it matches.
                                       # Symmetric with `match:`, more flexible than named
                                       # predicates for query-shape heuristics.
    output_lines_gt: Optional[int] = None


@dataclass
class CacheConfig:
    # None = resolve to paths.default_cache_path() at use time (XDG cache dir).
    # Set explicitly when you want a project-local DB instead.
    path: Optional[str] = None
    default_ttl_hours: int = 24


@dataclass
class LoggingConfig:
    level: str = "INFO"                # TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL
    # None = resolve to paths.default_log_path() at use time (XDG state dir).
    path: Optional[str] = None


# Memory adapter — recall-first subsystem.
#
# Separate from intercept rules because the output semantics differ: an
# intercept rule REPLACES a tool result; a memory recall AUGMENTS the prompt
# via UserPromptSubmit's additionalContext. Keeping it a distinct block
# avoids overloading intercept rules with a "what do I do with the output"
# field before we've seen enough lifecycle integrations to abstract cleanly.
@dataclass
class MemoryRecallConfig:
    # Hook event that triggers recall. Only UserPromptSubmit is supported
    # today; store-side events (Stop/PreCompact/SessionEnd) will land later.
    # NB: the field is `event:` not `on:` — YAML 1.1 parses bare `on` as
    # the boolean True, which silently corrupts the key.
    event: str = "UserPromptSubmit"
    # Inline worker spec — a memory backend is always single-purpose for a
    # given project, so inlining beats the named-worker-reference dance.
    worker: Optional[Worker] = None


@dataclass
class MemoryStoreConfig:
    # Multiple events fire the same worker. The event names here must match
    # what Claude Code's settings.json wires to `exactor hook <event>` — we
    # deliberately do NOT allowlist, because the source of truth for "which
    # hooks exist" is Claude Code, not exactor. Users can wire SubagentStop
    # or any future event without waiting on an exactor release.
    events: list[str] = field(default_factory=list)
    worker: Optional[Worker] = None
    # Optional adapter called after the store worker succeeds. The store
    # worker's stdout is piped to the adapter's stdin as JSON. Users can
    # plug in any backend (Mem0, Notion, custom DB) without coupling
    # to exactor's internals.
    adapter: Optional[Worker] = None


@dataclass
class MemoryConfig:
    recall: Optional[MemoryRecallConfig] = None
    store: Optional[MemoryStoreConfig] = None


@dataclass
class Config:
    workers: dict[str, Worker]
    intercept: list[InterceptRule]
    cache: CacheConfig = field(default_factory=CacheConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
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

    for i, rule in enumerate(intercept):
        if rule.route_to and rule.route_to not in workers:
            raise ValueError(
                f"intercept[{i}] (tool={rule.tool}): route_to='{rule.route_to}' "
                f"does not match any defined worker (have: {sorted(workers) or 'none'})"
            )

    cache_raw = raw.get("cache") or {}
    cache = CacheConfig(**cache_raw) if cache_raw else CacheConfig()

    logging_raw = raw.get("logging") or {}
    logging_cfg = LoggingConfig(**logging_raw) if logging_raw else LoggingConfig()

    mode = raw.get("mode", MODE_STRICT)
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {sorted(VALID_MODES)}, got '{mode}'")

    memory_raw = raw.get("memory") or {}
    memory = _parse_memory(memory_raw)

    return Config(
        workers=workers,
        intercept=intercept,
        cache=cache,
        logging=logging_cfg,
        memory=memory,
        guards=raw.get("guards") or {},
        mode=mode,
        source=path,
    )


def _parse_memory_worker(raw: dict, path: str) -> Worker:
    worker_raw = raw.get("worker")
    if not worker_raw:
        raise ValueError(f"{path}: `worker` is required")
    worker = Worker(**worker_raw) if isinstance(worker_raw, dict) else Worker(command=worker_raw)
    if worker.mode and worker.mode not in VALID_MODES:
        raise ValueError(f"{path}.worker: mode must be one of {sorted(VALID_MODES)}, got '{worker.mode}'")
    if worker.stdin not in VALID_STDIN:
        raise ValueError(f"{path}.worker: stdin must be one of {sorted(VALID_STDIN)}, got '{worker.stdin}'")
    return worker


def _parse_memory(raw: dict) -> MemoryConfig:
    recall: Optional[MemoryRecallConfig] = None
    store: Optional[MemoryStoreConfig] = None

    if "recall" in raw:
        recall_raw = raw.get("recall") or {}
        worker = _parse_memory_worker(recall_raw, "memory.recall")
        event = recall_raw.get("event", "UserPromptSubmit")
        if not isinstance(event, str) or not event:
            raise ValueError("memory.recall.event: must be a non-empty string")
        recall = MemoryRecallConfig(event=event, worker=worker)

    if "store" in raw:
        store_raw = raw.get("store") or {}
        worker = _parse_memory_worker(store_raw, "memory.store")
        events = store_raw.get("events", [])
        if not isinstance(events, list) or not events:
            raise ValueError("memory.store.events: must be a non-empty list")
        for ev in events:
            if not isinstance(ev, str) or not ev:
                raise ValueError(f"memory.store.events: entries must be non-empty strings, got {ev!r}")
        adapter: Optional[Worker] = None
        if "adapter" in store_raw:
            adapter_raw = store_raw["adapter"]
            adapter = Worker(**adapter_raw) if isinstance(adapter_raw, dict) else Worker(command=adapter_raw)
            if adapter.mode and adapter.mode not in VALID_MODES:
                raise ValueError(
                    f"memory.store.adapter: mode must be one of {sorted(VALID_MODES)}, got '{adapter.mode}'"
                )
            if adapter.stdin not in VALID_STDIN:
                raise ValueError(
                    f"memory.store.adapter: stdin must be one of {sorted(VALID_STDIN)}, got '{adapter.stdin}'"
                )
        store = MemoryStoreConfig(events=list(events), worker=worker, adapter=adapter)

    return MemoryConfig(recall=recall, store=store)


def find_config(start: Optional[Path] = None) -> Optional[Path]:
    start = start if start is not None else Path.cwd()
    for directory in [start, *start.parents]:
        candidate = directory / ".exactor.yml"
        if candidate.exists():
            return candidate
    return None
