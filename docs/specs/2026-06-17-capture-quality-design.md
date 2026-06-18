# Capture-quality backlog — design

**Date:** 2026-06-17
**Status:** Approved (brainstorm) → ready for implementation plan

## Problem

Three capture-quality issues found in the 2026-06-17 dogfood audit:

1. **Degenerate slugs/permalinks.** ~18% of notes are slugged off a trivial *trigger* turn
   (`is-node-22-being-deprecated`, `looks-good-…`, `1-and-yes-…`) while the real fact lives in
   the title / `[context]` line. Cosmetic for recall (matches on content) but ugly for browsing
   in Obsidian and for `build_context`-by-permalink.
2. **Inert `build_context` graph.** Captured notes contain no `[[wikilinks]]`, so the `links`
   table is empty for them and `build_context` always returns `outgoing: []`, `incoming: []` —
   its 1-hop neighbor feature does nothing.
3. **Compaction skip miscount.** The CLI's "skipped (non-authored)" line is built from the raw
   per-kind event counts (`rep.event_kinds`), so it reports *every* `compact_summary` event as
   skipped even when the latest one was promoted to a `session-summary` note and written.

## Goal

Make slugs reflect the distilled fact; make `build_context` genuinely useful via semantic
neighbors (without polluting the user-owned Markdown vault); and make the ingest report's
compaction numbers reconcile. Keep the vault clean — no machine-generated links written during
capture.

Non-goals (this cycle): writing `[[wikilinks]]`/`related:` into notes and the Obsidian graph —
that is the committed *follow-up* (`cairn link`), built on Part 2's reusable neighbor function.

## Design

### Part 1 — Title-derived slugs

In `ExtractiveDistiller.distill` (`src/cairn/ingest/distill.py`), the user-memory branch currently
computes `slug = f"{_slugify(candidate.text)}-{h[:8]}"` *before* the title. Reorder so the title is
computed first, then slug off it:

```python
title = (j.title if j and j.title else None) or _truncate_title(candidate.text)
slug = f"{_slugify(title)}-{h[:8]}"
```

- When a judge title exists (production / LLM tier), the slug becomes readable and topical.
- When there is no judge (extractive/`CAIRN_JUDGE=none` path), `title` *is* the truncated verbatim,
  so the slug equals today's behavior — strictly no regression.
- The `content_hash[:8]` suffix preserves uniqueness; only NEW notes are affected (existing
  permalinks are stable). The `session-summary` slug is already title/identity-based — unchanged.

### Part 2 — Semantic neighbors in `build_context`

**Reusable core.** Add `semantic_neighbors(con, permalink, embedder, *, k=5, min_score=0.0)` to
`src/cairn/search.py`:
- Look up the note's text (and title) for `permalink` from the index; if absent, return `[]`.
- Embed the text and run the existing cosine vector search over chunk embeddings.
- Aggregate to one row per note, exclude the note itself and any superseded note (reuse the
  validity filter already used by `search`), keep the top `k` above `min_score`.
- Return `[{"permalink", "title", "score"}]`. Best-effort: any failure (no embedder, empty
  index, embed error) → `[]`, never raises.
- This function is deliberately standalone so the deferred `cairn link` write-back can call it.

**Wire into `build_context`.** `build_context_tool` (`src/cairn/mcp/tools.py`) keeps `root`,
`outgoing`, `incoming` (so user-authored `[[wikilinks]]` are still honored) and adds a `related`
field = `semantic_neighbors(...)`. The MCP server (`build_server`) threads its already-resolved
`index` + `embedder` into the tool the same way `search`/`recall` are wired. `build_context` stays
MCP-only (there is no CLI `build_context` command).

Return shape:
```json
{ "root": {...}, "outgoing": [...], "incoming": [...], "related": [{"permalink","title","score"}] }
```

### Part 3 — Honest compaction counting

In the `ingest`/`sweep` report rendering (`src/cairn/cli.py`):
- Surface promoted summaries in the headline: add `· {rep.summaries} summaries` when `rep.summaries > 0`.
- In the "skipped (non-authored)" breakdown, subtract `rep.summaries` from the `compact_summary`
  entry (it equals the number of compaction events promoted — one per session); drop the key if the
  result is 0.

Now the arithmetic reconciles: `compact_summary` events = promoted (counted as `summaries`, present
in `written`) + genuinely-skipped.

## Error handling

- `semantic_neighbors` is wrapped best-effort → `[]` on any error; `build_context` never fails
  because neighbor computation did.
- Superseded notes are excluded from `related` (same validity rule as recall) so a stale note is
  never surfaced as a live neighbor.
- The skip-count subtraction is clamped at 0 (never negative) and the key is dropped at 0.

## Testing

- **Part 1:** `distill` yields a title-derived slug when a judge title is present; falls back to the
  verbatim-derived slug when there is no judge; the permalink remains unique (hash suffix).
- **Part 2:** `semantic_neighbors` returns nearest notes for a note with **no** wikilinks; excludes
  the note itself and superseded notes; honors `min_score`/`k`; empty index → `[]`.
  `build_context_tool` returns a populated `related` list while `outgoing`/`incoming` still reflect
  user-authored `[[wikilinks]]`; with no embedder/empty index `related` is `[]` and the call
  succeeds.
- **Part 3:** a transcript with N compaction events where 1 is promoted → headline shows
  `1 summaries`, `written` includes the session-summary, and the skipped breakdown shows
  `N-1 compact_summary` (and omits it entirely when `N-1 == 0`).

## Rollout

Single release. Part 1 changes only new notes (no migration). Part 2 is additive (new `related`
field; existing consumers ignore it). Part 3 is report-text only. CHANGELOG notes the readable
slugs, the `related` neighbors in `build_context`, and the corrected compaction report.
