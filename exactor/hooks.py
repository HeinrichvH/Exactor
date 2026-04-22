"""
Hook dispatcher for Claude Code PreToolUse and PostToolUse events.

Claude Code passes a JSON object on stdin. PreToolUse can respond via the
JSON protocol (stdout) with `permissionDecision: "deny"` + a reason — this
renders as a clean "denied with context" rather than a hook *error* (which
is how exit-2 surfaces). We deny the native call and hand Claude the worker
output as the reason; the model reads that in place of the raw tool result.

Exit 0 in all cases where we emit JSON; exit 0 (no output) for pass-through.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from .cache import Cache, make_key
from .config import MODE_LOOSE, Config, find_config, load_config
from .log import configure as configure_logging, get_logger
from .paths import default_cache_path
from .router import effective_mode, extract_query, match_rule, run_worker


def _load(config_path: Path | None) -> Config | None:
    path = config_path or find_config()
    if not path:
        return None
    return load_config(path)


def _deny(reason: str) -> None:
    """Emit a PreToolUse JSON deny decision with a reason on stdout."""
    json.dump(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        },
        sys.stdout,
    )


def _resolve_cache_path(config: Config) -> Path:
    """Resolve the cache DB path, honoring XDG default when unset.

    A relative `cache.path` is resolved against the .exactor.yml's
    directory (project-local), an absolute path is used verbatim, and
    an unset path falls through to $XDG_CACHE_HOME/exactor/cache.db.
    """
    if not config.cache.path:
        return default_cache_path()
    p = Path(config.cache.path)
    if p.is_absolute():
        return p
    base = config.source.parent if config.source else Path.cwd()
    return base / p


def pre_tool_use(config_path: Path | None = None) -> int:
    """Entry point. Always fail-open: any unexpected error lets the raw tool run.

    Rationale: under a catch-all Claude matcher (".*"), this hook fires for
    every tool call. A bug in Exactor — malformed stdin, missing worker, YAML
    error — must not block the host from making progress. We log the failure
    so it's diagnosable, then exit 0.
    """
    configure_logging()
    log = get_logger()
    try:
        return _pre_tool_use_impl(config_path, log)
    except Exception as e:  # noqa: BLE001 — intentional catch-all; see docstring
        log.exception("hook_crash", extra={"error_type": type(e).__name__})
        sys.stderr.write(
            f"[exactor] hook raised {type(e).__name__}: {e} — falling through to raw tool\n"
        )
        return 0


def _pre_tool_use_impl(config_path: Path | None, log) -> int:
    payload = json.loads(sys.stdin.read())
    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})

    config = _load(config_path)
    if not config:
        log.debug("no_config", extra={"tool": tool_name})
        return 0

    # Re-apply level from config if set — env var still wins.
    configure_logging(level=config.logging.level, path=config.logging.path)

    rule = match_rule(tool_name, tool_input, config)
    if not rule:
        log.debug("no_match", extra={"tool": tool_name})
        return 0

    if not rule.route_to:
        log.debug("rule_no_route", extra={"tool": tool_name})
        return 0

    worker = config.workers.get(rule.route_to)
    if worker is None:
        # Defense in depth — load_config validates this, but belt-and-braces
        # so runtime never crashes on a stale/partial config.
        log.error("unknown_worker", extra={"tool": tool_name, "worker": rule.route_to})
        sys.stderr.write(
            f"[exactor] rule for {tool_name} routes to unknown worker "
            f"'{rule.route_to}' — falling through to raw tool\n"
        )
        return 0

    query = extract_query(rule, tool_input)

    # 1. Cache lookup (only if worker opts in)
    cache: Cache | None = None
    cache_key: str | None = None
    cache_status = "disabled"
    if worker.cache:
        cache = Cache(_resolve_cache_path(config))
        cache_key = make_key(rule.route_to, query)
        hit = cache.get(cache_key)
        if hit is not None:
            log.info(
                "route",
                extra={
                    "tool": tool_name,
                    "worker": rule.route_to,
                    "cache": "hit",
                    "outcome": "cache_hit",
                },
            )
            _deny(f"[exactor] cache hit for {rule.route_to} → returning stored result\n\n{hit}")
            return 0
        cache_status = "miss"

    # 2. Run the worker
    t0 = time.monotonic()
    result = run_worker(rule, tool_input, config)
    duration_ms = int((time.monotonic() - t0) * 1000)

    # 3. Store on success (only if worker opts in)
    if result.success and cache is not None and cache_key is not None:
        ttl_hours = worker.cache_ttl_hours or config.cache.default_ttl_hours
        cache.put(cache_key, result.output, ttl_seconds=ttl_hours * 3600)

    if result.success:
        log.info(
            "route",
            extra={
                "tool": tool_name,
                "worker": rule.route_to,
                "cache": cache_status,
                "duration_ms": duration_ms,
                "outcome": "ok",
            },
        )
        _deny(f"[exactor] routed {tool_name} → {result.worker_name}\n\n{result.output}")
        return 0

    # 4. Worker failed. Apply mode policy.
    mode = effective_mode(worker, config)
    log.warning(
        "worker_failed",
        extra={
            "tool": tool_name,
            "worker": rule.route_to,
            "duration_ms": duration_ms,
            "mode": mode,
            "outcome": "fallback" if mode == MODE_LOOSE else "deny",
        },
    )
    if mode == MODE_LOOSE:
        # Loose fallback: log to a separate channel so it doesn't reach the model.
        # Claude Code's hook log shows the warning; the model proceeds with raw tool.
        sys.stderr.write(f"[exactor] {result.output} — falling back to raw {tool_name}\n")
        return 0

    _deny(result.output)
    return 0


def post_tool_use(config_path: Path | None = None) -> int:
    """Fail-open for the same reason as pre_tool_use."""
    configure_logging()
    log = get_logger()
    try:
        return _post_tool_use_impl(config_path, log)
    except Exception as e:  # noqa: BLE001
        log.exception("post_hook_crash", extra={"error_type": type(e).__name__})
        sys.stderr.write(
            f"[exactor] post-hook raised {type(e).__name__}: {e} — passing output through unchanged\n"
        )
        return 0


def _post_tool_use_impl(config_path: Path | None, log) -> int:
    payload = json.loads(sys.stdin.read())
    tool_output = payload.get("tool_output", "")

    config = _load(config_path)
    if not config:
        return 0

    configure_logging(level=config.logging.level, path=config.logging.path)

    max_lines = (config.guards or {}).get("max_raw_output_lines")
    if max_lines and isinstance(tool_output, str):
        lines = tool_output.splitlines()
        if len(lines) > max_lines:
            trimmed = "\n".join(lines[:max_lines])
            log.info(
                "output_trimmed",
                extra={"limit": max_lines, "actual": len(lines)},
            )
            print(
                f"[exactor] output trimmed to {max_lines} lines\n\n{trimmed}\n[... {len(lines) - max_lines} lines suppressed]",
                file=sys.stderr,
            )
            return 2

    return 0
