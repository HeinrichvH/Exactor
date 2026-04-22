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

from .config import Config, find_config, load_config
from .router import match_rule, run_worker


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

    if rule.route_to:
        output = run_worker(rule, tool_input, config)
        print(f"[exactor] routed {tool_name} → {rule.route_to}\n\n{output}")
        return 2

    return 0


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
