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
