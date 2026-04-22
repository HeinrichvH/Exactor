# recipes/explore — code-search delegation

Route Claude Code's `Grep` and `Glob` tools to a code-search subagent that
returns compact, cited reports instead of raw regex output.

## Why

A single `Grep` call is cheap. A `Grep` → `Read` → `Grep` → `Read` *loop*
is where context burns. A subagent does the loop in one subprocess and
hands back a summarized report — "here's what `PricingConfig` looks like
and how it flows through the codebase" instead of a list of 47 file paths
plus their contents.

Exactor's job: intercept the tool calls, fold multi-field inputs (`pattern`
+ `path` + `glob`) into one natural-language query via `query_template`,
route to your chosen subagent, and present the report to Claude as the
tool's result.

## Prerequisites

- An `explore` CLI (or whatever you call it) that:
  - Takes one positional arg: a natural-language exploration question
  - Prints a compact, cited report to stdout
  - Exits 0 on success

You bring the subagent. Common choices:

- [Goose](https://github.com/block/goose) with a read-only shell recipe
- [Aider](https://github.com/Aider-AI/aider) in `--architect` read-only mode
- A `claude` API subprocess with a code-search system prompt
- A hand-rolled shell script wrapping `rg --json`

## Install

### Option A: global config

Point Exactor at this recipe from `~/.exactor.yml`:

```yaml
version: 1
workers:
  explore:
    command: "explore"
    args: ["{query}"]
    cache: true
intercept:
  - tool: Grep
    query_template: "search for '{pattern}' in path '{path}'"
    route_to: explore
  - tool: Glob
    query_template: "list files matching '{pattern}' under '{path}'"
    route_to: explore
```

Then in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": ".*",
        "hooks": [{"type": "command", "command": "exactor hook", "timeout": 600}]
      }
    ]
  }
}
```

The catch-all matcher is intentional — Exactor's config is the source of
truth for which tools get intercepted. Adding a new intercepted tool
becomes a pure YAML edit, no Claude restart.

### Option B: per-project config

Copy this directory into your project and Exactor will find the
`.exactor.yml` by walking up from `cwd`.

## Tuning

- **`unless_match`**: skip exploration for obvious narrow cases. For Grep,
  a regex that matches 1–3 character patterns keeps direct lookups fast.
- **`timeout`**: bump to 300–600s if your subagent does multi-step LLM
  exploration. Remember to set Claude Code's hook timeout at least as high.
- **`cache: true`**: repeated identical queries reuse the report. TTL is
  configurable via `default_ttl_hours` or per-worker `cache_ttl_hours`.

## Read interception

Intercepting `Read` is possible but aggressive — every file read gets
funneled through the subagent. Most users should leave Read alone and rely
on Grep/Glob interception to short-circuit the exploration loops that
actually burn context. The skeleton `.exactor.yml` in this recipe includes
a commented-out Read intercept for reference.
