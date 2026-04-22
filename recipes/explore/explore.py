#!/usr/bin/env python3
"""
explore — code-exploration subagent for the explore recipe.

Invoked by Exactor with a single positional argument: the question. Runs a
Goose (Mistral Devstral) read-only code explorer, validates citations
against the source tree, re-runs proof_queries for negative claims, and
optionally asks an adversarial verifier whether the response actually
answers the question. Retries once on validator failure, once on verifier
miss, then gives up.

Writes the validated JSON response to stdout. Logs progress + failures to
stderr.

Expects cwd to be the repo to explore — Exactor's worker config leaves
cwd unset, so it inherits Claude Code's cwd.

Requirements:
  - goose on PATH
  - MISTRAL_API_KEY in env (shared with ~/.vibe/.env)
  - ripgrep (rg) available in the repo being explored
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
EXPLORE_RECIPE = SCRIPT_DIR / "goose-home" / "explore" / "recipe.yaml"
VERIFIER_RECIPE = SCRIPT_DIR / "goose-home" / "verifier" / "recipe.yaml"

CITATION_WINDOW = 3
PROOF_CMD_WHITELIST = {"rg", "fd", "grep", "ls", "find"}

# --- qualifier extraction -------------------------------------------------
# Shifts scope-drift detection left: mechanically surface time windows,
# environments, failure classes, and state modifiers so the subagent sees
# them as an explicit checklist before its first tool call.

QUALIFIER_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(yesterday)\b", re.IGNORECASE),
     "Time window: **{m}** — must look at yesterday's data specifically, not a rolling last-hour default."),
    (re.compile(r"\b(today)\b", re.IGNORECASE),
     "Time window: **{m}** — scope to today's data."),
    (re.compile(r"\b(?:in\s+the\s+|over\s+the\s+|during\s+the\s+|for\s+the\s+)?(?:last|past)\s+(\d+\s*(?:minutes?|hours?|days?|weeks?|months?|m|h|d|w))\b", re.IGNORECASE),
     "Time window: **{g1}** — query must cover this range, not a default."),
    (re.compile(r"\bsince\s+(\d{4}-\d{2}-\d{2}|\w+day|yesterday|last\s+\w+)\b", re.IGNORECASE),
     "Time window: **since {m}** — query must start at this point."),
    (re.compile(r"\b(this\s+(?:morning|afternoon|evening|week|month))\b", re.IGNORECASE),
     "Time window: **{m}** — scope accordingly."),
    (re.compile(r"\b(in\s+prod(?:uction)?|on\s+prod(?:uction)?|prod(?:uction)?\s+(?:cluster|env|data))\b", re.IGNORECASE),
     "Environment: **{m}** — must query the prod cluster, not quality/staging."),
    (re.compile(r"\b(in\s+quality|on\s+quality|quality\s+(?:cluster|env|data))\b", re.IGNORECASE),
     "Environment: **{m}** — must query the quality cluster specifically."),
    (re.compile(r"\b(in\s+staging|on\s+staging)\b", re.IGNORECASE),
     "Environment: **{m}** — scope to staging."),
    (re.compile(r"\b(live\s+users?|live\s+traffic|real\s+users?|real\s+customers?)\b", re.IGNORECASE),
     "Population: **{m}** — implies production data, not test/synthetic."),
    (re.compile(r"\b(timeouts?|timed?\s+out|deadline\s+exceeded)\b", re.IGNORECASE),
     "Failure class: **{m}** — only timeout-class findings count. Config errors, 404s, auth failures are OFF-topic."),
    (re.compile(r"\b(unauthori[sz]ed|forbidden|401|403|auth\s+failures?|auth\s+errors?)\b", re.IGNORECASE),
     "Failure class: **{m}** — only auth-class findings count."),
    (re.compile(r"\b(crashe?s?|panics?|fatal|oom|out\s+of\s+memory)\b", re.IGNORECASE),
     "Failure class: **{m}** — only crash-class findings count."),
    (re.compile(r"\b(5\d\d\s+errors?|internal\s+server\s+error)\b", re.IGNORECASE),
     "Failure class: **{m}** — only 5xx-class findings count."),
    (re.compile(r"\bun(verified|confirmed|activated|approved|paid|read)\s+(\w+)", re.IGNORECASE),
     "Modifier: **un{g1} {g2}** — must filter by the actual 'un{g1}' state, not 'any {g2}' or 'empty {g2}'."),
    (re.compile(r"\b(failed|pending|expired|deleted|disabled|inactive|orphaned|stale)\s+(\w+)", re.IGNORECASE),
     "Modifier: **{g1} {g2}** — must filter by '{g1}' state specifically."),
    (re.compile(r"\b(empty|null|missing)\s+(\w+)", re.IGNORECASE),
     "Modifier: **{g1} {g2}** — must check for literal empty/null, not 'any'."),
]


def extract_qualifiers(question: str) -> str:
    """Return a bulleted checklist of qualifiers found in the question, or ''."""
    hits: list[str] = []
    seen: set[str] = set()
    for pat, template in QUALIFIER_PATTERNS:
        for match in pat.finditer(question):
            kwargs = {"m": match.group(0)}
            for idx, val in enumerate(match.groups(), start=1):
                kwargs[f"g{idx}"] = val or ""
            entry = template.format(**kwargs)
            if entry not in seen:
                seen.add(entry)
                hits.append(entry)
    if not hits:
        return ""
    return "## Question qualifiers (mechanically extracted — address EACH)\n" + "\n".join(f"- {h}" for h in hits)


# --- negative-claim detection --------------------------------------------
# Claims like "no callers of X" require proof_queries backing. Validator
# rejects the response if a negative claim appears without any proofs.

NEGATIVE_RE = re.compile(
    r"\bno\s+(other\s+)?(caller|reference|usage|match|occurrence|instance|use|implementation|hit|result)s?\b"
    r"|\bnone\s+of\b"
    r"|\bnot\s+(\w+ed|used|call|reference|implement|exist|import|invoke|present|found)\w*\b"
    r"|\bonly\s+(in|used|called|referenced|found|one|two|three|within)\b"
    r"|\boutside\s+(of\s+)?(the|this|that)\s+\w+"
    r"|\bnothing\s+(matches|else|found|here)\b"
    r"|\bnever\s+(called|used|referenced|invoked|imported)\b"
    r"|\babsent\s+from\b"
    r"|\bmissing\s+from\b",
    re.IGNORECASE,
)


# --- citation validation --------------------------------------------------

def parse_citation(citation: str) -> tuple[str, int, int] | None:
    m = re.match(r"^\.?/?(?P<path>.+?):(?P<start>\d+)(?:-(?P<end>\d+))?$", citation)
    if not m:
        return None
    start = int(m["start"])
    end = int(m["end"]) if m["end"] else start
    return m["path"], start, end


_TRACKED_CACHE: dict[str, list[str]] = {}


def _tracked_files(repo_root: Path) -> list[str]:
    key = str(repo_root)
    cached = _TRACKED_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            ["git", "ls-files"], cwd=repo_root, capture_output=True, text=True, timeout=10
        )
        files = result.stdout.splitlines() if result.returncode == 0 else []
    except (OSError, subprocess.TimeoutExpired):
        files = []
    _TRACKED_CACHE[key] = files
    return files


def resolve_cited_path(repo_root: Path, rel_path: str) -> Path | None:
    """Return an existing absolute path for rel_path, or None if unresolvable.

    Tries in order:
      1. repo_root / rel_path — the literal citation
      2. suffix match against `git ls-files` — handles the common case
         where the subagent drops a monorepo prefix (e.g. cites
         `Core/Foo/Bar.cs` instead of `src/Core/Foo/Bar.cs`).

    Suffix match only resolves when exactly one tracked file ends with
    rel_path — ambiguous matches (e.g. multiple `README.md`) are rejected
    rather than silently picking one.
    """
    direct = repo_root / rel_path
    if direct.is_file():
        return direct
    suffix = "/" + rel_path.lstrip("/")
    matches = [f for f in _tracked_files(repo_root) if f.endswith(suffix) or f == rel_path]
    if len(matches) == 1:
        candidate = repo_root / matches[0]
        if candidate.is_file():
            return candidate
    return None


def verify_citation(repo_root: Path, citation: str, symbol: str) -> tuple[bool, str, str]:
    """Return (ok, reason, resolved_rel_path).

    resolved_rel_path is the repo-relative path actually opened — may
    differ from the path embedded in `citation` if suffix-matching kicked
    in. Caller can attach it to the finding so the consumer (Claude)
    doesn't have to re-guess the prefix before opening the file.
    """
    parsed = parse_citation(citation)
    if not parsed:
        return False, f"malformed citation: {citation!r}", ""
    rel_path, start, end = parsed
    abs_path = resolve_cited_path(repo_root, rel_path)
    if abs_path is None:
        return False, f"file not found: {rel_path}", ""
    try:
        lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return False, f"read error: {exc}", ""
    resolved_rel = str(abs_path.relative_to(repo_root))
    lo = max(0, start - 1 - CITATION_WINDOW)
    hi = min(len(lines), end + CITATION_WINDOW)
    window = "\n".join(lines[lo:hi])
    if symbol in window:
        return True, "ok", resolved_rel
    return False, f"symbol {symbol!r} not found within ±{CITATION_WINDOW} of {start}..{end}", resolved_rel


def run_proof(repo_root: Path, command: str) -> tuple[bool, int, str]:
    """Execute a whitelisted search command; return (ok, line_count, reason)."""
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return False, 0, f"unparseable command: {exc}"
    if not argv:
        return False, 0, "empty command"
    if argv[0] not in PROOF_CMD_WHITELIST:
        return False, 0, f"command {argv[0]!r} not in whitelist {sorted(PROOF_CMD_WHITELIST)}"
    if any(tok in {"|", ";", "&&", "||", ">", ">>", "<", "`", "$("} for tok in argv):
        return False, 0, "shell metacharacters forbidden"
    try:
        result = subprocess.run(argv, cwd=repo_root, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        return False, 0, "command timed out after 30s"
    except OSError as exc:
        return False, 0, f"exec failed: {exc}"
    stdout = result.stdout or ""
    if stdout == "":
        count = 0
    else:
        count = stdout.count("\n") + (0 if stdout.endswith("\n") else 1)
    return True, count, "ok"


def validate_response(repo_root: Path, data: dict) -> tuple[dict, int]:
    """Strip fabricated findings, re-run proof_queries. Return (data, rc).

    rc:
      0 — at least one citation kept, all proofs passed
      2 — all findings AND references stripped; response unreliable
      3 — proof_queries missing/failed, or negative claim without any proofs
    """
    def _attach_resolved(item: dict, citation: str, resolved: str) -> dict:
        # Only surface resolved_path when it differs from what the subagent
        # cited — otherwise it's just noise. Compare by the path portion
        # (drop the :line suffix) so resolved vs cited are apples-to-apples.
        cited_path = citation.split(":", 1)[0].lstrip("./")
        if resolved and resolved != cited_path:
            return {**item, "resolved_path": resolved}
        return item

    stripped_findings: list[dict] = []
    kept_findings: list[dict] = []
    for f in data.get("findings", []):
        citation = f.get("citation", "")
        symbol = f.get("symbol", "")
        if not citation or not symbol:
            stripped_findings.append({"item": f, "reason": "missing citation/symbol"})
            continue
        ok, reason, resolved = verify_citation(repo_root, citation, symbol)
        if ok:
            kept_findings.append(_attach_resolved(f, citation, resolved))
        else:
            stripped_findings.append({"item": f, "reason": reason})

    stripped_refs: list[dict] = []
    kept_refs: list[dict] = []
    for r in data.get("references", []):
        citation = r.get("citation", "")
        symbol = r.get("symbol", "")
        if not citation or not symbol:
            stripped_refs.append({"item": r, "reason": "missing citation/symbol"})
            continue
        ok, reason, resolved = verify_citation(repo_root, citation, symbol)
        if ok:
            kept_refs.append(_attach_resolved(r, citation, resolved))
        else:
            stripped_refs.append({"item": r, "reason": reason})

    proof_results: list[dict] = []
    proofs_ok = True
    for p in data.get("proof_queries", []):
        cmd = p.get("command", "")
        claimed = p.get("claimed_count")
        supports = p.get("supports", "")
        ok, actual, reason = run_proof(repo_root, cmd)
        status = "pass" if (ok and claimed == actual) else "fail"
        proof_results.append({
            "command": cmd,
            "claimed_count": claimed,
            "actual_count": actual if ok else None,
            "supports": supports,
            "status": status,
            "reason": reason if not ok else ("count matches" if claimed == actual else f"claimed {claimed}, actual {actual}"),
        })
        if status != "pass":
            proofs_ok = False

    summary = data.get("summary", "")
    claims = " ".join(f.get("claim", "") for f in kept_findings)
    has_negative = bool(NEGATIVE_RE.search(summary) or NEGATIVE_RE.search(claims))
    missing_proof = has_negative and not data.get("proof_queries")

    data["findings"] = kept_findings
    data["references"] = kept_refs
    data["_validation"] = {
        "stripped_findings": stripped_findings,
        "stripped_references": stripped_refs,
        "kept_findings": len(kept_findings),
        "kept_references": len(kept_refs),
        "proof_queries": proof_results,
        "proofs_passed": proofs_ok,
        "negative_claim_without_proof": missing_proof,
    }

    if not kept_findings and not kept_refs:
        return data, 2
    if not proofs_ok or missing_proof:
        return data, 3
    return data, 0


# --- goose invocation -----------------------------------------------------

def _last_json_line(text: str) -> dict | None:
    """Goose emits a verbose trace and a final single-line JSON object; grab it."""
    last: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            last = parsed
    return last


def run_goose(recipe: Path, params: dict[str, str], max_turns: int) -> dict | None:
    """Run a goose recipe; return parsed JSON response, or None if none produced."""
    # Goose's --params KEY=VALUE can't handle literal newlines in the value.
    # Encode as \n so Devstral reads them back as line breaks inside the prompt.
    escaped = {k: v.replace("\n", "\\n") for k, v in params.items()}
    argv = [
        "goose", "run", "--recipe", str(recipe),
        "--no-session",
        "--max-turns", str(max_turns),
        "--max-tool-repetitions", "8",
    ]
    for k, v in escaped.items():
        argv += ["--params", f"{k}={v}"]
    result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    combined = (result.stdout or "") + "\n" + (result.stderr or "")
    parsed = _last_json_line(combined)
    if parsed is None:
        sys.stderr.write("error: no valid JSON response from goose\n")
        sys.stderr.write("--- raw output ---\n" + combined)
    return parsed


def run_verifier(question: str, response_json: str) -> tuple[str, str]:
    """Adversarial fit/miss check. Return (verdict, gap). Default to fit on error."""
    result = run_goose(VERIFIER_RECIPE, {"question": question, "response": response_json}, max_turns=8)
    if result is None:
        sys.stderr.write("warning: verifier produced no JSON; assuming fit\n")
        return "fit", ""
    return result.get("verdict", ""), result.get("gap", "")


# --- orchestration --------------------------------------------------------

RETRY_SHARPEN = (
    "\n\nIMPORTANT: Your previous response asserted a negative or scope-limited claim "
    '(e.g. "no callers outside X", "only used in Y") without populating proof_queries[]. '
    "RE-EMIT the full response with proof_queries populated — each entry is a literal "
    "rg/fd/grep/ls/find command whose stdout line count supports the claim. Run the "
    "command yourself first and copy the observed line count into claimed_count. "
    "Do not assert a negative without proof."
)


def _git_toplevel(start: Path) -> Path | None:
    """Return the git toplevel containing `start`, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start if start.is_dir() else start.parent),
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    top = result.stdout.strip()
    return Path(top) if top else None


