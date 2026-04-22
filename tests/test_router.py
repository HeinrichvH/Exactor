import pytest
from exactor.config import Config, InterceptRule, Worker
from exactor.router import match_rule, run_worker


def _config(*rules: InterceptRule) -> Config:
    return Config(
        workers={"research": Worker("research {query}"), "explore": Worker("explore {query}")},
        intercept=list(rules),
    )


def test_matches_websearch():
    config = _config(InterceptRule(tool="WebSearch", query_field="query", route_to="research"))
    rule = match_rule("WebSearch", {"query": "what is exactor"}, config)
    assert rule is not None
    assert rule.route_to == "research"


def test_no_match_different_tool():
    config = _config(InterceptRule(tool="WebSearch", query_field="query", route_to="research"))
    rule = match_rule("Bash", {"command": "grep foo"}, config)
    assert rule is None


def test_matches_bash_grep():
    config = _config(InterceptRule(tool="Bash", query_field="command", match=r"^(grep|rg)\b", route_to="explore"))
    rule = match_rule("Bash", {"command": "grep -r foo src/"}, config)
    assert rule is not None


def test_bash_grep_unless_single_file():
    config = _config(InterceptRule(tool="Bash", query_field="command", match=r"^(grep|rg)\b", route_to="explore", unless="single_file_absolute_path"))
    # grep still matches even with unless=single_file (grep isn't cat)
    rule = match_rule("Bash", {"command": "grep foo /absolute/path/file.py"}, config)
    assert rule is not None


def test_bash_cat_single_file_bypassed():
    config = _config(InterceptRule(tool="Bash", query_field="command", match=r"^cat\b", route_to="explore", unless="single_file_absolute_path"))
    rule = match_rule("Bash", {"command": "cat /home/user/project/file.py"}, config)
    assert rule is None


def test_first_match_wins():
    config = _config(
        InterceptRule(tool="Bash", route_to="research"),
        InterceptRule(tool="Bash", route_to="explore"),
    )
    rule = match_rule("Bash", {"command": "anything"}, config)
    assert rule.route_to == "research"


def test_worker_timeout_returns_clean_message():
    config = Config(
        workers={"slow": Worker(command="sleep 5", timeout=1)},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="slow")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "anything"}, config)
    assert not result.success
    assert "timed out" in result.output


def test_worker_success_returns_stdout():
    config = Config(
        workers={"echo": Worker(command="echo hello-{query}")},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="echo")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "world"}, config)
    assert result.success
    assert result.output == "hello-world"


def test_worker_nonzero_exit_marks_failure():
    config = Config(
        workers={"broken": Worker(command="exit 1")},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="broken")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "q"}, config)
    assert not result.success
    assert "exit 1" in result.output


def test_worker_args_form_shell_false():
    # args form should NOT go through a shell — shell metacharacters in
    # the query must appear verbatim as the argument, not interpreted.
    config = Config(
        workers={"echo": Worker(command="echo", args=["{query}"])},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="echo")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "foo; rm -rf ~"}, config)
    assert result.success
    assert result.output == "foo; rm -rf ~"  # literal, shell did not parse


def test_worker_args_with_flags():
    config = Config(
        workers={"w": Worker(command="sh", args=["-c", "echo flag1=$1 flag2=$2", "--", "{query}", "B"])},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="w")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "A"}, config)
    assert result.success
    assert result.output == "flag1=A flag2=B"


def test_worker_env_var_expansion(monkeypatch):
    monkeypatch.setenv("MY_SECRET", "opensesame")
    config = Config(
        workers={"w": Worker(
            command="sh",
            args=["-c", "echo $MY_VAR"],
            env={"MY_VAR": "${MY_SECRET}-suffix"},
        )},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="w")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "x"}, config)
    assert result.success
    assert result.output == "opensesame-suffix"


def test_worker_env_exposes_exactor_config_dir(tmp_path):
    cfg_path = tmp_path / ".exactor.yml"
    cfg_path.write_text("")  # existence only; we build Config manually below
    config = Config(
        workers={"w": Worker(
            command="sh",
            args=["-c", "echo $RECIPE_ROOT"],
            env={"RECIPE_ROOT": "${EXACTOR_CONFIG_DIR}/vibe-home"},
        )},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="w")],
        source=cfg_path,
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "x"}, config)
    assert result.success
    assert result.output == f"{tmp_path}/vibe-home"


def test_worker_command_not_found_is_clean_failure():
    config = Config(
        workers={"w": Worker(command="this-does-not-exist", args=["{query}"])},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="w")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "x"}, config)
    assert not result.success
    assert "not found" in result.output


def test_router_is_tool_agnostic():
    # No hardcoded knowledge of any tool: a rule targeting a fictional MCP
    # tool extracts its custom field exactly like WebSearch extracts `.query`.
    config = Config(
        workers={"echo": Worker(command="echo", args=["{query}"])},
        intercept=[InterceptRule(tool="mcp__custom__ask", query_field="question", route_to="echo")],
    )
    rule = match_rule("mcp__custom__ask", {"question": "why the sky is blue", "extra": "ignored"}, config)
    assert rule is not None
    result = run_worker(rule, {"question": "why the sky is blue", "extra": "ignored"}, config)
    assert result.success
    assert result.output == "why the sky is blue"


def test_no_query_field_passes_whole_payload():
    # When query_field is omitted the worker gets the whole str(tool_input).
    config = Config(
        workers={"echo": Worker(command="echo", args=["{query}"])},
        intercept=[InterceptRule(tool="AnyTool", route_to="echo")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"a": 1, "b": 2}, config)
    assert result.success
    assert "'a': 1" in result.output and "'b': 2" in result.output


def test_worker_cwd():
    config = Config(
        workers={"w": Worker(command="pwd", cwd="/tmp")},
        intercept=[InterceptRule(tool="WebSearch", query_field="query", route_to="w")],
    )
    rule = config.intercept[0]
    result = run_worker(rule, {"query": "x"}, config)
    assert result.success
    assert result.output == "/tmp"
