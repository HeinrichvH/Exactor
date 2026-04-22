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
    assert code == 2
    assert "failed" in out


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
    code, _, err = _run_hook(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 0
    assert "falling back" in err


def test_successful_worker_always_blocks_with_output(tmp_path, monkeypatch, capsys):
    config_yaml = """
mode: loose
workers:
  echo:
    command: "echo hello-{query}"
intercept:
  - tool: WebSearch
    route_to: echo
"""
    code, out, _ = _run_hook(
        {"tool_name": "WebSearch", "tool_input": {"query": "world"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 2
    assert "hello-world" in out
