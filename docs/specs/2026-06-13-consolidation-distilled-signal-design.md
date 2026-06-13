# Consolidation on the Distilled Signal (0.10.1)

**Status:** Approved (2026-06-13)
**Affects:** `src/cairn/cli.py` (neighbor index + sweep wiring), `scripts/eval_consolidate.py`, `src/cairn/ingest/consolidate.py` (gate value), `src/cairn/__init__.py`, `CHANGELOG.md`.
**Builds on / fixes:** [memory-consolidation](2026-06-13-memory-consolidation-design.md) (0.10.0). The consolidation *machinery* (verdicts, `LLMConsolidator`, pipeline step, fail-safe, supersede) is unchanged and correct — this fixes only the **detection signal** the prior-index arm uses.

## Problem (dogfood finding, 0.10.0)

Validating the cosine gate on the live ~391-note vault showed it only separates duplicates on **distilled-vs-distilled** embeddings:

| embed target | top-1 cosine median | notes ≥ 0.88 |
|---|---|---|
| full note body (`[context]` + `[verbatim]`) — what the recall `chunk_embeddings` store | 0.896 | 288 / 391 (74%) |
| distilled `[context]` line only | 0.751 | 8 / 391 |

nomic embeddings of full note bodies cluster by **conversational genre**, not by the specific fact: the highest-cosine pairs (0.94–0.97) are *distinct* same-style notes ("yes i'd love to see" ~ "let's go with c"; "merge pr81" ~ "pr82 is merged"), while the genuine targets sit lower and interspersed (Fly RAM 1GB/2GB/4GB: 0.845–0.93). No gate separates them on full-body embeddings.

The 0.10.0 **within-sweep batch arm already embeds distilled-vs-distilled** (`_memory_text` both sides) → clean signal. But the **prior-index arm** queries the recall `chunk_embeddings` (full body) → noisy. So a full from-scratch re-gate works, but **incremental sweeps consolidate unreliably**, and the shipped gate `0.88` is calibrated for the noisy regime.

## Goal / non-goals

- **Goal:** make the prior-index arm use the same clean distilled signal as the batch arm, re-tune the gate on that signal, and fix the eval script — so consolidation works on incremental sweeps too. Invariants (LLM-tier only, fail-safe DISTINCT, kill-switch, keep-iff-distilled/blend gating, supersede-keeps-and-demotes) unchanged.
- **Non-goals:** persisting a distilled embedding in the index/reconcile (rejected: premature for this scale — see Decision); changing the recall index or its chunk embeddings; merging; the consolidation pipeline step itself (unchanged).

## Decision (brainstorm)

Prior-index arm uses **(i) vault-backed distilled embeddings, computed on-demand per sweep** — NOT (ii) persisted-in-index. Rationale: the vault is small (hundreds of notes), so embedding every live note's `[context]` at sweep start is ~1–2s (batched, only on the LLM tier); it keeps the change isolated to the consolidation path and *removes* the DuckDB dependency + connection lifecycle rather than adding a recall-index schema migration. YAGNI.

## Architecture

### A. `_DistilledNeighborIndex` (replaces `_DuckDBNeighborIndex` in `cli.py`)

A `NeighborIndex` (same protocol: `nearest(text)`, `add(permalink, text, timestamp, path=None)`, `note_superseded(permalink)`) backed by **distilled-text embeddings held in memory** — no DuckDB.

Construction `__init__(self, *, vault_root: Path, subdir: str, embedder)`:
- Glob `vault_root/subdir/*.md`. For each, `parse_note(text)`; **skip** notes whose frontmatter has a truthy `superseded_by` (already-demoted notes must never be matched).
- Extract the note's distilled text via `extract_context(note.body)` (§B). Skip notes with no `[context]` line.
- Embed all extracted texts in batches of `_EMBED_BATCH` (64) to avoid OOM (the same hazard `EmbeddingJudge` guards against; embedding all-at-once on a large vault SIGKILLs).
- Store `self._live: list[tuple[str, list[float], str, str | None, str]]` = `(permalink, vec, text, created_ts, path)`, where `created_ts` is the note's `created` frontmatter (ISO string) and `path` is the absolute file path.
- `self._batch: list[...]` (same shape) for this-sweep writes; `self._superseded: set[str]`.

`nearest(self, text) -> tuple[Neighbor, float] | None`:
- Embed `text` (one short text). Compute cosine against every entry in `self._live` and `self._batch`, skipping any permalink in `self._superseded`. Track the single highest. Return `(Neighbor(permalink, text, timestamp, path), cosine)` if `cosine >= _CONSOLIDATE_GATE`, else `None`.

`add(self, permalink, text, timestamp, path=None)`: embed `text`, append `(permalink, vec, text, timestamp, path)` to `self._batch`.

`note_superseded(self, permalink)`: `self._superseded.add(permalink)` (skips it in both arms thereafter).

`_cosine` helper stays (or is shared); a zero-norm guard returns 0.0.

