# Vibe recipe

Uses [Mistral Vibe](https://github.com/mistralai/mistral-vibe) as an always-on researcher.
Every `WebSearch` and `WebFetch` Claude Code tries to run gets intercepted and
routed to `vibe` instead.

## What you get

- Mandatory web research via Vibe — Claude can't skip it
- 24h result cache keyed by normalized query
- Timeouts, failure modes, cache-bust CLI — all from Exactor

## Prerequisites

1. `vibe` on `PATH` (follow Mistral Vibe install instructions)
2. A Mistral API key available in `$MISTRAL_API_KEY` or `~/.vibe/.env`

The agent and prompt are **bundled** in `vibe-home/` and used via `VIBE_HOME`
override — no manual copying into `~/.vibe/` required.

## Install

```bash
pipx install exactor
cp -r .exactor.yml vibe-home/ /path/to/your/repo/
exactor check              # validate the config
```

Keep `vibe-home/` next to `.exactor.yml`; the recipe points at it via
`${EXACTOR_CONFIG_DIR}/vibe-home`, which Exactor resolves to the directory
holding the loaded `.exactor.yml` at hook-invocation time.

Add the hook to your Claude Code settings:

```jsonc
// .claude/settings.json (or ~/.claude/settings.json)
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "WebSearch|WebFetch",
        "hooks": [
          { "type": "command", "command": "exactor hook pre", "timeout": 300 }
        ]
      }
    ]
  }
}
```

Restart Claude Code. Your next `WebSearch` runs through `vibe`.

## Customize

The `args` array maps directly to `vibe`'s CLI flags. Swap `--agent` to use a
different local vibe agent, or drop `--max-turns` to use vibe's default.

**Tune the researcher in-repo.** `vibe-home/prompts/research.md` is the live
system prompt; edit it and the next hook invocation picks up the change.
`vibe-home/agents/research.toml` selects the model and enabled tools.

**Env substitution.** Values in the `env:` block are expanded at hook time
against the host environment plus these Exactor-provided locals:

| Variable | Value |
|---|---|
| `EXACTOR_CONFIG_DIR` | directory containing the loaded `.exactor.yml` |
| `EXACTOR_CONFIG_FILE` | the `.exactor.yml` path itself |

Nothing sensitive lives in the YAML — `${MISTRAL_API_KEY}` and friends come
from the shell that invoked Claude Code.
