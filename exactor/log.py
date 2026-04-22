"""
Structured JSON-lines logging for Exactor.

Every hook invocation, worker run, and routing decision goes to a
rotating file under $XDG_STATE_HOME/exactor/exactor.log. One JSON
object per line — grep with `jq`, ingest into Loki/Datadog if you
ever want to, tail -f while debugging.

Format:
  {"ts": "2026-04-22T21:55:01.123Z", "level": "INFO",
   "event": "route", "tool": "Grep", "worker": "explore",
   "duration_ms": 12345, "cache": "miss"}

Stdout stays reserved for the Claude Code hook protocol; stderr is
only used when level >= WARNING or when EXACTOR_LOG_LEVEL=debug to
mirror everything there (useful under `claude --debug`).
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
import time
from pathlib import Path
from typing import Any

from .paths import default_log_path


_LOGGER_NAME = "exactor"
_LOG_BYTES = 5 * 1024 * 1024       # 5 MiB per file
_LOG_BACKUPS = 3                   # keep 3 rotated files = ~20 MiB cap
_ISO_FMT = "%Y-%m-%dT%H:%M:%S"


class _JsonFormatter(logging.Formatter):
    """One JSON object per log record, newline-terminated.

    Reserved keys (ts, level, event, msg) come first; everything the
    caller attaches via `extra={...}` is merged in after. We deliberately
    skip Python's LogRecord boilerplate (pathname, lineno, etc.) — that
    noise helps local debugging of the logger itself, not the hook.
    """

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self._iso_ts(record.created),
            "level": record.levelname,
        }
        # Message as "event" for the common one-word case, otherwise msg.
        message = record.getMessage()
        if " " in message or len(message) > 40:
            payload["msg"] = message
        else:
            payload["event"] = message

        for k, v in record.__dict__.items():
            if k in self._RESERVED or k.startswith("_"):
                continue
            payload[k] = v

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, ensure_ascii=False)

    @staticmethod
    def _iso_ts(created: float) -> str:
        millis = int((created - int(created)) * 1000)
        return f"{time.strftime(_ISO_FMT, time.gmtime(created))}.{millis:03d}Z"


def _resolve_level(override: str | None) -> int:
    raw = override or os.environ.get("EXACTOR_LOG_LEVEL") or "INFO"
    return logging.getLevelName(raw.upper()) if isinstance(raw, str) else logging.INFO


def _resolve_path(override: str | None) -> Path:
    raw = override or os.environ.get("EXACTOR_LOG_FILE")
    return Path(raw).expanduser() if raw else default_log_path()


def configure(level: str | None = None, path: str | None = None) -> logging.Logger:
    """Idempotently attach handlers to the `exactor` logger and return it.

    Safe to call multiple times — hooks.py and cli.py both call this at
    entry, and we don't want duplicate handlers stacking up in tests.
    """
    logger = logging.getLogger(_LOGGER_NAME)
    resolved_level = _resolve_level(level)
    logger.setLevel(resolved_level)
    logger.propagate = False

    if getattr(logger, "_exactor_configured", False):
        # Update level on subsequent calls — e.g. CLI --verbose after config load.
        logger.setLevel(resolved_level)
        return logger

    log_path = _resolve_path(path)
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=_LOG_BYTES,
            backupCount=_LOG_BACKUPS,
            encoding="utf-8",
        )
    except OSError:
        # Read-only fs, permission denied, sandbox — degrade to stderr so
        # the hook still works. Never let logger setup crash the dispatch.
        handler = logging.StreamHandler(sys.stderr)

    handler.setFormatter(_JsonFormatter())
    logger.addHandler(handler)

    # Stderr mirror only when DEBUG — stderr feeds Claude Code's debug log,
    # so INFO-level mirroring would flood `claude --debug` output.
    if resolved_level <= logging.DEBUG:
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(_JsonFormatter())
        logger.addHandler(stderr_handler)

    logger._exactor_configured = True  # type: ignore[attr-defined]
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(_LOGGER_NAME)
