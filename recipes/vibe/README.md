# Vibe recipe

Uses [Mistral Vibe](https://github.com/mistralai/vibe) as an always-on researcher.
Every `WebSearch` and `WebFetch` Claude Code tries to run gets intercepted and
routed to `vibe` instead.

## What you get

- Mandatory web research via Vibe — Claude can't skip it
- 24h result cache keyed by normalized query
- Timeouts, failure modes, cache-bust CLI — all from Exactor

## Prerequisites

1. `vibe` on `PATH` (follow Mistral Vibe install instructions)
2. A Mistral API key available in `$MISTRAL_API_KEY` or `~/.vibe/.env`
3. A `research` agent provisioned in `~/.vibe/`:

   ```bash
   mkdir -p ~/.vibe/agents ~/.vibe/prompts
   # agent.toml and prompt.md — bring your own, or copy these:
   cp agent.toml  ~/.vibe/agents/research.toml
   cp prompt.md   ~/.vibe/prompts/research.md
   ```

## Install

```bash
pipx install exactor
cp .exactor.yml /path/to/your/repo/
exactor check              # validate the config
```

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

The `env` block is how secrets flow. `${VAR}` values are expanded from your
host environment at hook-invocation time — nothing sensitive lives in the YAML.
