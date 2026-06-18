# `cairn link` — design

**Date:** 2026-06-18
**Status:** Approved (brainstorm) → ready for implementation plan

## Problem

Captured memory notes contain no `[[wikilinks]]`, so the Obsidian graph for the vault is
edge-starved (and was previously cut for that reason). 0.19.0 added `semantic_neighbors()` and a
live `related` field on `build_context`, but nothing is persisted to the vault — so Obsidian still
has no edges to draw. The user wants real wikilinks and a working Obsidian graph.

## Goal

An **opt-in** `cairn link` command that writes each note's top semantic neighbors into a `related:`
frontmatter list of `[[wikilink]]`-formatted strings. Obsidian renders those as graph edges +
backlinks. The command is idempotent (writes a file only when its links changed) and the tool fully
owns the `related:` field.

Non-goals: parser/indexer integration (this is Obsidian-graph-focused — `build_context` already
surfaces neighbors live via the `related` field, so the agentcairn link table is intentionally NOT
fed from `related:`); writing both link directions (Obsidian backlinks show the reverse); automatic
linking during capture (this is a deliberate, occasional command, not part of `sweep`).

## Design

### Command

```
cairn link [--vault V] [--top 5] [--min-score 0.6] [--index PATH] [--dry-run]
```

- `--vault` → `CAIRN_VAULT` → `~/agentcairn` (via `paths.resolve_vault`).
- index via `paths.index_for(index, vault)` — escape hatch (`--index`/`CAIRN_INDEX`) honored.
- `--top` (default 5) and `--min-score` (default 0.6) are tunable starting points: link the top-N
  neighbors whose cosine ≥ the floor. They are starting points the user can adjust after seeing the
  graph; 0.6 is conservative enough to avoid a hairball without being so high the graph stays empty.
- `--dry-run` computes and reports counts without writing.

The command **reads** the index (chunk vectors) and **writes** the vault (frontmatter). It links
only notes present in the index — it does not reindex; a stale index yields stale links, so the
help text notes "run `cairn sweep`/`reindex` first" for fresh results.

### Core logic (reuses `semantic_neighbors`)

Iterate the notes in the index (e.g. `SELECT permalink, path FROM notes WHERE superseded_by IS NULL`).
For each:

1. `nbrs = semantic_neighbors(con, permalink, k=top, min_score=min_score)` — already excludes the
   note itself and superseded targets.
2. `desired = [f"[[{n['permalink']}]]" for n in nbrs]` (wikilink-strings; Obsidian renders edges).
3. Read the file with `parse_note`; compare `frontmatter.get("related")` to `desired`:
   - **Different and `desired` non-empty** → set `frontmatter["related"] = desired`, `write_note`.
   - **`desired` empty and a `related:` key exists** → remove the key, `write_note` (tool owns the field).
   - **Equal** (including both absent) → leave the file untouched (no mtime/git churn).

`write_note` is a parse→write fixpoint, so the body and all other frontmatter are preserved; only
`related:` changes. Links are one-directional (each note → its neighbors); Obsidian's backlinks pane
shows the reverse.

### Idempotency / churn

Every run recomputes from the current index and rewrites a file **only when its `related:` list
actually differs**. A no-change re-run touches nothing. Because neighbors drift as the vault grows,
this is a deliberate, occasional command — re-run it to refresh the graph.

### Error handling

- Best-effort per note: a parse/read/write failure on one note is logged to stderr and skipped;
  the run continues (mirrors the ingest fail-safe ethos). The command never aborts mid-vault because
  of one bad file.
- Missing index → a clear message and exit 1 (consistent with `recall`/`doctor`).
- `--dry-run` performs all computation and reports `N linked, M unchanged, K cleared` but writes
  nothing.
- Final summary line on a real run: `linked N · unchanged M · cleared K` (K = notes whose stale
  `related:` was removed).

### Components / files

- **`src/cairn/cli.py`** — new `link` command (option parsing, vault/index resolution, iterate +
  summary). Keep the per-note transform in a small helper for testability.
- **Reuses** `cairn.search.engine.semantic_neighbors`, `cairn.paths` (vault/index resolution),
  `cairn.vault.parse_note` / `write_note`.
- No new module is required; if the per-note logic grows, factor a `def _relink_note(...) -> str`
  ("linked" | "unchanged" | "cleared") helper in `cli.py` or a small `cairn/linkback.py`.

## Testing

- **Links the near note, not the far one:** a 3-note vault (two semantically near, one far),
  `cairn link` → the near note's frontmatter gets `related: ["[[neighbor]]"]`, the far note doesn't;
  body and other frontmatter unchanged (round-trip).
- **Idempotent:** a second `cairn link` with no vault change rewrites nothing (assert file mtimes
  unchanged, or that the summary reports 0 linked / all unchanged).
- **Clears stale links:** seed a note with a `related:` whose neighbors now fall below threshold (or
  whose target was removed) → after `cairn link` the `related:` key is gone.
- **`--dry-run`:** writes nothing (mtimes unchanged) but the summary reports the would-be counts.
- **Superseded:** a superseded note is neither written a `related:` nor listed as another note's
  neighbor.
- **Missing index:** `cairn link --vault <no-index>` exits 1 with a message.
- **Wikilink format:** the written value is a YAML list of `"[[<permalink>]]"` strings (the form
  Obsidian treats as graph links).

## Rollout

Single release. Additive (new command); no migration; existing notes only gain a `related:` field
when the user runs `cairn link`. CHANGELOG notes the new opt-in command and that it populates the
Obsidian graph from semantic neighbors.
