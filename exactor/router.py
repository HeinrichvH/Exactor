from __future__ import annotations

import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Config, InterceptRule, Worker


@dataclass
class WorkerResult:
    output: str
    success: bool
    worker_name: str


_SINGLE_FILE_RE = re.compile(r"^(cat|head|tail|less)\s+(/[^\s;|&]+)$")


def _is_single_file_absolute_path(command: str) -> bool:
    return bool(_SINGLE_FILE_RE.match(command.strip()))


def _apply_unless(rule: InterceptRule, tool_input: dict) -> bool:
    """Return True if the unless-clause fires (i.e. rule should be skipped)."""
    if rule.unless == "single_file_absolute_path":
        command = tool_input.get("command", "")
        return _is_single_file_absolute_path(command)
    return False


def _extract_query(rule: InterceptRule, tool_input: dict) -> str:
    if rule.tool == "Bash":
        return tool_input.get("command", "")
    if rule.tool in ("WebSearch",):
        return tool_input.get("query", "")
    if rule.tool in ("WebFetch",):
        return tool_input.get("url", "")
    return str(tool_input)


def match_rule(tool_name: str, tool_input: dict, config: Config) -> Optional[InterceptRule]:
    for rule in config.intercept:
        if rule.tool != tool_name:
            continue
        if rule.match:
            subject = tool_input.get("command", "") if tool_name == "Bash" else str(tool_input)
            if not re.search(rule.match, subject):
                continue
        if _apply_unless(rule, tool_input):
            continue
        return rule
    return None


def run_worker(rule: InterceptRule, tool_input: dict, config: Config) -> WorkerResult:
    worker_name = rule.route_to or ""
    worker: Optional[Worker] = config.workers.get(worker_name)
    if not worker:
        raise ValueError(f"Worker '{worker_name}' not defined in config")

    query = _extract_query(rule, tool_input)
    command = worker.command.replace("{query}", shlex.quote(query))

    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=worker.timeout,
        )
    except subprocess.TimeoutExpired:
        return WorkerResult(
            output=f"[exactor] worker '{worker_name}' timed out after {worker.timeout}s",
            success=False,
            worker_name=worker_name,
        )

    if result.returncode != 0:
        return WorkerResult(
            output=f"[exactor] worker '{worker_name}' failed (exit {result.returncode}):\n{result.stderr.strip()}",
            success=False,
            worker_name=worker_name,
        )

    return WorkerResult(
        output=result.stdout.strip(),
        success=True,
        worker_name=worker_name,
    )


def effective_mode(worker: Worker, config: Config) -> str:
    return worker.mode or config.mode
