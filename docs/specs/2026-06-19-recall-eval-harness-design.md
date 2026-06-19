# Recall-Eval Harness (#45 leg 1) — Design

**Status:** approved 2026-06-19
**Issue:** #45 (e2e dogfood the full system per host) — this is **leg 1** of that suite.
**Goal:** A test harness that (a) guards the capture→index→recall loop end-to-end (the path the new PreCompact hook exercises) and (b) becomes the measurable **ruler** for recall-quality tuning — so we can change recall ranking and *know* whether it improved or regressed.

## Background

The 2026-06-19 dogfood found two things:
1. Capture was missing whole sessions (fixed: PreCompact hook, plugin 0.3.0).
2. **Recall ranking is the remaining quality gap:** session-summary mega-notes dominate results and the *same note* returns multiple times (different chunks), crowding out atomic memories. Example: a query paraphrasing the "cairn link scope" note returned the giant "Session summary · 2026-06-18" note **twice** instead.

Ranking changes (demote summaries, etc.) cannot be made safely without a measurement. This harness is that measurement, plus the one obviously-correct fix (dedup-by-note).

Scope is **leg 1 only** (core loop + recall-quality eval + dedup-by-note). #45 legs 2–4 (MCP-over-stdio contract, per-host install matrix, CC-plugin hook leg) are deferred — they already have unit coverage and aren't needed for the recall ruler. This leg leaves clean seams to add them.

## Architecture

Two test tiers sharing fixtures, plus one production change (dedup-by-note in recall).

### Tier 1 — Plumbing smoke (always-on, offline, hermetic)

Exercises the real capture→index→recall wiring with no network/keys, fast enough for default CI.

- Temp vault (pytest `tmp_path`), isolated config (existing `conftest` autouse fixtures).
- Ingest a fixture transcript via the real ingest path with `embedder="fake"` and `judge="none"` (deterministic, offline) → reindex.
- Assert: the expected memory note(s) exist in the vault, and `recall("<query for that fact>")` returns the expected note (membership only — **no ranking claim**, because fake vectors make ranking meaningless).

This is the regression guard for the loop the PreCompact/SessionEnd sweep runs.

### Tier 2 — Recall-quality eval (gated, real embeddings)

The ruler. Runs only when `CAIRN_E2E=1` is set (a dedicated CI job sets it); skipped — never failed — otherwise, or if the `fastembed` model can't load offline.

- Seed a temp vault with **authored** Markdown notes (committed fixtures): several atomic fact notes + one realistic session-summary mega-note modeled on the real ones. Authoring the notes (rather than ingesting a transcript) isolates *ranking quality* from distillation/LLM non-determinism.
- Reindex with the real `fastembed` embedder; recall with the cross-encoder reranker on (defaults).
- For each labeled `(query, expected_note)` pair: assert the expected atomic note is in top-k **and** ranks above the session-summary note for that query.
- Compute and **print** `recall@k` and `MRR` over the labeled set. The printed metric is the before/after comparison point for any future recall tuning.

## Components & files

- `tests/fixtures/dogfood_session.jsonl` — synthetic Claude Code transcript: a handful of distinct atomic decisions across turns + one compaction-summary turn (so the ingest path yields both atomic notes and a `session-summary` note). Deterministic; committed.
- `tests/fixtures/recall_eval/` — authored vault notes for Tier 2: `*.md` atomic-fact notes + one `session-summary-*.md` mega-note, plus a `labels.json` (or in-test list) of `(query, expected_permalink)` pairs.
- `tests/e2e/__init__.py`, `tests/e2e/test_recall_eval.py` — both tiers:
  - `test_core_loop_offline` (Tier 1, always-on).
  - `test_recall_quality` (Tier 2, `skipif(not CAIRN_E2E)`), with the metric print + ranking assertions.
- `src/cairn/search/engine.py` — **dedup-by-note**: collapse multiple result chunks of the same note into one result, keeping the best-scoring chunk. (Exact insertion point confirmed during planning by reading the recall result-assembly path; recall is currently chunk-level — the dogfood showed one note returning twice.)
- `tests/search/test_engine.py` (or a new focused test) — unit test for dedup-by-note: given results with two chunks of the same note, recall returns that note once at its best score.

## Data flow

Tier 1: fixture transcript → `ingest`/sweep (fake embedder, no judge) → vault notes → reindex → DuckDB index → `recall` → assert membership.

Tier 2: authored vault notes → reindex (fastembed) → `recall` (+rerank) → ranked results → dedup-by-note → assert ranking + emit metric.

## Error handling / hermeticity

- Both tiers use `tmp_path` vaults and the existing config-isolation fixtures — no read of the developer's real vault/config.
- Tier 1 is fully offline/keyless.
- Tier 2 skips (not fails) when `CAIRN_E2E` is unset or the embedding model is unavailable, so default CI and offline dev are never blocked.

## CI

- Default suite: runs Tier 1 (Tier 2 auto-skips).
- A separate CI job sets `CAIRN_E2E=1` to run Tier 2 (model download cached), matching the gated benchmark-harness pattern the issue cites.

## Out of scope (deferred to #45 legs 2–4)

MCP-over-stdio tool contract, per-host `cairn install` matrix, Claude Code plugin hook leg. The harness is structured so these become additional modules under `tests/e2e/` later.

## Definition of done

- Tier 1 runs in default CI and guards capture→index→recall.
- Tier 2 (gated) asserts atomic facts outrank the session-summary note and prints recall@k/MRR.
- dedup-by-note shipped with a unit test; recall returns each note at most once.
- #45 stays open for legs 2–4.
