import subprocess
import sys


def test_exactor_hook_defaults_to_pre(tmp_path):
    # `exactor hook` (no subcommand) must not die at argparse — older docs
    # used this bare form, and a catch-all Claude matcher exposes argparse
    # failures as blocking hook errors on *every* tool call. The event
    # argument defaults to "pre" to keep those installs working.
    (tmp_path / ".exactor.yml").write_text("workers: {}\nintercept: []\n")
    result = subprocess.run(
        [sys.executable, "-m", "exactor.cli", "hook"],
        input='{"tool_name":"Bash","tool_input":{"command":"ls"}}',
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
