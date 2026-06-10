# `cairn savings` — Design

**Status:** Approved (brainstorm) — 2026-06-10
**Author:** ccf + Claude
**Scope:** A single, contained feature. One implementation plan.

## Goal

Turn agentcairn's benchmark "context-reduction" claim into a **concrete, compounding, personal number**: a local, no-telemetry tally of how much context the user's *actual* recalls have saved versus dumping the whole vault. Surfaced on demand (`cairn savings`) and ambiently (one line in the SessionStart digest), so the value is *visible* and *grows with use*.

## Context & principles (constraints)

agentcairn is local-first: an Obsidian Markdown vault is the source of truth; a rebuildable DuckDB index is an ephemeral cache; the runtime is a daemonless CLI + on-demand read-only MCP server. Capture is ambient (SessionEnd `sweep`). Principles this feature must honor:

- **No telemetry.** A *local* ledger is not telemetry — nothing leaves the machine. The file is plain, inspectable, editable, and deletable by the user.
- **Non-lossy / user owns everything.** The ledger is additive analytics; it never affects retrieval.
- **Daemonless & rebuildable.** No always-on process. The ledger is a side-car that does **not** feed retrieval ranking, so it does not make recall history-dependent and does not compromise index rebuildability. (This is the key distinction from a recall-log that *influences ranking* — see the prior tiering investigation, which rejected that.)
- **Honest by construction.** Every surface states the model and that it is an estimate, not a measured dollar cost.

### Why a ledger (and how we differ from agentmemory)

agentmemory computes "tokens saved" as `observationCount × 80 − min(obs,50) × 38` — magic constants, recomputed live from current memory size, displayed via an always-on daemon viewer. It is a **stateless snapshot keyed on memory size**, not a tally of real recalls, and it requires a daemon.

`cairn savings` instead records **real recall events with real measured token sizes** and sums them. It is genuinely cumulative (grows with *use*, not just vault size), more honest (no magic constants), and fits daemonless (session-paced surfacing instead of a live viewer).

## Resolved decisions

| Fork | Decision |
|---|---|
| Baseline ("saved" vs what?) | **Full-haystack**: `saved = full_haystack_tokens − recalled_tokens` per event. Same model as the published benchmark stat and as agentmemory; labeled "vs. dumping your whole vault." |
| Tracking | **Cumulative JSONL ledger** of real recall events. |
| Default | **On by default, local.** Off-switch `CAIRN_USAGE=0`. |
| Tokenizer | **~4 chars/token heuristic** (zero-dep, model-agnostic) — the *same* estimator as the benchmark, shared from the package. |
| Surface | `cairn savings` CLI + `/agentcairn:savings` + **one ambient line in the SessionStart digest**. |
| What counts as an event | **`recall` only** (not `search`/`build_context`) — see Capture points. |

## Architecture & components

One new module, **`src/cairn/usage.py`**, is the single home for the ledger:

- `estimate_tokens(text: str) -> int` — ~4 chars/token, rounded up; empty/None → 0. **Moved here from the benchmark**; `benchmarks/cairn_bench/token_savings.py` is updated to import it, so the personal number and the published benchmark number use the identical estimator.
- `ledger_path() -> Path` — `$CAIRN_USAGE_PATH` if set, else `~/.cache/agentcairn/usage.jsonl`.
- `enabled() -> bool` — `os.environ.get("CAIRN_USAGE", "1") != "0"`.
- `record(event: str, *, full: int, recalled: int, k: int) -> None` — append one JSONL row. **Best-effort**: returns immediately if disabled; wraps all IO so any error is swallowed (never raises into the recall path). Creates the parent dir if missing.
- `summarize(path: Path | None = None) -> dict` — read the ledger and aggregate: `recalls`, `total_saved`, `total_full`, `total_recalled`, `mean_factor`, `median_factor`, `first_ts`, `last_ts`. Tolerant of malformed/partial lines (skip them).
- `oneline(summary: dict | None = None) -> str` — the SessionStart string, or `""` when there are zero recalls.

Capture is wired into **two call sites** (`recall_tool` and CLI `recall`). The token total cached in the index (`full`) is read at capture time, not recomputed per call (see Token model).

## Ledger schema

JSONL, one object per line, **no query text** (privacy + minimal):

```json
{"v":1,"ts":"2026-06-10T18:00:00Z","event":"recall","k":5,"full":124000,"recalled":2100}
```

- `v` — schema version (1).
- `ts` — ISO-8601 UTC.
- `event` — `"recall"` (the field is retained so `"search"`/`"build_context"` could be added later under distinct labels).
- `k` — requested top-k.
- `full` — whole-haystack token estimate at event time.
- `recalled` — token estimate of the payload actually returned.