### B. `extract_context(body: str) -> str | None`

The note body written by the distiller is `- [context] <distilled> #ingested\n- [verbatim] <raw>\n` (or `- [context] <verbatim> #ingested\n` when there was no LLM distillation). Extract the `[context]` payload: regex `- \[context\] (.*?)(?: #ingested)?$` on the first matching line, return the captured text stripped, or `None` if absent. Place this helper as `extract_context(body: str) -> str | None` in **`consolidate.py`** (lightweight, no CLI deps) and import it from both `cli.py` (`_DistilledNeighborIndex`) and `scripts/eval_consolidate.py`, so the production index and the eval tool measure exactly the same text.

### C. Sweep wiring (`cli.py`)

Replace the DuckDB-neighbor block. When `resolve_consolidator()` returns a consolidator:
```python
neighbor_index = _DistilledNeighborIndex(vault_root=vault, subdir="memories", embedder=emb)
```
else `None`. Pass `consolidator` + `neighbor_index` to `ingest_transcripts` as today. **Remove** the `nbr_con = open_index(...)` / `nbr_con.close()` lifecycle and the `vector_search` import (now unused — confirm and delete). The reconcile path (its own `open_index` + `try/finally`) is untouched.

Note: the neighbor index reflects the vault **as it was at sweep start** (loaded once in `__init__`). New writes this sweep are tracked via `add`; supersedes via `note_superseded`. This is the intended behavior (matches the 0.10.0 batch-arm semantics).

### D. Gate re-tune (`consolidate.py`)

`_CONSOLIDATE_GATE` is re-validated on the distilled signal (the plan measures the known dup/supersession pairs' distilled cosine and the above-gate volume) and set to the value that admits those pairs with margin while keeping above-gate volume small. Target **0.85**; the plan finalizes the exact number from measurement and records it. (Distilled signal: ≥0.85 was 18/391, ≥0.88 was 8/391 — the gate trades recall of true pairs against classifier volume.)

### E. `scripts/eval_consolidate.py` fix

- **Batch** the embedding in chunks of 64 (no single giant `.embed(all)` → no OOM).
- Embed the **`[context]` line** (via `extract_context` from `consolidate.py`), not the full note text — so the eval measures the production signal.
- **Exclude** notes whose `superseded_by` is set.
- Output unchanged in spirit (notes count, gate, count ≥ gate, top pairs).

## Edge cases

- **Note with no `[context]`** (hand-authored, or malformed) → skipped at load; never a neighbor. Safe.
- **Empty vault / first sweep** → `_live` empty; `nearest` returns None unless the batch has a hit.
- **Superseded note at load** → excluded from `_live`. A note superseded *during* this sweep → `note_superseded` skips it.
- **Missing `created` frontmatter** → timestamp `None` (classifier still works; recency is a hint).
- **OOM safety** → batched embedding (chunk 64) at load.
- **Non-LLM tier / kill-switch / dry-run** → consolidation skipped entirely (pipeline gate unchanged); the neighbor index isn't even built.
- **Large vault** → O(N) embeds at load + O(N) cosine per candidate; for hundreds of notes this is trivial. (If the vault grows to many thousands, revisit (ii); out of scope now.)

## Testing

**`_DistilledNeighborIndex` / `extract_context` (`tests/test_cli.py`):**
- `extract_context` pulls the distilled text from a `- [context] X #ingested` body, drops the `#ingested` suffix, returns None when absent.
- Construction over a tiny fixture vault: loads live notes, **excludes** a note with `superseded_by` in frontmatter, skips a note with no `[context]`.
- `nearest`: a query close to a preloaded live note returns it (cosine ≥ gate); an orthogonal query returns None; a `add`ed batch note with higher cosine wins over a preloaded one; `note_superseded(permalink)` then excludes that note (returns None / next-best).
- Uses a fake embedder (deterministic axis vectors) so cosines are exact — no model load.

**Eval script (`tests/` or smoke):** run `eval_consolidate.py` against a tiny fixture vault (3–4 notes) and assert it exits 0 and prints the gate line — guards the OOM/regression without the real corpus.

**Gate value:** assert `_CONSOLIDATE_GATE == <chosen>` (documents the re-tune).

**Pipeline consolidation tests:** unchanged (they inject `_FakeNeighborIndex`), confirming the protocol contract held.

## File-by-file summary

| File | Change |
|---|---|
| `src/cairn/cli.py` | replace `_DuckDBNeighborIndex` with `_DistilledNeighborIndex` (vault-backed, in-memory, batched embed, excludes superseded); sweep builds it; remove `nbr_con` lifecycle + `vector_search` import |
| `src/cairn/ingest/consolidate.py` | add `extract_context(body)` helper; re-tune `_CONSOLIDATE_GATE` (≈0.85, finalized in plan) |
| `scripts/eval_consolidate.py` | batch embeddings; embed `[context]`; exclude superseded |
| `src/cairn/__init__.py`, `CHANGELOG.md` | 0.10.1 |

## Open questions

None.
