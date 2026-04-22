import io
import json
import sys

import pytest

from exactor.config import load_config
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


# ---------------------------------------------------------------------------
# Fail-open contract
#
# Under a catch-all Claude matcher (".*"), every tool call fires this hook.
# A bug in Exactor must never block the host from making progress — if we
# can't produce a deliberate deny decision, we exit 0 and let the raw tool run.
# ---------------------------------------------------------------------------


def test_fail_open_when_tool_has_no_rule(tmp_path, monkeypatch, capsys):
    # Catch-all scenario: hook fires on a tool we don't intercept.
    config_yaml = """
workers:
  echo:
    command: "echo ignored"
intercept:
  - tool: WebSearch
    route_to: echo
"""
    code, out, err = _run_hook(
        {"tool_name": "Bash", "tool_input": {"command": "ls"}},
        config_yaml, tmp_path, monkeypatch, capsys,
    )
    assert code == 0
    assert out == ""
    assert err == ""  # silent pass-through, no noise in hook log


def test_fail_open_on_missing_config(tmp_path, monkeypatch, capsys):
    # No .exactor.yml anywhere in the ancestry.
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}}
    )))
    monkeypatch.chdir(tmp_path)
    assert pre_tool_use() == 0


def test_fail_open_on_malformed_stdin(tmp_path, monkeypatch, capsys):
    # Whatever Claude sends, we don't crash.
    (tmp_path / ".exactor.yml").write_text("workers: {}\nintercept: []\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO("not-json-at-all"))
    monkeypatch.chdir(tmp_path)
    code = pre_tool_use()
    err = capsys.readouterr().err
    assert code == 0
    assert "[exactor] hook raised" in err


def test_fail_open_on_malformed_yaml(tmp_path, monkeypatch, capsys):
    (tmp_path / ".exactor.yml").write_text("workers: [this is: not valid\n")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}}
    )))
    monkeypatch.chdir(tmp_path)
    code = pre_tool_use()
    err = capsys.readouterr().err
    assert code == 0
    assert "[exactor] hook raised" in err


def test_load_config_rejects_rule_with_unknown_worker(tmp_path):
    # Misconfiguration surfaces at `exactor check` time, not at tool-fire time.
    cfg = tmp_path / ".exactor.yml"
    cfg.write_text("""
workers:
  real:
    command: "echo hi"
intercept:
  - tool: WebSearch
    route_to: typo-worker
""")
    with pytest.raises(ValueError, match="route_to='typo-worker'"):
        load_config(cfg)


def test_fail_open_when_runtime_worker_missing(tmp_path, monkeypatch, capsys):
    # Belt-and-braces: even if load_config validation is somehow bypassed
    # (e.g. in-memory construction, stale import), runtime still falls open.
    from exactor.config import Config, InterceptRule, Worker

    config = Config(
        workers={},
        intercept=[InterceptRule(tool="WebSearch", route_to="ghost", query_field="query")],
    )
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(
        {"tool_name": "WebSearch", "tool_input": {"query": "x"}}
    )))
    # Patch _load to return our hand-built config (bypasses load_config validation).
    from exactor import hooks as hooks_mod
    monkeypatch.setattr(hooks_mod, "_load", lambda _: config)

    code = pre_tool_use()
    err = capsys.readouterr().err
    assert code == 0
    assert "unknown worker 'ghost'" in err
