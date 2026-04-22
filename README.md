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

## Status

Early development. Core hook dispatcher in progress.

## Installation

```bash
pip install exactor
exactor init
```

*Coming soon.*

## License

MIT
