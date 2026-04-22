# exactor

*Disciplinam quam AGENTS.md tantum suggerit, impone.*  
Enforce the discipline your AGENTS.md only suggests.

Exactor is a Claude Code hooks layer that makes subagent delegation mandatory, not optional. Configure once in `.exactor.yml` — your researcher, explorer, and log digger fire every time, not just when the model decides to.

## The problem

You write in your AGENTS.md: *"use the researcher tool for web questions."*  
The model reads it. Sometimes follows it. Often doesn't.

Exactor turns that suggestion into a constraint. A `WebSearch` call triggers your researcher. A `grep` triggers your explorer. The raw tool never fires. Not sometimes — always.

## How it works

Claude Code fires hooks before and after every tool call. Exactor sits in those hooks, matches tool calls against rules you define in `.exactor.yml`, and routes them to the configured worker instead. Workers are plain shell commands — bring your own.

```yaml
# .exactor.yml
workers:
  research:
    command: "research {query}"
  explore:
    command: "explore {query}"

intercept:
  - tool: WebSearch
    route_to: research
  - tool: Bash
    match: "^(grep|rg|find)\\b"
    route_to: explore
```

## Recipes

Ready-made configs for common researcher and explorer tools live in [`recipes/`](recipes/).
Copy the one matching your setup and drop it in your repo root.

- [`recipes/vibe`](recipes/vibe) — [Mistral Vibe](https://github.com/mistralai/mistral-vibe)
  as a mandatory researcher
- more coming (Claude CLI, Perplexity, Ollama — contributions welcome)

## Worker contract

A worker is any CLI that:

- accepts the intercepted query via `{query}` substitution
- reads nothing from stdin (by default; configurable via `stdin:`)
- writes the result to stdout
- writes errors to stderr
- exits 0 on success, non-zero on failure

Pass flags via `args:`, secrets via `env:`. See [`recipes/vibe/.exactor.yml`](recipes/vibe/.exactor.yml)
for a complete example.

## Logging

Every hook fire, routing decision, and worker outcome is written as one
JSON object per line to `$XDG_STATE_HOME/exactor/exactor.log` (default
`~/.local/state/exactor/exactor.log`). Rotated at 5 MiB × 3 files.

```bash
exactor log path      # print the resolved log file path
exactor log tail      # tail -f
jq 'select(.worker=="explore")' ~/.local/state/exactor/exactor.log
```

Set `EXACTOR_LOG_LEVEL=debug` to also mirror to stderr (visible under
`claude --debug`). Override the path per-project via `logging.path` in
`.exactor.yml`, or globally via `EXACTOR_LOG_FILE`.

## Status

Early development. Core hook dispatcher and SQLite working-memory cache
are in place. Structured worker invocation (`args` / `env` / `stdin` / `cwd`)
landed.

## Installation

```bash
pipx install exactor
exactor init
```

*Published to PyPI soon.*

## License

MIT
