from __future__ import annotations

import json
import logging
from pathlib import Path

from exactor import log as log_module
from exactor import paths


def _read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_xdg_paths_honor_env_vars(tmp_path):
    assert paths.cache_dir() == tmp_path / "cache" / "exactor"
    assert paths.state_dir() == tmp_path / "state" / "exactor"
    assert paths.default_log_path() == tmp_path / "state" / "exactor" / "exactor.log"


def test_xdg_paths_fallback_to_home(monkeypatch):
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    assert paths.cache_dir() == Path.home() / ".cache" / "exactor"
    assert paths.state_dir() == Path.home() / ".local" / "state" / "exactor"


def test_configure_writes_json_lines(tmp_path):
    log = log_module.configure()
    log.info("route", extra={"tool": "Grep", "worker": "explore", "cache": "miss"})

    log_path = paths.default_log_path()
    assert log_path.exists()

    records = _read_log(log_path)
    assert len(records) == 1
    rec = records[0]
    assert rec["level"] == "INFO"
    assert rec["event"] == "route"
    assert rec["tool"] == "Grep"
    assert rec["worker"] == "explore"
    assert rec["cache"] == "miss"
    assert "ts" in rec and rec["ts"].endswith("Z")


def test_configure_is_idempotent():
    first = log_module.configure()
    before = len(first.handlers)
    second = log_module.configure()
    assert first is second
    assert len(second.handlers) == before


def test_env_var_sets_level(monkeypatch):
    monkeypatch.setenv("EXACTOR_LOG_LEVEL", "DEBUG")
    log = log_module.configure()
    assert log.level == logging.DEBUG
    # DEBUG adds a stderr mirror handler on top of the file handler.
    assert len(log.handlers) == 2


def test_long_messages_become_msg_not_event():
    log = log_module.configure()
    log.info("this is a free-form message with spaces and detail")
    records = _read_log(paths.default_log_path())
    assert "msg" in records[0]
    assert "event" not in records[0]


def test_oserror_on_file_handler_degrades_to_stderr(monkeypatch, tmp_path, capsys):
    def _boom(*a, **kw):
        raise OSError("read-only fs")

    monkeypatch.setattr(log_module.logging.handlers, "RotatingFileHandler", _boom)

    log = log_module.configure()
    log.warning("fallback_test")

    captured = capsys.readouterr()
    assert "fallback_test" in captured.err
