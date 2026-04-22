import pytest
from exactor.config import Config, InterceptRule, MemoryConfig, Worker
from exactor.router import match_rule


def _config(*rules: InterceptRule) -> Config:
    return Config(
        workers={"research": Worker("research {query}"), "explore": Worker("explore {query}")},
        intercept=list(rules),
        memory=MemoryConfig(),
    )


def test_matches_websearch():
    config = _config(InterceptRule(tool="WebSearch", route_to="research"))
    rule = match_rule("WebSearch", {"query": "what is exactor"}, config)
    assert rule is not None
    assert rule.route_to == "research"


def test_no_match_different_tool():
    config = _config(InterceptRule(tool="WebSearch", route_to="research"))
    rule = match_rule("Bash", {"command": "grep foo"}, config)
    assert rule is None


def test_matches_bash_grep():
    config = _config(InterceptRule(tool="Bash", match=r"^(grep|rg)\b", route_to="explore"))
    rule = match_rule("Bash", {"command": "grep -r foo src/"}, config)
    assert rule is not None


def test_bash_grep_unless_single_file():
    config = _config(InterceptRule(tool="Bash", match=r"^(grep|rg)\b", route_to="explore", unless="single_file_absolute_path"))
    # grep still matches even with unless=single_file (grep isn't cat)
    rule = match_rule("Bash", {"command": "grep foo /absolute/path/file.py"}, config)
    assert rule is not None


def test_bash_cat_single_file_bypassed():
    config = _config(InterceptRule(tool="Bash", match=r"^cat\b", route_to="explore", unless="single_file_absolute_path"))
    rule = match_rule("Bash", {"command": "cat /home/user/project/file.py"}, config)
    assert rule is None


def test_first_match_wins():
    config = _config(
        InterceptRule(tool="Bash", route_to="research"),
        InterceptRule(tool="Bash", route_to="explore"),
    )
    rule = match_rule("Bash", {"command": "anything"}, config)
    assert rule.route_to == "research"
