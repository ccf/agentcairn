# Antecedent-Resolved Distillation

**Status:** Approved (2026-06-13)
**Affects:** `src/cairn/ingest/{locate,models,pipeline,judge}.py`
**Builds on:** [structural-ingestion](2026-06-11-structural-ingestion-design.md) (user-turns-only candidate model) and [layer-b-semantic-judge](2026-06-12-layer-b-semantic-judge-design.md) (the LLM distiller).

## Problem

The user-turns-only candidate model (only `AUTHORED_USER` events become candidates) is correct for noise control, but it orphans **confirmation-style decisions** whose referent lived in the assistant's prior turn. Dogfooding the 327-note vault surfaced ~6–10 such notes:

| `[verbatim]` (the user's actual turn) | Distillation (accurate, but not self-contained) |
|---|---|
| "lock A, present the full design" | "Approach A is the decided direction…" — **A of what?** |
| "Go with (i), but make a note of the alternative…" | "Option (i) is selected for implementation…" — **(i) of what?** |
| "All three, but make the cap configurable" | "All three candidate options are to be included…" — **three of what?** |

The distillation is faithful to the turn; the *turn itself* is meaningless without its antecedent. These memories have near-zero recall value: a future agent searching "orderbook representation decision" won't match "Approach A is the decided direction."

## Goal

Make a referent-bearing user turn distill into a **self-contained** memory by letting the LLM judge see the immediately-preceding assistant proposal as **transient resolution context** — without:

1. reintroducing the noise that user-turns-only eliminates,
2. letting the agent's proposals *become* the user's memories, or
3. leaking unredacted secrets from assistant turns.

## Non-goals

- Semantic near-duplicate dedup (separate issue #2 from the audit).
- Superseded point-in-time state / temporal supersession (separate issue #3).
- Changing the embedding tier (it produces no distillation; antecedent is irrelevant there).
- `cairn redistill` (backlogged; cannot resolve antecedents anyway — they aren't stored in notes).

## Decisions (locked during brainstorming)

- **Trigger scope = attach-to-all, judge-gated (c).** Every candidate carries its antecedent into the LLM judge; the *prompt* enforces the resolve-only discipline. No separate heuristic detector to build or tune. Accepted cost: ~2× LLM-judge **input** tokens (antecedent is truncated).
- **Semantics = resolve-only (ii).** The antecedent may only *disambiguate a referent already present in the user's turn*. A contentless turn ("yes", "do it") with no referent of its own must **not** be turned into a decision memory. Keeps memories anchored to turns where the user expressed intent; does not inflate volume.
- **Rollout = ship + cache-version bump + full re-gate (a).** Bump `_JUDGE_CACHE_VERSION` so the next clear-and-resweep re-resolves every orphaned decision.

## Architecture

The change is a thin, additive layer over the existing pipeline order
(`redact → dedup → judge → gate → distill → write`). It touches four files and
adds no new module.

### A. Antecedent capture — `select_candidates` (`pipeline.py`)

`select_candidates(transcript)` already iterates `transcript.events` in order and
filters to `AUTHORED_USER`. It will additionally track the **most recent
`AUTHORED_ASSISTANT` event seen so far in the same session**, and attach its text
to each user candidate as a new optional field.

- The antecedent is the nearest preceding `AUTHORED_ASSISTANT` event whose
  `session_id` matches the candidate's. Reset the "last assistant" tracker when
  the session id changes (a single transcript file can, in principle, carry
  events from more than one session id).
- Intervening `TOOL_RESULT` / `META_INJECTION` / other kinds do **not** clear the
  tracker — only a newer assistant turn (or a session change) replaces it.
- Truncate the antecedent to `_ANTECEDENT_CHARS = 2000` from the **HEAD** (the
  proposal's option list is near the top; consistent with `_JUDGE_INPUT_CHARS`).
- No preceding assistant turn (e.g. the first user turn) → `antecedent is None` →
  identical to today's behavior.

### B. `Candidate.antecedent` field (`models.py`)

Add one optional field to the `Candidate` dataclass:

```python
antecedent: str | None = None  # nearest preceding assistant turn (resolution
# context for the LLM judge ONLY; redacted; never stored in the note)
```

Defaulted, so all existing constructors are unaffected.

### C. Redaction-first (unchanged invariant) — `pipeline.py` Phase A

In Phase A, the candidate text is already redacted before hashing. The antecedent
is redacted in the **same place**, immediately, so no unredacted assistant text is
ever passed to the judge:

```python
red = redact(cand.text)
cand = replace(cand, text=red.text)
if cand.antecedent is not None:
    cand = replace(cand, antecedent=redact(cand.antecedent).text)
```

The antecedent does **not** participate in the dedup hash (`content_hash` stays
over the candidate text only — two identical user turns after different proposals
are still the same turn for dedup purposes; resolution is a distillation concern,
not an identity concern). Antecedent redaction counts toward `report.redactions`.

### D. Judge interface — optional parallel `contexts` (`judge.py`)

Extend the `Judge` protocol method, backward-compatibly:

```python
def judge(self, texts: list[str], *, contexts: list[str | None] | None = None) -> list[Judgment]: ...
```

- `contexts is None` → today's behavior exactly (every existing caller and test
  keeps working unchanged).
- When provided, `contexts[i]` is the redacted antecedent for `texts[i]` (or
  `None`).
- **`EmbeddingJudge` ignores `contexts`** — it computes durability by cosine
  margin and emits no distillation, so the antecedent is irrelevant. (It accepts
  the kwarg to satisfy the protocol.)
- **`LLMJudge` uses `contexts`.** Each numbered item in the prompt body gets an
  optional prior-assistant block; the chunk's `contexts` slice is threaded
  through `_judge_llm` alongside its `texts` slice (chunking already exists —
  keep `texts` and `contexts` index-aligned per chunk).

The pipeline's single batched judge call (`pipeline.py` Phase B) already builds
`to_judge` (indices into `pending`) and sends `[pending[i][0].text for i in
to_judge]`. It now also sends `contexts=[pending[i][0].antecedent for i in
to_judge]` — the same index order, so `texts[k]` and `contexts[k]` correspond.

### E. Prompt — render the block + resolve-only instruction (`judge.py`)

Each item that has an antecedent renders as:

```
[3] PRIOR ASSISTANT MESSAGE (context only): <redacted, truncated antecedent>
    DEVELOPER MESSAGE: <redacted user turn>
```

Items without an antecedent render as today (`[3] <message>`).

Add one paragraph to `_PROMPT`:

> A "PRIOR ASSISTANT MESSAGE" is provided only as context. Use it **only** to
> resolve a referent that appears in the developer's message — e.g. "A",
> "option (i)", "all three", "that approach", "the second one". When you
> resolve such a referent, write the title and distillation so they stand alone
> (name what "A" was). If the developer's message carries no such referent, or
> is itself ephemeral, **ignore the prior message entirely** and judge the
> developer's message exactly as you would without it. Never manufacture a
> decision from a contentless acknowledgement ("yes", "do it", "ok").

### F. What changes in the note — and what does not

- **`[context]` distillation:** now self-contained for resolved turns
  ("Lock approach A: the orderbook representation strategy" rather than
  "Approach A is the decided direction").
- **`[verbatim]`:** **unchanged** — the user's literal redacted words. The
  antecedent is transient context for judging and is **never** written to the
  note, the index, or the judged cache.
- **Keep rule:** **unchanged** — keep iff `judgment.distilled is not None`
  (0.9.3). Resolve-only means a referent-less ack still gets `distilled=None`
  → dropped; a referent-bearing turn gets a resolved distillation → kept (as it
  is today, but now self-contained). Volume is not expected to rise materially.
- **`importance`:** unchanged (LLM tier: `= durability`).

### G. Rollout (`judge.py` + ops)

- Bump `_JUDGE_CACHE_VERSION` (2 → 3): the prompt changed, so prior cached
  verdicts are stale and must be re-judged.
- A full from-scratch re-gate (clear `~/agentcairn/memories/*.md` + the
  `<vault_key>.sha256` ledger + the `<vault_key>.judged.jsonl` cache, then
  `cairn sweep`) re-resolves every orphaned decision. This is the validated
  0.9.4/0.9.5 procedure; ~11 min for the current corpus.

## Edge cases

- **First user turn / no prior assistant:** `antecedent=None` → unchanged behavior.
- **Several user turns in a row:** each takes the same nearest preceding assistant
  turn (the tracker isn't cleared by user turns).
- **Session change mid-file:** the tracker resets on a differing `session_id`, so
  a user turn never resolves against another session's proposal.
- **Resumed session spanning transcript files:** the antecedent is scoped to the
  events of one parsed transcript; a proposal in a prior file is not reached. This
  is acceptable (bounded, fail-safe — worst case is today's behavior).
- **Huge assistant dump as antecedent:** HEAD-truncated to `_ANTECEDENT_CHARS`.
- **Degraded LLM chunk:** the fallback judge (embedding/neutral) ignores
  `contexts` and behaves exactly as in 0.9.4 — degradation is unaffected.

## Testing

**`select_candidates` (capture):**
- A user candidate gets the text of the nearest preceding `AUTHORED_ASSISTANT`.
- Tool/meta events between assistant and user do **not** clear the antecedent.
- A user turn before any assistant turn → `antecedent is None`.
- A `session_id` change resets the tracker (no cross-session antecedent).
- Antecedent longer than `_ANTECEDENT_CHARS` is HEAD-truncated.

**Redaction:**
- An antecedent containing a secret is redacted before reaching the judge
  (assert the judge receives the `[REDACTED:…]` form, and `report.redactions`
  counts it).

**`LLMJudge` (prompt + threading):**
- With `contexts`, the rendered prompt contains the "PRIOR ASSISTANT MESSAGE"
  block for items that have one and not for items that don't; `texts`/`contexts`
  stay index-aligned across chunk boundaries.
- The resolve-only instruction is present in `_PROMPT`.
- `judge(texts)` with no `contexts` kwarg behaves exactly as before (regression).
- `EmbeddingJudge.judge(texts, contexts=…)` ignores `contexts` (same output as
  without).

**End-to-end (`ingest_transcripts`):**
- A referent-bearing turn ("lock A") preceded by a substantive proposal yields a
  **self-contained** distillation (the resolved referent appears in `[context]`),
  and `[verbatim]` is still the user's literal turn.
- A bare "yes, do it" preceded by an *ephemeral* assistant turn is still
  **dropped** (resolve-only; null distillation).
- A self-contained durable turn with no referent is **unaffected** by the
  presence of an antecedent.

**Cache:**
- `_JUDGE_CACHE_VERSION` bumped; a v2 row is discarded on load (existing
  version-discard test already covers the mechanism — update the version constant
  references).

## File-by-file summary

| File | Change |
|---|---|
| `models.py` | `Candidate.antecedent: str \| None = None` |
| `pipeline.py` | `select_candidates` tracks nearest preceding assistant per session; Phase A redacts the antecedent; Phase B passes `contexts=` to `judge.judge` |
| `judge.py` | `Judge.judge(..., *, contexts=None)`; `LLMJudge` renders the prior-assistant block + resolve-only prompt + threads `contexts` through `_judge_llm`/chunking; `EmbeddingJudge` accepts-and-ignores; bump `_JUDGE_CACHE_VERSION`; add `_ANTECEDENT_CHARS` |
| `CHANGELOG.md` / `__init__.py` | version bump |

## Open questions

None.
