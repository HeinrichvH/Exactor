"""Test-wide isolation: never let a test write to the real XDG dirs.

Hook and router paths both lazily configure the `exactor` logger against
$XDG_STATE_HOME. Without this fixture, running `pytest` would scribble
into ~/.local/state/exactor/exactor.log on the developer's machine.
"""
from __future__ import annotations

import logging

import pytest


@pytest.fixture(autouse=True)
def _isolate_xdg(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.delenv("EXACTOR_LOG_LEVEL", raising=False)
    monkeypatch.delenv("EXACTOR_LOG_FILE", raising=False)

    logger = logging.getLogger("exactor")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    if hasattr(logger, "_exactor_configured"):
        delattr(logger, "_exactor_configured")
    yield
    for h in list(logger.handlers):
        logger.removeHandler(h)
    if hasattr(logger, "_exactor_configured"):
        delattr(logger, "_exactor_configured")
