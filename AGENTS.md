# Exactor — Agent Instructions

## What this repo is

A Claude Code hooks layer. Python. One config file. No magic.

## Working here

- Entry point: `exactor/cli.py`
- Hook dispatcher: `exactor/hooks.py`
- Config schema: `exactor/config.py`
- Default config template: `exactor/templates/.exactor.yml`

## Rules

**Codebase questions** — read the relevant file directly. The codebase is small enough that exploration tools are overkill.

**Before adding a new intercept pattern** — check `exactor/config.py` for the schema and `tests/` for an existing fixture to extend.

**Keep workers as shell commands** — no worker should require Python imports. If you're tempted to write Python logic into a worker, it belongs in the hook dispatcher instead.

**Do not add runtime dependencies without discussion** — `pyyaml` is the only allowed third-party dep for now.