Append-only. Even heavy use is a few thousand short lines; no rotation needed (YAGNI). Default location is **outside the vault** (cache dir), consistent with the "no machine state in the source-of-truth" rule.

## Token model

`saved = max(0, full − recalled)` per event.

- **`full`** (whole-haystack baseline): cached in the index `meta` table, computed at reindex/reconcile time as `sum(estimate_tokens(text))` over all `chunks`. The capture path reads this single cached int — it does **not** scan all chunks on every recall. If the meta key is absent (index built before this feature), compute it lazily once and proceed; never fail the recall.
- **`recalled`**: tokens of the hydrated note text the `recall` actually returned to the agent — the honest "what it cost you."

## Capture points

`usage.record(...)` is called, best-effort, right before the content-recall functions return:

- `recall_tool` (`src/cairn/mcp/tools.py`) — `event="recall"`, `recalled = Σ estimate_tokens(note text)`.
- CLI `recall` (`src/cairn/cli.py`) — `event="recall"`.

**`search` and `build_context` are intentionally NOT logged.** They are progressive-disclosure steps (snippet index / graph expansion) that typically *precede* a `recall`; counting them as separate savings events would double-count a single retrieval need and inflate the headline. Logging only `recall` keeps "recalls" unambiguous and the total conservative (it under-claims rather than over-claims). The `event` field is retained so they could be added later under distinct labels if search-only usage proves common.

`full` is read from index meta (with lazy fallback). Each call is guarded so a ledger failure cannot break or slow retrieval.

## Surfaces

**`cairn savings` (new CLI command in `src/cairn/cli.py`):**
- Default: a short report — total tokens saved, number of recalls, mean/median reduction factor, current haystack size, date span, and a footer with the ledger path + `CAIRN_USAGE=0` hint + the honest-estimate caveat.
- `--json` — machine-readable summary (the `summarize()` dict).
- `--oneline` — the single SessionStart line, e.g.
  `🪨 agentcairn has saved you ~2.3M tokens across 318 recalls (≈51× smaller than your full vault)` — empty string when there are zero recalls. The "×" is the lifetime ratio `total_full / total_recalled` (robust to call count), not a per-event average.

**Plugin `commands/savings.md`** — `/agentcairn:savings` runs `uvx --from agentcairn cairn savings`.

**SessionStart digest** (`plugin/scripts/session-start.sh`) — after the existing index-exists check, call `cairn savings --oneline`; if non-empty, prepend it to the digest `additionalContext`. One extra warm `uvx` call (~0.6s); shown only when there are recalls. (Future optimization: combine `recent` + `savings` into one call; out of scope here.)

## Error handling & privacy

- **Best-effort everywhere on the hot path.** `record` and the `full`-from-meta read are wrapped so no exception escapes into `recall`. A corrupt or unwritable ledger degrades to "no savings recorded," never to a broken recall.
- **Disabled cleanly.** `CAIRN_USAGE=0` makes `record` a no-op and `cairn savings` report that tracking is off.
- **No queries stored.** Only counts + timestamps. The file is local, plain JSONL the user can read/edit/`rm`.
- **Honest labels.** Every surface includes: *"estimated (~4 chars/token), vs. dumping your whole vault — a model of context size, not a measured dollar cost."*

## Testing

- `estimate_tokens` parity with the benchmark (same inputs → same outputs); benchmark still imports it.
- `record`: appends a well-formed row; **no-op when `CAIRN_USAGE=0`**; swallows an unwritable-path error (best-effort) without raising.
- `summarize`/`oneline`: aggregate a seeded ledger correctly; tolerate a malformed line; `oneline` is `""` for an empty ledger; the `--oneline` factor equals `total_full / total_recalled`.
- Capture: `recall_tool` and CLI `recall` each write exactly one row with the expected `event`/`full`/`recalled` (tmp ledger via `CAIRN_USAGE_PATH`); a recall still succeeds if the ledger dir is unwritable; **`search_tool` writes NO row** (documents the recall-only decision).
- `full`-in-meta: present after reindex; lazy fallback when absent.
- CLI: `cairn savings` text output, `--json` shape, `--oneline` string (and empty when no data).
- Plugin (offline): `session-start.sh` includes the savings line when the ledger has data; omits it when empty; first-run early-exit still holds.

## Out of scope (YAGNI)

- Counting `search`/`build_context` as savings events (deliberately excluded to avoid double-counting; revisit only if search-only usage proves common).
- `cairn savings --watch` / any live TUI or daemon viewer (anti-daemonless; rejected in brainstorm).
- Dollar-cost conversion or model-specific tokenizers (tiktoken) — keep the zero-dep estimator and label it.
- Ledger rotation/compaction; per-project or per-tag savings breakdowns.
- Combining `recent` + `savings` into one hook call (possible later optimization).
