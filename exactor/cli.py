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


def _open_cache() -> "object | None":
    from .cache import Cache
    from .config import find_config, load_config
    path = find_config()
    if not path:
        print("[exactor] no .exactor.yml found", file=sys.stderr)
        return None
    config = load_config(path)
    cache_path = (path.parent / config.cache.path) if not Path(config.cache.path).is_absolute() else Path(config.cache.path)
    return Cache(cache_path)


def cmd_cache(args: argparse.Namespace) -> int:
    cache = _open_cache()
    if cache is None:
        return 1

    import datetime as _dt

    if args.cache_action == "list":
        entries = cache.list_entries()
        if not entries:
            print("[exactor] cache is empty")
            return 0
        now = int(__import__("time").time())
        for key, size, expires_at in entries:
            ttl = expires_at - now
            status = f"expires in {ttl}s" if ttl > 0 else f"EXPIRED {-ttl}s ago"
            print(f"  {key}   [{size} bytes, {status}]")
        return 0

    if args.cache_action == "clear":
        if args.all:
            n = cache.clear_all()
            print(f"[exactor] cleared {n} entries")
        elif args.worker:
            n = cache.clear_by_worker(args.worker)
            print(f"[exactor] cleared {n} entries for worker '{args.worker}'")
        elif args.query:
            n = cache.clear_by_query_substring(args.query)
            print(f"[exactor] cleared {n} entries matching query '{args.query}'")
        elif args.expired:
            n = cache.purge_expired()
            print(f"[exactor] purged {n} expired entries")
        else:
            print("[exactor] specify one of: --all | --worker NAME | --query STRING | --expired", file=sys.stderr)
            return 1
        return 0

    print(f"[exactor] unknown cache action: {args.cache_action}", file=sys.stderr)
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="exactor", description="Enforce the discipline your AGENTS.md only suggests.")
    parser.add_argument("--version", action="version", version=f"exactor {__version__}")
    sub = parser.add_subparsers(dest="command")

    p_init = sub.add_parser("init", help="Create .exactor.yml from template")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing config")

    sub.add_parser("check", help="Validate .exactor.yml")

    p_hook = sub.add_parser("hook", help="Run as a Claude Code hook")
    # `event` defaults to "pre" because that's ~95% of hook installs and
    # older docs recommended the bare `exactor hook` form. Keeping the
    # default avoids silent argparse failure under a catch-all matcher.
    p_hook.add_argument("event", choices=["pre", "post"], nargs="?", default="pre")

    p_cache = sub.add_parser("cache", help="Inspect or clear the working-memory cache")
    cache_sub = p_cache.add_subparsers(dest="cache_action")
    cache_sub.add_parser("list", help="Show cache entries")
    p_clear = cache_sub.add_parser("clear", help="Remove cache entries")
    group = p_clear.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="Clear all entries")
    group.add_argument("--worker", help="Clear entries for one worker")
    group.add_argument("--query", help="Clear entries whose normalized query matches substring")
    group.add_argument("--expired", action="store_true", help="Remove only expired entries")

    args = parser.parse_args()

    if args.command == "init":
        sys.exit(cmd_init(args))
    elif args.command == "check":
        sys.exit(cmd_check(args))
    elif args.command == "hook":
        sys.exit(cmd_hook(args))
    elif args.command == "cache":
        sys.exit(cmd_cache(args))
    else:
        parser.print_help()
        sys.exit(0)
