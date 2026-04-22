import io
import json
import sys

import pytest

from exactor.hooks import pre_tool_use


def _run_hook(payload: dict, config_yaml: str, tmp_path, monkeypatch, capsys) -> tuple[int, str, str]:
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text(config_yaml)

    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    monkeypatch.chdir(tmp_path)

    code = pre_tool_use()
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _deny_reason(stdout: str) -> str:
    """Parse the PreToolUse JSON deny payload and return its reason string."""
    payload = json.loads(stdout)
    hso = payload["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    return hso["permissionDecisionReason"]


def test_strict_mode_blocks_on_worker_failure(tmp_path, monkeypatch, capsys):
    config_yaml = """
mode: strict
workers:
  broken:
    command: "exit 1"
intercept:
  - tool: WebSearch
    route_to: broken
"""
    code, out, err = _run_hook(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 0
    assert "failed" in _deny_reason(out)


def test_loose_mode_falls_back_on_failure(tmp_path, monkeypatch, capsys):
    config_yaml = """
mode: loose
workers:
  broken:
    command: "exit 1"
intercept:
  - tool: WebSearch
    route_to: broken
"""
    code, out, err = _run_hook(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 0
    assert out == ""
    assert "falling back" in err


def test_per_worker_loose_overrides_strict_default(tmp_path, monkeypatch, capsys):
    config_yaml = """
mode: strict
workers:
  broken:
    command: "exit 1"
    mode: loose
intercept:
  - tool: WebSearch
    route_to: broken
"""
    code, out, err = _run_hook(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 0
    assert out == ""
    assert "falling back" in err


def test_successful_worker_denies_with_output(tmp_path, monkeypatch, capsys):
    config_yaml = """
mode: loose
workers:
  echo:
    command: "echo hello-{query}"
intercept:
  - tool: WebSearch
    query_field: query
    route_to: echo
"""
    code, out, err = _run_hook(
        {"tool_name": "WebSearch", "tool_input": {"query": "world"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 0
    assert "hello-world" in _deny_reason(out)


def test_cache_miss_then_hit(tmp_path, monkeypatch, capsys):
    # Worker writes a file every time it runs; we assert it runs exactly once.
    counter = tmp_path / "runs"
    config_yaml = f"""
cache:
  path: {tmp_path}/cache.db
  default_ttl_hours: 1
workers:
  counter:
    command: "echo run && echo 1 >> {counter}"
    cache: true
intercept:
  - tool: WebSearch
    route_to: counter
"""
    payload = {"tool_name": "WebSearch", "tool_input": {"query": "same"}}

    # First call: miss, run worker, store
    code1, out1, _ = _run_hook(payload, config_yaml, tmp_path, monkeypatch, capsys)
    assert code1 == 0
    assert "routed" in _deny_reason(out1)
    assert counter.read_text().count("1") == 1

    # Second call: hit, do NOT run worker
    code2, out2, _ = _run_hook(payload, config_yaml, tmp_path, monkeypatch, capsys)
    assert code2 == 0
    assert "cache hit" in _deny_reason(out2)
    assert counter.read_text().count("1") == 1  # unchanged


def test_cache_disabled_by_default(tmp_path, monkeypatch, capsys):
    counter = tmp_path / "runs"
    config_yaml = f"""
cache:
  path: {tmp_path}/cache.db
workers:
  counter:
    command: "echo run && echo 1 >> {counter}"
    # cache omitted — defaults to false
intercept:
  - tool: WebSearch
    route_to: counter
"""
    payload = {"tool_name": "WebSearch", "tool_input": {"query": "same"}}
    _run_hook(payload, config_yaml, tmp_path, monkeypatch, capsys)
    _run_hook(payload, config_yaml, tmp_path, monkeypatch, capsys)
    assert counter.read_text().count("1") == 2  # ran twice


def test_cache_key_differs_by_worker(tmp_path, monkeypatch, capsys):
    # Same query text through two different workers → separate cache entries.
    counter_a = tmp_path / "a"
    counter_b = tmp_path / "b"
    config_yaml = f"""
cache:
  path: {tmp_path}/cache.db
workers:
  a:
    command: "echo a && echo 1 >> {counter_a}"
    cache: true
  b:
    command: "echo b && echo 1 >> {counter_b}"
    cache: true
intercept:
  - tool: WebSearch
    route_to: a
  - tool: WebFetch
    route_to: b
"""
    # WebSearch.query and WebFetch.url happen to share the same string.
    _run_hook({"tool_name": "WebSearch", "tool_input": {"query": "same"}}, config_yaml, tmp_path, monkeypatch, capsys)
    _run_hook({"tool_name": "WebFetch", "tool_input": {"url": "same"}}, config_yaml, tmp_path, monkeypatch, capsys)
    assert counter_a.read_text().count("1") == 1
    assert counter_b.read_text().count("1") == 1
