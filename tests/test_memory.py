from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from exactor import hooks
from exactor.config import MemoryConfig, MemoryRecallConfig, MemoryStoreConfig, load_config


def _write_config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / ".exactor.yml"
    cfg.write_text(body)
    return cfg


def _run_hook(monkeypatch, capsys, cfg_path: Path, stdin: dict) -> tuple[int, str, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin)))
    rc = hooks.user_prompt_submit(config_path=cfg_path)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


# ---------- config parsing ----------

def test_memory_block_parses(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    worker:
      command: "echo hit"
""")
    config = load_config(cfg)
    assert config.memory.recall is not None
    assert config.memory.recall.event == "UserPromptSubmit"
    assert config.memory.recall.worker.command == "echo hit"


def test_memory_recall_requires_worker(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall: {}
""")
    with pytest.raises(ValueError, match="worker.*required"):
        load_config(cfg)


def test_memory_recall_accepts_any_event_string(tmp_path):
    # Exactor does not allowlist event names — Claude Code is the source
    # of truth for which hooks exist. We only insist the value is a string.
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    event: SomeFutureHook
    worker:
      command: "echo hit"
""")
    config = load_config(cfg)
    assert config.memory.recall.event == "SomeFutureHook"


def test_no_memory_block_is_ok(tmp_path):
    cfg = _write_config(tmp_path, "workers: {}\nintercept: []\n")
    config = load_config(cfg)
    assert config.memory == MemoryConfig()
    assert config.memory.recall is None


# ---------- hook behavior ----------

def test_recall_injects_additional_context(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    worker:
      command: "echo past memory about {query}"
""")
    rc, out, _err = _run_hook(
        monkeypatch, capsys, cfg,
        {"hook_event_name": "UserPromptSubmit", "prompt": "what did we decide about caching"},
    )
    assert rc == 0
    payload = json.loads(out)
    assert payload["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "past memory about" in payload["hookSpecificOutput"]["additionalContext"]


def test_recall_empty_output_emits_nothing(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    worker:
      command: "true"
""")
    rc, out, _err = _run_hook(
        monkeypatch, capsys, cfg,
        {"prompt": "anything"},
    )
    assert rc == 0
    assert out == ""   # no JSON payload when recall is empty


def test_recall_worker_failure_fails_open(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    worker:
      command: "false"
""")
    rc, out, err = _run_hook(
        monkeypatch, capsys, cfg,
        {"prompt": "anything"},
    )
    assert rc == 0              # fail-open: never block the prompt
    assert out == ""            # no additionalContext
    assert "memory recall failed" in err


def test_recall_no_config_passes_through(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, "workers: {}\nintercept: []\n")
    rc, out, _err = _run_hook(
        monkeypatch, capsys, cfg,
        {"prompt": "anything"},
    )
    assert rc == 0
    assert out == ""


def test_recall_empty_prompt_skipped(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    worker:
      command: "echo should-not-fire"
""")
    rc, out, _err = _run_hook(
        monkeypatch, capsys, cfg,
        {"prompt": "   "},
    )
    assert rc == 0
    assert out == ""


def test_recall_output_clamped_to_10kb(tmp_path, monkeypatch, capsys):
    # Worker emits ~12 KiB of 'x'. We expect the clamp to bring it down.
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  recall:
    worker:
      command: "python3 -c 'print(\\"x\\" * 12000)'"
""")
    rc, out, _err = _run_hook(
        monkeypatch, capsys, cfg,
        {"prompt": "anything"},
    )
    assert rc == 0
    payload = json.loads(out)
    ctx = payload["hookSpecificOutput"]["additionalContext"]
    assert len(ctx.encode("utf-8")) <= 10 * 1024


# ---------- store-side config ----------

def test_store_block_parses(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [Stop, PreCompact, SessionEnd]
    worker:
      command: "echo stored"
""")
    config = load_config(cfg)
    assert config.memory.store is not None
    assert config.memory.store.events == ["Stop", "PreCompact", "SessionEnd"]
    assert config.memory.store.worker.command == "echo stored"


def test_store_rejects_empty_events(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: []
    worker:
      command: "echo stored"
""")
    with pytest.raises(ValueError, match="non-empty list"):
        load_config(cfg)


def test_store_requires_worker(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [Stop]
""")
    with pytest.raises(ValueError, match="worker.*required"):
        load_config(cfg)


def test_store_accepts_any_event_string(tmp_path):
    # No allowlist — Claude Code is the source of truth for event names.
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [SubagentStop, SomeFutureHook]
    worker:
      command: "echo stored"
""")
    config = load_config(cfg)
    assert "SubagentStop" in config.memory.store.events
    assert "SomeFutureHook" in config.memory.store.events


# ---------- store-side hook behavior ----------

def _run_store(monkeypatch, capsys, cfg_path, event, stdin_payload, echo_stdin_to=None):
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(stdin_payload)))
    rc = hooks._store_event(event, config_path=cfg_path)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def test_store_fires_worker_when_event_configured(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "stored.txt"
    cfg = _write_config(tmp_path, f"""
workers: {{}}
intercept: []
memory:
  store:
    events: [Stop, PreCompact]
    worker:
      command: "cat > {sink}"
""")
    rc, _out, _err = _run_store(
        monkeypatch, capsys, cfg, "Stop",
        {"hook_event_name": "Stop", "session_id": "abc", "transcript_path": "/tmp/t.jsonl"},
    )
    assert rc == 0
    assert sink.exists()
    stored = json.loads(sink.read_text())
    assert stored["session_id"] == "abc"
    assert stored["transcript_path"] == "/tmp/t.jsonl"


def test_store_skips_unwired_event(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "stored.txt"
    cfg = _write_config(tmp_path, f"""
workers: {{}}
intercept: []
memory:
  store:
    events: [Stop]
    worker:
      command: "cat > {sink}"
""")
    rc, _out, _err = _run_store(
        monkeypatch, capsys, cfg, "PreCompact",
        {"hook_event_name": "PreCompact"},
    )
    assert rc == 0
    assert not sink.exists()    # worker didn't fire


def test_store_no_config_passes_through(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, "workers: {}\nintercept: []\n")
    rc, _out, _err = _run_store(
        monkeypatch, capsys, cfg, "Stop", {"hook_event_name": "Stop"},
    )
    assert rc == 0


def test_store_worker_failure_fails_open(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [Stop]
    worker:
      command: "false"
""")
    rc, _out, err = _run_store(
        monkeypatch, capsys, cfg, "Stop", {"hook_event_name": "Stop"},
    )
    assert rc == 0              # fail-open
    assert "memory store (Stop) failed" in err


# ---------- adapter config + hook behavior ----------

def test_adapter_block_parses(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "echo stored"
    adapter:
      command: "cat"
      mode: loose
""")
    config = load_config(cfg)
    assert config.memory.store.adapter is not None
    assert config.memory.store.adapter.command == "cat"
    assert config.memory.store.adapter.mode == "loose"


def test_adapter_string_shorthand_parses(tmp_path):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "echo stored"
    adapter: "python /path/to/adapter.py"
""")
    config = load_config(cfg)
    assert config.memory.store.adapter.command == "python /path/to/adapter.py"


def test_adapter_receives_store_worker_stdout(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "adapter_input.txt"
    cfg = _write_config(tmp_path, f"""
workers: {{}}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "echo memories-json"
    adapter:
      command: "cat > {sink}"
""")
    _run_store(
        monkeypatch, capsys, cfg, "PreCompact",
        {"hook_event_name": "PreCompact", "session_id": "s1", "transcript_path": "/t.jsonl"},
    )
    assert sink.exists()
    assert "memories-json" in sink.read_text()


def test_adapter_not_called_when_store_worker_fails(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "adapter_input.txt"
    cfg = _write_config(tmp_path, f"""
workers: {{}}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "false"
    adapter:
      command: "cat > {sink}"
""")
    rc, _out, err = _run_store(
        monkeypatch, capsys, cfg, "PreCompact",
        {"hook_event_name": "PreCompact"},
    )
    assert rc == 0
    assert not sink.exists()   # adapter never ran


def test_adapter_not_called_when_store_worker_emits_nothing(tmp_path, monkeypatch, capsys):
    sink = tmp_path / "adapter_input.txt"
    cfg = _write_config(tmp_path, f"""
workers: {{}}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "true"
    adapter:
      command: "cat > {sink}"
""")
    _run_store(
        monkeypatch, capsys, cfg, "PreCompact",
        {"hook_event_name": "PreCompact"},
    )
    assert not sink.exists()   # adapter skipped — no output to forward


def test_adapter_failure_fails_open(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "echo memories-json"
    adapter:
      command: "false"
""")
    rc, _out, err = _run_store(
        monkeypatch, capsys, cfg, "PreCompact",
        {"hook_event_name": "PreCompact"},
    )
    assert rc == 0   # adapter failure never blocks
    assert "memory adapter failed" in err


def test_no_adapter_configured_is_fine(tmp_path, monkeypatch, capsys):
    cfg = _write_config(tmp_path, """
workers: {}
intercept: []
memory:
  store:
    events: [PreCompact]
    worker:
      command: "echo memories-json"
""")
    rc, _out, err = _run_store(
        monkeypatch, capsys, cfg, "PreCompact",
        {"hook_event_name": "PreCompact"},
    )
    assert rc == 0
    assert "adapter" not in err


def test_recall_crash_in_hook_fails_open(tmp_path, monkeypatch, capsys):
    # Malformed JSON on stdin → top-level except catches, stderr gets a note,
    # exit 0 (never block the prompt).
    monkeypatch.setattr(sys, "stdin", io.StringIO("not json"))
    rc = hooks.user_prompt_submit(config_path=tmp_path / ".exactor.yml")
    captured = capsys.readouterr()
    assert rc == 0
    assert "user-prompt hook raised" in captured.err
