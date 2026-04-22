# recipes/explore — code-search delegation

Route Claude Code's `Grep` and `Glob` tool calls to a code-exploration
subagent that returns compact, cited reports instead of raw regex output.
Ships with a reference implementation using [Goose](https://github.com/block/goose)
+ Mistral Devstral; swappable if you'd rather run a different backend.

## Why

A single `Grep` call is cheap. A `Grep` → `Read` → `Grep` → `Read` *loop*
is where context burns. A subagent does the loop in one subprocess and
hands back a summarized report — "here's what `PricingConfig` looks like
and how it flows through the codebase" instead of 47 file paths plus
their contents.

Exactor's job: intercept the tool calls, fold multi-field inputs
(`pattern` + `path` + `glob`) into one natural-language query via
`query_template`, route to the subagent, and present the report to
Claude as the tool's result.

## Layout

```
recipes/explore/
├── .exactor.yml                    # worker definition + Grep/Glob intercepts
├── explore.py                      # orchestration: goose + validation + verifier
└── goose-home/
    ├── explore/recipe.yaml         # Devstral code explorer
    └── verifier/recipe.yaml        # adversarial scope-drift checker
```

One Python file. Stdlib only (argparse, json, re, shlex, subprocess).
Fork it if you want to customize — it's a reference implementation, not a
library. The two Goose recipes are where the subagent's tools, prompts,
and response schema live.

## What it does (in order)

1. **Qualifier shim** — scan the question for time windows, environments,
   failure classes, and state modifiers, append them as a mechanical
   checklist. Shifts scope-drift detection left from verifier (catches
   drift after) to prompt (prevents it).
2. **Main explore pass** — `goose run --recipe goose-home/explore/recipe.yaml`
   with Devstral + read-only shell + `text_editor` (view). The recipe
   enforces a strict JSON response schema with a citation regex —
   generated code, build output, and vendored paths are rejected at the
   Goose boundary.
3. **Citation validator** — re-open each cited file, confirm the `symbol`
   appears within ±3 lines of the citation, strip fabricated findings,
   and re-execute any `proof_queries` (whitelisted to
   `rg | fd | grep | ls | find`) to verify negative claims like "no
   callers outside X."
4. **Validator retry** — if rc=3 (proof_queries missing/failed), re-invoke
   with a sharper prompt nudge.
5. **Adversarial verifier** — `goose run --recipe goose-home/verifier/recipe.yaml`
   reads the question + response and decides whether the response
   actually answers the question. On `"verdict": "miss"`, the main
   recipe is re-run with the verifier's gap feedback.

## Requirements

- [Goose](https://github.com/block/goose) on `PATH`
- `MISTRAL_API_KEY` in the environment (shared with the `vibe` recipe —
  Goose reads it the same way vibe does)
- `ripgrep` installed as a **real binary** (`which rg` should resolve).
  Claude Code itself defines `rg` as a *bash function* that proxies to
  its bundled ripgrep — that function only exists in Claude Code's
  interactive shell and is not inherited by subprocesses, so goose's
  shell tool will get "command not found" unless you have a standalone
  install (`apt install ripgrep` / `brew install ripgrep` / `cargo install ripgrep`).
- Python 3 (stdlib only)

## Install

### Global config (recommended)

Add to `~/.exactor.yml`:

```yaml
version: 1

workers:
  explore:
    command: python3
    args:
      - "${HOME}/path/to/Exactor/recipes/explore/explore.py"
      - "{query}"
    stdin: devnull
    timeout: 600
    cache: false
    mode: loose

intercept:
  - tool: Grep
    query_template: "find '{pattern}' in path '{path}' (glob='{glob}', type='{type}')"
    route_to: explore
    unless_match: "^find '.{1,3}' in"
  - tool: Glob
    query_template: "list files matching '{pattern}' under '{path}'"
    route_to: explore
  - tool: Bash
    query_field: command
    match: "^(?:[A-Za-z_][A-Za-z0-9_]*=\\S+\\s+)*(grep|egrep|fgrep|rg|find|fd|ag|ack)\\b"
    unless_match: "^find\\s+\\S+\\s*$"
    route_to: explore
```

The Bash intercept fires only when the command **starts** with a search
tool (optionally preceded by `VAR=val` env assignments). That
deliberately excludes `kubectl ... | grep foo` / `fj ... | grep bar` —
the explorer can't reproduce the upstream command's output and would
hallucinate. `ls` is omitted on purpose: too often a one-shot sanity
check where routing burns latency for no context win.

And in `~/.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "exactor hook pre", "timeout": 600}]
      }
    ]
  }
}
```

The catch-all matcher is intentional — Exactor's config is the source of
truth for which tools get intercepted. Adding a new intercepted tool
becomes a pure YAML edit, no Claude restart.

### Per-project config

Copy or symlink the whole `recipes/explore/` directory into your project.
Exactor will find the `.exactor.yml` by walking up from `cwd`.

## Tuning

- **Model**: change `goose_model:` in `goose-home/explore/recipe.yaml`
  (defaults to `devstral-2512`). Any Goose-supported model works; Devstral
  is coding-specialist and worth keeping unless you're cost-constrained.
- **Turns**: add `--turns N` to the worker's `args` (default 120). Larger
  values let the subagent chase cross-module flows; smaller values cap
  cost on simple lookups.
- **Verifier bypass**: add `--no-verify` to the worker's `args` to skip
  the adversarial pass (faster, less reliable).
- **Retry bypass**: add `--no-retry` to skip the validator-rc=3 retry.
- **`unless_match`**: skip the subagent for narrow cases. The default
  regex `^find '.{1,3}' in` bypasses 1–3 character patterns (sigils,
  typos). Tune for your codebase — overly broad patterns will route
  simple lookups through a 20–60s subprocess.
- **`cache`**: stays `false` by default. Code changes under us; a cache
  hit on a stale exploration is worse than re-running.

## Cost profile

- Single exploration: 1–3 Mistral Devstral calls via Goose + 1 Mistral
  Medium verifier pass. Typical total: $0.01–0.05 per question, 20–60s
  wall time.
- Worth it vs. 10+ rounds of Claude Grep/Read burning 10K+ context tokens.
- Cache off means every unique query pays; use `unless_match` to steer
  cheap questions away from the subagent.

## Swapping the backend

The Exactor side of the contract is just "a command that takes a
natural-language query and prints a report." If you'd rather not run
Goose + Devstral, point the worker at any subagent that satisfies that
shape:

- [Aider](https://github.com/Aider-AI/aider) in `--architect` read-only mode
- A `claude` API subprocess with a code-search system prompt
- A hand-rolled shell script wrapping `rg --json`

```yaml
workers:
  explore:
    command: "my-explorer"
    args: ["{query}"]
```

You lose the citation validator and adversarial verifier that live in
`explore.py` — those are Goose-specific. If you want that quality bar
with a different backend, fork `explore.py` and swap out the
`goose run` calls.
