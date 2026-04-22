"""
Hook dispatcher for Claude Code PreToolUse and PostToolUse events.

Claude Code passes a JSON object on stdin. Exit codes:
  0 — allow the tool call to proceed
  2 — block the tool call; stdout is shown to the model as feedback
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .cache import Cache, make_key
from .config import MODE_LOOSE, Config, find_config, load_config
from .router import effective_mode, extract_query, match_rule, run_worker


def _load(config_path: Path | None) -> Config | None:
    path = config_path or find_config()
    if not path:
        return None
    return load_config(path)


def pre_tool_use(config_path: Path | None = None) -> int:
    payload = json.loads(sys.stdin.read())
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    config = _load(config_path)
    if not config:
        return 0

    rule = match_rule(tool_name, tool_input, config)
    if not rule:
        return 0

    if not rule.route_to:
        return 0

    worker = config.workers[rule.route_to]

    # 1. Cache lookup (only if worker opts in)
    cache: Cache | None = None
    cache_key: str | None = None
    if worker.cache:
        cache = Cache(Path(config.cache.path))
        cache_key = make_key(rule.route_to, extract_query(rule, tool_input))
        hit = cache.get(cache_key)
        if hit is not None:
            print(f"[exactor] cache hit for {rule.route_to} → returning stored result\n\n{hit}")
            return 2

    # 2. Run the worker
    result = run_worker(rule, tool_input, config)

    # 3. Store on success (only if worker opts in)
    if result.success and cache is not None and cache_key is not None:
        ttl_hours = worker.cache_ttl_hours or config.cache.default_ttl_hours
        cache.put(cache_key, result.output, ttl_seconds=ttl_hours * 3600)

    if result.success:
        print(f"[exactor] routed {tool_name} → {result.worker_name}\n\n{result.output}")
        return 2

    # 4. Worker failed. Apply mode policy.
    if effective_mode(worker, config) == MODE_LOOSE:
        print(f"[exactor] {result.output} — falling back to raw {tool_name}", file=sys.stderr)
        return 0

    print(result.output)
    return 2


def post_tool_use(config_path: Path | None = None) -> int:
    payload = json.loads(sys.stdin.read())
    tool_output = payload.get("tool_output", "")

    config = _load(config_path)
    if not config:
        return 0

    max_lines = (config.guards or {}).get("max_raw_output_lines")
    if max_lines and isinstance(tool_output, str):
        lines = tool_output.splitlines()
        if len(lines) > max_lines:
            trimmed = "\n".join(lines[:max_lines])
            print(f"[exactor] output trimmed to {max_lines} lines\n\n{trimmed}\n[... {len(lines) - max_lines} lines suppressed]")
            return 2

    return 0
