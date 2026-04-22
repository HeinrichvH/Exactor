"""
XDG Base Directory resolution for Exactor's runtime files.

Cache (SQLite) goes under $XDG_CACHE_HOME/exactor/; logs under
$XDG_STATE_HOME/exactor/. Both honor the env vars with the usual
~/.cache and ~/.local/state fallbacks, and both are creatable on
demand — callers pass these to Cache()/logging without worrying
about parent directories.
"""
from __future__ import annotations

import os
from pathlib import Path


def _xdg(env_var: str, fallback: str) -> Path:
    raw = os.environ.get(env_var)
    if raw:
        return Path(raw).expanduser()
    return Path.home() / fallback


def cache_dir() -> Path:
    return _xdg("XDG_CACHE_HOME", ".cache") / "exactor"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "exactor"


def default_cache_path() -> Path:
    return cache_dir() / "cache.db"


def default_log_path() -> Path:
    return state_dir() / "exactor.log"
