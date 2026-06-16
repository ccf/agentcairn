# Capture Compaction Summaries as Session-Summary Memories

**Status:** Approved (2026-06-16)
**Affects:** `src/cairn/ingest/` — `pipeline.py` (promote + gate-bypass), `models.py` (`Candidate.kind`), `distill.py` (session-summary note shape), plus a supersession step; tests. No change to harness adapters (they already emit `COMPACT_SUMMARY` with full provenance), redaction, the judge, the index, or the Markdown contract beyond one new frontmatter field.

## Problem

When a coding-agent session grows past its context window, the harness writes a **dense, model-generated summary** of the session so far (Claude Code: a `type:"user", isCompactSummary:true` record, ~13–15K chars; Codex similarly). agentcairn already *recognizes* these (`EventKind.COMPACT_SUMMARY`) but **drops them** — only `AUTHORED_USER` turns become candidates. These summaries are the single highest-density artifact a session produces: the model's own synthesis of decisions, files, and state. Capturing them — clearly marked as model-generated, project-stamped — gives recall a session-level synthesis layer that atomic user-prompt memories don't provide, and pairs naturally with provenance-aware/project-scoped recall.

## Research (verified on disk, 2026-06-16)

- Claude Code transcripts contain `isCompactSummary:true` records (7 in a long session), each ~13–15K chars of structured summary, delimited by `compact_boundary` system events carrying `cwd`/`gitBranch`. They are cumulative (each re-summarizes prior content).
- `classify_claude_code` returns `EventKind.COMPACT_SUMMARY` for them (`src/cairn/ingest/harness/claude_code.py:69`); the **Codex adapter also emits** `COMPACT_SUMMARY`. `to_event` builds a full `NormalizedEvent` for them — `text` (the summary), `project` (from cwd), `git_branch`, `session_id`, `harness` are all populated.
- `select_candidates` (`pipeline.py`) promotes only `AUTHORED_USER` → `COMPACT_SUMMARY` events flow through and are discarded.
- The pipeline is redact → dedup → (batched) judge → gate → distill → write, with consolidation/supersession in the write pass.

## Goal / decisions (brainstorm)

- **Scope:** Claude Code + Codex (the two adapters that emit `COMPACT_SUMMARY`). Cursor/Antigravity don't expose compaction → out of scope. **User-prompt capture is unchanged** — summaries are an *additional* source at a different granularity.
- **Granularity (A):** store the compaction **verbatim** as one "session summary" derived note (the existing `ExtractiveDistiller` model), not atomically extracted. Non-lossy, no extra LLM pass, no compounding interpretation.
- **Redundancy (i):** **latest compaction per session only** (cumulative ⇒ subsumes earlier). One *current* session-summary note per session.
- **Gating (a):** **bypass the durability judge** (compaction is itself the substance signal). Still **redact** (mandatory) and **dedup**.
- **Trust:** the note is clearly marked **model-generated** (`kind: session-summary`) so recall labels it and the reader knows it's synthesis, not a user-asserted fact.
- **Supersession (non-lossy):** when a newer compaction for a session is captured on a later sweep, the prior session-summary note for that session is marked `superseded_by` the new one (kept, demoted in recall).

## Architecture

### A. `Candidate.kind` (`models.py`)

Add a discriminator so the pipeline can treat summaries differently:
```python
kind: str = "user"   # "user" (authored prompt) | "summary" (compaction session summary)
```

### B. Promote the latest summary (`select_candidates`, `pipeline.py`)

Today the loop skips everything but `AUTHORED_USER`. Add: track the **latest** `COMPACT_SUMMARY` event per session while iterating, then after the loop emit one summary `Candidate(kind="summary")` per session from that latest event. The summary candidate carries the event's `text`, `session_id`, `cwd`/`project`, `git_branch`, `timestamp`, `source_path`, `harness`; `antecedent=None` (not judged). User candidates are emitted exactly as before. (Latest = last `COMPACT_SUMMARY` by stream order / timestamp within the session.)

### C. Bypass judge + gate for summaries (`ingest_transcripts`, `pipeline.py`)

- **Phase A (redact/dedup):** unchanged — summary candidates are redacted and deduped by `content_hash` like any candidate (so an unchanged summary on a re-sweep is skipped via the ledger).
- **Phase B (judge):** **exclude `kind=="summary"` candidates** from the `to_judge` set — they never hit the judge.
- **Phase C (gate):** for `kind=="summary"`, force `keep = True` (skip the durability/heuristic gate); set a fixed/high `importance` (e.g. `0.9`) or leave it unset — these are always kept. They proceed to the write pass.

### D. Session-summary note shape (`distill.py`)