def derive_repo_root(question: str) -> Path:
    """Pick repo_root from absolute paths in the query; else cwd's toplevel.

    When Claude greps an absolute path outside cwd (e.g. another checkout),
    the command itself is the authoritative scope signal. Honor it — the
    cwd-derived toplevel is only a fallback for commands with no path args
    (e.g. bare `rg pattern`).

    Strategy:
      1. Tokenize with shlex; keep tokens that start with `/` and exist on
         disk. (Regex patterns that happen to contain `/` are filtered out
         by the on-disk-existence check.)
      2. Map each to its git toplevel. If any exist, use the first unique
         one. Multiple distinct toplevels → pick the first and warn;
         citation resolution will just miss paths in the others.
      3. No absolute paths in the query → cwd's git toplevel → cwd itself.
    """
    try:
        tokens = shlex.split(question)
    except ValueError:
        tokens = question.split()

    abs_existing = [Path(t) for t in tokens if t.startswith("/") and Path(t).exists()]
    toplevels: list[Path] = []
    for p in abs_existing:
        top = _git_toplevel(p)
        if top and top not in toplevels:
            toplevels.append(top)

    if toplevels:
        if len(toplevels) > 1:
            sys.stderr.write(
                f"info: query spans {len(toplevels)} repos; using {toplevels[0]}\n"
            )
        return toplevels[0]

    cwd_top = _git_toplevel(Path.cwd())
    return cwd_top or Path.cwd()


