# Automatic per-turn recall (UserPromptSubmit) — design

**Date:** 2026-06-29
**Status:** approved (brainstorm); pending implementation plan
**Scope:** Claude Code plugin (Codex is a verified fast-follow, out of scope here)

## Problem

agentcairn is currently **write-mostly** on Claude Code: sessions are captured and
the index is healthy, but *semantic recall is essentially never triggered*.

Empirical evidence (user's machine, 2026-06-29):

- `~/.cache/agentcairn/usage.jsonl` records **only** `recall` events. The last
  entry was `2026-06-24T18:38` — **zero recalls in ~4 days of active multi-session
  use**, despite many session starts.
- `cairn recall` works correctly (returns ranked, cited results); the index is
  healthy (696 notes / 892 chunks / 892 embeds, local `nomic-embed-text-v1.5`).
- So the gap is on the **trigger** side, not capture, indexing, or the engine.

Root cause, confirmed in code:

- The Claude Code plugin wires only `SessionStart`, `SessionEnd`, `PreCompact`
  hooks — **no per-prompt hook**. `SessionStart` injects `cairn recent -n 5`, a
  pure `ORDER BY mtime DESC` **recency digest** (no query, no embeddings, no
  ranking) — and `recent` does **not** write to `usage.jsonl`, which is exactly
  why the ledger stayed frozen.
- The `recall` MCP tool and the `using-agentcairn-memory` skill exist but are
  **LLM-discretion-gated** — they fire only if the model chooses to invoke them,
  and in practice it does not.
- Per-prompt auto-recall was **deliberately omitted** in the original plugin spec
  (`docs/specs/2026-06-10-agentcairn-claude-code-plugin-design.md` §13) for
  "latency/noise/cost" reasons. This design revisits that decision: the empirical
  cost of the omission is that recall never happens at all.

Notably, agentcairn's **OpenCode** and **Hermes** integrations already do
automatic per-turn relevance recall (OpenCode via `chat.message` +
`experimental.chat.system.transform → cairn recall`; Hermes via `prefetch()`).
The flagship host is the laggard. This design ports that proven pattern to Claude
Code's `UserPromptSubmit` hook.

## Goals

- On each substantive Claude Code user prompt, run a hybrid recall against the
  prompt and inject the top hits as context for that turn.
- Keep the existing `SessionStart` recency digest unchanged (complementary:
  recency-orientation at start + relevance per turn).
- Default-on, configurable via `~/.agentcairn/config.toml`.
- Never block, slow, or break a prompt (fail-open).

## Non-goals (YAGNI)

- Codex port (verified fast-follow once its per-prompt hook capability is
  confirmed).
- Topic-aware / "new topic" gating (a simple length gate suffices).
- Deduplication of per-turn recall against the `SessionStart` digest (recency vs
  relevance overlap is minimal; dedup adds hot-path latency).
- A resident embedder daemon (pre-warm + per-call load is sufficient).

## Settled decisions (from brainstorm)

1. **Keep both** — `SessionStart` digest *and* per-turn relevance recall,
   complementary.
2. **Claude Code only** now; Codex fast-follow.
3. **Skip trivially-short prompts** via a simple length gate.
4. **Default-on, configurable** in `config.toml` (`auto`, `k`, `scope`).
5. **Architecture:** a thin Python command `cairn recall-hook` holds all logic; a
   small shell wrapper wires it to the hook (mirrors the project's "thin shell
   over the cairn CLI" philosophy, e.g. the OpenCode TS plugin).

## Architecture

### New files

- **`src/cairn/recall_hook.py`** — all logic, as pure/testable units:
  - `should_recall(prompt: str, cfg) -> bool` — config master-switch **and** the
    trivial-prompt length gate.
  - `format_block(notes: list[dict]) -> str` — render recalled notes into the
    injection markdown; returns `""` when there is nothing to inject.
  - `build_hook_output(block: str) -> dict` — the Claude Code `UserPromptSubmit`
    hook JSON envelope.
  - `run(stdin_text: str, env, vault) -> str` — orchestrates: parse stdin →
    `should_recall` → hybrid `search()` → `format_block` → `build_hook_output`;
    returns the string to print (empty string ⇒ print nothing).
- **`plugin/scripts/user-prompt-submit.sh`** — thin wrapper. Resolves the vault
  exactly like `session-start.sh`
  (`${CLAUDE_PLUGIN_OPTION_VAULT_PATH:-${1:-$HOME/agentcairn}}`, `~`-expanded),
  then `exec`s `cairn recall-hook --vault "$VAULT"` with stdin inherited.
- **`tests/test_recall_hook.py`** — unit tests (see Testing).

### Modified files

- **`src/cairn/cli.py`** — add `@app.command("recall-hook")` that reads stdin and
  delegates to `recall_hook.run(...)`, printing its result. Reuses the existing
  `search()` / embedder-resolution path shared with `recall`.
- **`plugin/hooks/hooks.json`** — add a `UserPromptSubmit` entry calling
  `user-prompt-submit.sh` with a safety-ceiling `timeout` (~10s; the BM25
  cold-fallback keeps the expected latency far below this, and on timeout Claude
  Code proceeds without injection — fail-open).
- **`plugin/scripts/session-start.sh`** — fire a detached, best-effort
  `cairn warm` on each start to pre-load the embedder (idempotent; cheap when the
  model is already cached). The first-run path already does this; generalize it.
- **`src/cairn/config.py`** — add `KNOBS` entries + `resolve_*()` functions for
  the recall knobs (see Config).
- **`README.md`** + docs — document automatic recall and the `[recall]` config.

## Config surface

New `[recall]` section in `~/.agentcairn/config.toml`. Each key maps to a
`CAIRN_*` env var through the existing `KNOBS` / `_translate` machinery, preserving
the established precedence: **explicit arg → env → config file → default**.

```toml
[recall]
auto  = true     # CAIRN_AUTO_RECALL       — master on/off (default: true)
k     = 3        # CAIRN_AUTO_RECALL_K      — notes injected per turn (default: 3)
scope = "all"    # CAIRN_AUTO_RECALL_SCOPE  — "all" (boost, non-lossy) | "project" (hard filter)
```

- `resolve_auto_recall(env) -> bool` (default `True`)
- `resolve_auto_recall_k(env) -> int` (default `3`)
- `resolve_auto_recall_scope(env) -> str` (default `"all"`)

Rerank is **forced off** on this hot path (latency). The trivial-prompt length
threshold is a module constant (env-overridable via `CAIRN_AUTO_RECALL_MIN_CHARS`,
default ~12) but intentionally **not** surfaced in `config.toml` (YAGNI).

## Data flow (per turn)

1. User submits a prompt. Claude Code fires the `UserPromptSubmit` hook, invoking
   `user-prompt-submit.sh` with the hook JSON on **stdin**
   (`{prompt, cwd, session_id, …}`).
2. Wrapper resolves the vault and `exec`s `cairn recall-hook --vault "$VAULT"`,
   stdin inherited.
3. `recall-hook` parses stdin and extracts `prompt`. If `auto` is disabled **or**
   `should_recall` rejects the prompt (too short) → **exit 0, no output**.
4. Otherwise it runs the hybrid `search()` (`k`, `scope`, `project=cwd` boost,
   **no rerank**), formats the block, and prints:
   ```json
   {"hookSpecificOutput":{"hookEventName":"UserPromptSubmit",
    "additionalContext":"## Relevant memories (agentcairn)\n\n…"}}
   ```
   then exits 0.
5. Claude Code injects `additionalContext` into the model's context for that turn.
6. The recall path calls `usage.record("recall", …)`, so `usage.jsonl` starts
   moving again and the savings stat reflects **real** interactive recall.

### Injection format

Mirrors the OpenCode plugin's `formatMemoryBlock` for cross-host consistency, with
a permalink so the model can cite (per the `using-agentcairn-memory` skill):

```
## Relevant memories (agentcairn)

<note text>
— [[<permalink>]]

---

<note text>
— [[<permalink>]]
```

Recall JSON shape consumed (from `cairn recall --json`):
`[{permalink, title, text, score}, …]`.

## The trivial-prompt gate

`should_recall` skips recall when `len(prompt.strip())` is below the threshold
(~12 chars). Drops bare continuations ("yes", "go", "A"); keeps substantive short
prompts ("lets check on the pr's"). No topic detection.

## Latency / warm strategy

- **Pre-warm:** `SessionStart` spawns a detached `cairn warm` so the embedder's
  model files are cached on disk.
- **Per-call:** each `recall-hook` invocation loads the model from cache
  (~0.5–1.5s warm). OpenCode already shells `cairn recall` per turn, so this is
  proven tolerable in practice.
- **Graceful cold fallback:** if the embedder is not yet warm, `recall-hook` runs
  **BM25-only** (`embedder=none`, instant) for that turn instead of blocking on a
  cold model load — keyword recall immediately, full hybrid once warm.
- The hook `timeout` is a safety ceiling, not the expected latency.

## Error handling — fail-open, always

A `UserPromptSubmit` hook that exits non-zero **blocks the prompt**. Therefore
`recall-hook` **always exits 0** and prints nothing on any problem: missing index,
stdin parse error, recall exception, timeout, or empty results. Every path is
wrapped. Recalled content is already redacted at write time (redaction-before-
write), so the hook needs no additional redaction.

## Testing

**Unit (`tests/test_recall_hook.py`):**

- `should_recall`: trivial prompts skipped; substantive prompts pass; respects
  `auto=false`.
- Config resolution: `resolve_auto_recall{,_k,_scope}` honor env and `config.toml`
  with correct precedence and default-on.
- `format_block`: empty/all-blank notes → `""` (no injection); populated → correct
  markdown with permalinks.
- `build_hook_output`: correct `UserPromptSubmit` envelope.
- Fail-open: no index / malformed stdin → `run` returns `""` and the command exits
  0 (never raises).
- BM25 cold-fallback: when the embedder is unavailable, recall still returns
  keyword hits rather than erroring.

**Plugin wiring (`plugin/tests/`):** assert `hooks.json` contains a
`UserPromptSubmit` entry pointing at `user-prompt-submit.sh`. (Note: `plugin/tests/`
runs only under CI's `validate` job or manually — `uv run pytest plugin/tests/` —
not the default `testpaths`.)

## Cross-host note

OpenCode and Hermes already auto-recall per turn. After this lands, Claude Code
reaches parity. The **Codex** plugin shares `session-start.sh` and would take the
same wrapper + `hooks.codex.json` `UserPromptSubmit`-equivalent entry **iff** Codex
exposes a per-prompt hook — to be verified, then shipped as a fast-follow.

## Rollout

- Plugin version bump (`plugin/.claude-plugin/plugin.json`).
- CHANGELOG entry.
- No index/schema migration; no breaking change. Existing users get auto-recall on
  next plugin update (default-on); opt out via `config.toml`/`CAIRN_AUTO_RECALL=0`.