`ExtractiveDistiller.distill` branches on `candidate.kind`:
- For `"summary"`: frontmatter `type: memory`, **`kind: session-summary`**, `tags: ["session-summary", "ingested"]`, `source: memory://session/<session_id>`, plus `project`/`harness` (already persisted by the provenance work), `created` from the candidate timestamp; title derived from the session (e.g. `"Session summary — <project> <date>"`). Body = the **verbatim** summary (the existing `- [context] … #ingested` / `- [verbatim] …` shape, or a single verbatim block). No judge `distilled` line (it bypassed the judge).
- For `"user"`: unchanged.

### E. Supersession — one current summary per session

After the new session-summary note for a session is written, **supersede any prior session-summary note for the same `session_id`**: find existing notes with `kind: session-summary` and the same `source`/session that aren't already superseded, and `mark_superseded(path, by=new_permalink)` (the existing helper). Implementation: a small step in the write pass (or a dedicated `supersede_prior_session_summary(vault, session_id, new_permalink)` that scans `memories/` for matching frontmatter). This keeps one *current* summary per session; earlier ones remain in the vault, demoted by recall's validity factor. (Within a single sweep only the latest compaction is emitted per §B, so this only fires across sweeps when a session continues and re-compacts.)

### F. Recall / surfaces (no new code)

Provenance-aware recall (shipped) already carries `project`/`harness` and currency. With `kind: session-summary` in frontmatter + the index already storing it (or via the `tags`), recall can label results `[session summary]` and demote superseded ones; the Obsidian plugin's currency/provenance view surfaces them for free. (If a recall-time label is desired, it reads the existing `kind`/tag — additive, optional.)

## Data flow

```
cairn sweep
  → parse_transcript → NormalizedEvents (COMPACT_SUMMARY already carries text+project+session+harness)
  → select_candidates: AUTHORED_USER (as today) + latest COMPACT_SUMMARY per session (kind="summary")
  → redact (all) → dedup (all)
  → judge: user candidates only; summary candidates bypass
  → gate: user candidates by durability; summary candidates always keep
  → distill: user → memory note; summary → session-summary note (verbatim, model-generated, project-stamped)
  → write pass: supersede prior session-summary for the same session
  → reindex (unchanged)
```

## Error handling / correctness

- **No misclassification risk:** identification is the adapter's existing battle-tested `COMPACT_SUMMARY` flag — a real user prompt is never treated as a summary, and vice-versa.
- **Redaction is mandatory** on summaries (they can contain secrets) — same Phase-A path, before any hash/write.
- **Idempotent:** unchanged summary on re-sweep → deduped by content hash (ledger), no duplicate write, no spurious supersession.
- **Latest-only:** earlier compactions in the same transcript are not written (only the final per session).
- **Degrade-safe:** summary capture adds no judge/LLM dependency (it bypasses the judge), so it can't be broken by judge/provider failures.
- **Cross-harness:** Claude Code + Codex only; other harnesses simply never produce `COMPACT_SUMMARY`, so nothing changes for them.

## Testing / verification

- **select_candidates:** a fixture transcript with 3 `COMPACT_SUMMARY` events (same session) + several `AUTHORED_USER` → candidates include the user turns **and exactly one** summary candidate (`kind="summary"`) = the latest; its `project`/`harness`/`session_id` are populated.
- **Gate bypass:** a summary candidate is kept even with a judge that would reject it (and never appears in the judge's input batch).
- **Redaction:** a secret embedded in a summary is redacted before the note is written.
- **Note shape:** the written note has `kind: session-summary`, the `session-summary` tag, the verbatim summary body, and `project`/`harness` provenance.
- **Supersession:** sweep a session with one compaction → one current summary note; re-sweep the same session with a newer (different) compaction → new note current, prior `superseded_by` it; an unchanged re-sweep writes nothing.
- **Isolation:** user-prompt capture and counts are unchanged by the feature (regression check on existing ingest tests).
- `uv run pytest` green; `uv run ruff` clean.
- **Dogfood:** `cairn sweep` over a real Claude Code vault → a session-summary note per compacted session, model-generated/project-stamped, recall surfaces it for a session-level query, no secret leakage.

## File-by-file

| File | Change |
|---|---|
| `src/cairn/ingest/models.py` | add `Candidate.kind` |
| `src/cairn/ingest/pipeline.py` | `select_candidates` promotes latest `COMPACT_SUMMARY`/session; `ingest_transcripts` excludes summaries from judge + force-keeps them; supersede prior session-summary in the write pass |
| `src/cairn/ingest/distill.py` | session-summary frontmatter/body branch for `kind=="summary"`; `supersede_prior_session_summary` helper (or reuse `mark_superseded`) |
| `tests/ingest/` | select latest-summary, gate bypass, redaction, note shape, supersession, user-capture isolation |

## Non-goals

- **Atomic extraction** from summaries (Option B / future LLM-distiller).
- **Cursor / Antigravity** compaction (they don't expose it).
- **The `/insights` idea (Idea 2)** — separate.
- Changing user-prompt capture, the judge, redaction, or the index schema (the `kind` field rides in frontmatter; recall labeling is additive/optional).

## Open questions

None.