def explore(question: str, turns: int, no_retry: bool, no_verify: bool) -> int:
    repo_root = derive_repo_root(question)
    os.chdir(repo_root)

    qualifier_block = extract_qualifiers(question)
    enriched = f"{question}\n\n{qualifier_block}" if qualifier_block else question

    def run_once(q: str) -> tuple[dict | None, int]:
        raw = run_goose(EXPLORE_RECIPE, {"question": q}, max_turns=turns)
        if raw is None:
            return None, 10
        return validate_response(repo_root, raw)

    output, rc = run_once(enriched)

    if rc == 3 and not no_retry:
        sys.stderr.write("info: proof_queries missing/failed — retrying with sharper prompt\n")
        output, rc = run_once(enriched + RETRY_SHARPEN)

    if rc != 10 and not no_verify and output is not None:
        verdict, gap = run_verifier(question, json.dumps(output))
        if verdict == "miss" and gap:
            sys.stderr.write(f"verifier: miss — {gap}\ninfo: retrying main with verifier feedback\n")
            output, rc = run_once(f"{enriched}\n\n## Retry feedback\n{gap}")
        else:
            sys.stderr.write("verifier: fit\n")

    if output is not None:
        # Trim the response before handing it to Claude:
        #  - _validation: operator telemetry, not actionable for Claude → stderr
        #  - references that duplicate a finding's citation: pure noise → drop
        #  - empty fields: omit so the JSON stays scannable
        # The goal is to land at ~300-400 tokens for a typical 4-finding
        # answer instead of ~600+ with the full structure.
        validation = output.pop("_validation", None)
        findings = output.get("findings") or []
        finding_citations = {f.get("citation") for f in findings if f.get("citation")}
        refs = output.get("references") or []
        unique_refs = [r for r in refs if r.get("citation") not in finding_citations]
        if unique_refs:
            output["references"] = unique_refs
        else:
            output.pop("references", None)
        if not output.get("findings"):
            output.pop("findings", None)
        if validation is not None:
            sys.stderr.write(
                f"validation: kept findings={validation.get('kept_findings',0)} "
                f"refs={validation.get('kept_references',0)} "
                f"stripped findings={len(validation.get('stripped_findings',[]))} "
                f"refs={len(validation.get('stripped_references',[]))} "
                f"proofs_passed={validation.get('proofs_passed',True)}\n"
            )
            for s in validation.get("stripped_findings", []):
                sys.stderr.write(f"  stripped finding: {s.get('reason','?')}\n")
            for s in validation.get("stripped_references", []):
                sys.stderr.write(f"  stripped reference: {s.get('reason','?')}\n")
        sys.stdout.write(json.dumps(output) + "\n")

    if rc == 2:
        sys.stderr.write("warning: validation stripped ALL findings — response unreliable\n")
    elif rc == 3:
        sys.stderr.write("warning: proof_queries still failed after retry — response unreliable\n")
    elif rc == 10:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="explore",
        description="Delegate a codebase exploration question to Goose + Mistral Devstral.",
    )
    parser.add_argument("question", help="Natural-language exploration question.")
    parser.add_argument("--turns", type=int, default=120,
                        help="Max tool-use turns for the main recipe (default 120).")
    parser.add_argument("--no-retry", action="store_true",
                        help="Skip the validator-rc=3 retry.")
    parser.add_argument("--no-verify", action="store_true",
                        help="Skip the adversarial verifier pass.")
    args = parser.parse_args()

    question = args.question.strip()
    if not question:
        sys.stderr.write("error: empty question\n")
        return 2

    return explore(question, args.turns, args.no_retry, args.no_verify)


if __name__ == "__main__":
    sys.exit(main())
