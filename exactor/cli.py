from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from . import __version__


_TEMPLATE = Path(__file__).parent.parent / "templates" / ".exactor.yml"


def cmd_init(args: argparse.Namespace) -> int:
    dest = Path.cwd() / ".exactor.yml"
    if dest.exists() and not args.force:
        print(f"[exactor] .exactor.yml already exists. Use --force to overwrite.")
        return 1
    shutil.copy(_TEMPLATE, dest)
    print(f"[exactor] created .exactor.yml")
    print(f"[exactor] add hooks to your .claude/settings.json:")
    print()
    print('  "hooks": {')
    print('    "PreToolUse": [{"command": "exactor hook pre"}],')
    print('    "PostToolUse": [{"command": "exactor hook post"}]')
    print('  }')
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from .config import find_config, load_config
    path = find_config()
    if not path:
        print("[exactor] no .exactor.yml found")
        return 1
    try:
        config = load_config(path)
        print(f"[exactor] config OK — {len(config.workers)} workers, {len(config.intercept)} rules")
        return 0
    except Exception as e:
        print(f"[exactor] config error: {e}")
        return 1


def cmd_hook(args: argparse.Namespace) -> int:
    from .hooks import pre_tool_use, post_tool_use
    if args.event == "pre":
        return pre_tool_use()
    if args.event == "post":
        return post_tool_use()
    print(f"[exactor] unknown hook event: {args.event}")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="exactor", description="Enforce the discipline your AGENTS.md only suggests.")
    parser.add_argument("--version", action="version", version=f"exactor {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create .exactor.yml from template")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")

    sub.add_parser("check", help="Validate .exactor.yml")

    p_hook = sub.add_parser("hook", help="Run as a Claude Code hook")
    p_hook.add_argument("event", choices=["pre", "post"])

    args = parser.parse_args()

    if args.command == "init":
        sys.exit(cmd_init(args))
    elif args.command == "check":
        sys.exit(cmd_check(args))
    elif args.command == "hook":
        sys.exit(cmd_hook(args))
    else:
        parser.print_help()
        sys.exit(0)
