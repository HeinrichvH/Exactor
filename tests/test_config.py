from pathlib import Path
import pytest
from exactor.config import load_config

FIXTURE = Path(__file__).parent / "fixtures" / "basic.exactor.yml"


def test_load_workers(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("""
workers:
  research:
    command: "research {query}"
  explore:
    command: "explore {query}"
intercept: []
""")
    config = load_config(cfg_file)
    assert "research" in config.workers
    assert config.workers["research"].command == "research {query}"
    assert "explore" in config.workers


def test_load_intercept_rules(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("""
workers:
  research:
    command: "research {query}"
intercept:
  - tool: WebSearch
    route_to: research
  - tool: Bash
    match: "^grep\\\\b"
    route_to: research
    unless: single_file_absolute_path
""")
    config = load_config(cfg_file)
    assert len(config.intercept) == 2
    assert config.intercept[0].tool == "WebSearch"
    assert config.intercept[1].unless == "single_file_absolute_path"


def test_default_memory_backend(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("workers: {}\nintercept: []\n")
    config = load_config(cfg_file)
    assert config.memory.backend == "file"


def test_default_mode_is_strict(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("workers: {}\nintercept: []\n")
    config = load_config(cfg_file)
    assert config.mode == "strict"


def test_loose_mode_accepted(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("mode: loose\nworkers: {}\nintercept: []\n")
    config = load_config(cfg_file)
    assert config.mode == "loose"


def test_invalid_mode_rejected(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("mode: chaotic\nworkers: {}\nintercept: []\n")
    with pytest.raises(ValueError, match="mode must be one of"):
        load_config(cfg_file)


def test_per_worker_mode_override(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("""
mode: strict
workers:
  research:
    command: "research {query}"
    mode: loose
intercept: []
""")
    config = load_config(cfg_file)
    assert config.mode == "strict"
    assert config.workers["research"].mode == "loose"


def test_worker_structured_args_loaded(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("""
workers:
  research:
    command: "vibe"
    args:
      - "-p"
      - "{query}"
      - "--agent"
      - "research"
    env:
      VIBE_HOME: "${HOME}/.vibe"
    stdin: devnull
intercept: []
""")
    config = load_config(cfg_file)
    w = config.workers["research"]
    assert w.command == "vibe"
    assert w.args == ["-p", "{query}", "--agent", "research"]
    assert w.env == {"VIBE_HOME": "${HOME}/.vibe"}
    assert w.stdin == "devnull"


def test_invalid_stdin_rejected(tmp_path):
    cfg_file = tmp_path / ".exactor.yml"
    cfg_file.write_text("""
workers:
  w:
    command: "echo"
    stdin: pipe
intercept: []
""")
    with pytest.raises(ValueError, match="stdin must be one of"):
        load_config(cfg_file)
