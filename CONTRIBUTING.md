# Contributing

Contributions welcome. Keep it focused — Exactor does one thing.

## Setup

```bash
git clone https://github.com/your-username/exactor
cd exactor
pip install -e ".[dev]"
```

## Before opening a PR

- Run `pytest` — all tests must pass
- Keep the diff small and the purpose clear
- One concern per PR

## What belongs here

- New intercept patterns and routing logic
- Additional memory backends (file, SQLite, custom)
- Better `unless`-clause heuristics
- Documentation improvements

## What doesn't belong here

- New runtime dependencies (discuss first)
- Worker implementations — workers are user-defined shell commands, not part of this repo
- Scope creep toward an MCP server — Exactor is a hooks layer, not a platform

## Questions

Open an issue.
