# Memory Consolidation (semantic dedup + supersession)

**Status:** Approved (2026-06-13)
**Affects:** `src/cairn/ingest/{pipeline,models,consolidate(new)}.py`, `src/cairn/config.py`, `src/cairn/cli.py`, `src/cairn/search/engine.py` (read-only reuse)
**Builds on:** [structural-ingestion](2026-06-11-structural-ingestion-design.md), [layer-b-semantic-judge](2026-06-12-layer-b-semantic-judge-design.md), [bitemporal-validity](2026-06-09-bitemporal-validity-design.md) (`superseded_by` + recall demotion already exist).

## Problem

Two memory-quality gaps surfaced auditing the live ~391-note vault:

- **#2 semantic near-duplicates.** Dedup is exact-hash only (`content_hash(cand.text)`), so the same fact phrased two ways becomes two notes — e.g. *"SigNoz OTel ingest endpoint: https://…"* and *"SigNoz ingestion endpoint is https://…"*.
- **#3 superseded point-in-time state.** Operational facts that evolve are all kept, coexisting and conflicting — e.g. Fly RAM *"scaled to 1GB" → "exceeded 2GB" → "scaled to 2GB" → "4 cores / 4GB"*. An agent asking "how much RAM?" gets four answers.

Both begin with the same primitive — *find the most similar existing memory* — and differ only in the action. They are addressed together as **consolidation**.

## Goals / non-goals

- **Goal:** when a new memory is a near-duplicate of an existing one, skip it; when it's a newer version of the same fact, mark the old one superseded. Local-first, **fail-safe** (a wrong call must never silently drop a *distinct* memory), harness-agnostic (operates on distilled memories, not harness specifics).
- **Non-goals:** merging duplicates into a richer note (skip only); cross-fact summarization; consolidation on the embedding/none tiers; changing recall ranking (the existing ×0.5 demotion is reused as-is); `cairn redistill`.

## Locked decisions (brainstorm)

- **(b) Unified** consolidation (dedup + supersession in one detection pass).
- **(i) LLM classifies** above a cosine pre-gate; **LLM-tier only**; fail-safe default `DISTINCT`.
- **Duplicate → skip** (not merge).
- **Supersedes → keep** the old note in the vault, marked `superseded_by` the new one and demoted ×0.5 in recall (reuse bitemporal infra); never delete.
- Only explicit `DUPLICATE`/`SUPERSEDES` verdicts above a high cosine gate ever drop/demote — **never cosine alone**.

## Architecture

Consolidation is a step in `ingest_transcripts`' Phase C (the per-candidate
gate→distill→write loop), inserted **after distillation, before the write**, and
active only when `report.judge_tier == "llm"` and consolidation is enabled.

```
redact -> exact-dedup -> judge -> gate -> distill -> CONSOLIDATE -> write
```

The pipeline gains two optional, injected dependencies so it stays decoupled from
DuckDB and is trivially testable:

- `consolidator: Consolidator | None` — classifies a (new, existing) memory pair.
- `neighbor_index: NeighborIndex | None` — finds the nearest existing memory by embedding.

Both default to `None` → no consolidation (today's behavior). The CLI wires them
to the real classifier + DuckDB index; tests inject fakes.

### A. New module `src/cairn/ingest/consolidate.py`

```python
class ConsolidationVerdict(StrEnum):
    DISTINCT = "distinct"      # separate facts -> write both
    DUPLICATE = "duplicate"    # same fact, and the new adds nothing newer (same
    #                            value, OR the new is an older/equal version of an
    #                            evolving fact) -> skip the new, keep the existing
    SUPERSEDES = "supersedes"  # the new is a strictly NEWER version of the same
    #                            evolving fact -> write new, mark existing superseded

@dataclass(frozen=True)
class Neighbor:
    permalink: str
    text: str          # the existing memory's distilled text (for the classify prompt)
    timestamp: str | None

class NeighborIndex(Protocol):
    def nearest(self, text: str) -> tuple[Neighbor, float] | None:
        """Return (closest existing memory, cosine similarity), or None if the
        store is empty. The index embeds `text` internally; the pipeline passes
        plain text, not pre-computed vectors. Spans prior-sweep notes AND
        this-sweep's writes."""
    def add(self, permalink: str, text: str, timestamp: str | None) -> None:
        """Register a newly-written memory in the index (embeds internally)."""

class Consolidator(Protocol):
    def classify(
        self, *, new_text: str, new_ts: str | None, neighbor: Neighbor
    ) -> ConsolidationVerdict: ...
```

- `_CONSOLIDATE_GATE: float` — module constant, the cosine threshold below which
  no classify call is made (validated; see §threshold). Default conservative.
- `LLMConsolidator(api_key, model, timeout)` — one small Anthropic call via the
  existing `judge._anthropic_request` seam. Prompt gives both memories + their
  timestamps and asks for a single JSON object
  `{"relation": "distinct|duplicate|supersedes"}`. **Any exception, unparhseable
  response, or unknown value → `DISTINCT`** (write both). No retry/degradation
  machinery — failure is already the safe outcome.
- `resolve_consolidator(env) -> Consolidator | None` — returns an `LLMConsolidator`
  when `CAIRN_JUDGE == "anthropic"` with a key AND `CAIRN_CONSOLIDATE` is on;
  else `None`.

### B. The neighbor index (CLI-wired, `cli.py` sweep)

A concrete `NeighborIndex` backed by the open DuckDB index + the sweep's embedder:

- `nearest(vec)` runs `search.engine.vector_search(con, vec, dim=…, pool=1)` over
  `chunk_embeddings`, mapping the top chunk back to its note (permalink, distilled
  text, timestamp). It also consults an **in-memory list of this-sweep's already-
  written memories** `(permalink, vec, text, ts)`, returning whichever of
  (index-best, batch-best) has the higher cosine. The in-memory arm is what lets a
  single from-scratch rebuild collapse a same-sweep series (the index has no
  this-sweep notes until end-of-sweep reconcile).

The CLI opens the index read-only before ingest (it already opens it after, for
reconcile), passes the `NeighborIndex` + `resolve_consolidator(...)` into
`ingest_transcripts`, then reconciles as today.

### C. Pipeline integration (`pipeline.py`)

In Phase C, candidates that pass the gate are processed in **timestamp order**
(sort `pending`'s kept members by `timestamp`, ascending) so "later supersedes
earlier". For each kept, distilled memory:

1. If `consolidator is None` or `neighbor_index is None` or `report.judge_tier != "llm"`
   → write normally (skip consolidation).
2. `hit = neighbor_index.nearest(M.text)`. The index embeds the text internally
   (the pipeline passes plain text, not vectors). If `hit is None` or
   `cosine < _CONSOLIDATE_GATE` → write normally.
3. `verdict = consolidator.classify(new_text=…, new_ts=…, neighbor=hit.neighbor)`.
   - `DUPLICATE` → do **not** write; `ledger.add(content_hash(cand.text))`;
     `report.semantic_deduped += 1`; continue.
   - `SUPERSEDES` → write the new note; set the **old** note's `superseded_by`
     frontmatter to the new note's permalink (edit the old `.md` in place);
     `report.superseded += 1`. Register the new note in the in-memory batch index.
   - `DISTINCT` → write normally.
4. Every written memory is registered in the in-memory batch index (so later
   same-sweep candidates can match it).

Writing a note already returns its path/permalink; `superseded_by` is written by
a small frontmatter editor (load old note, set the key, rewrite) — the old note's
`content_hash`/mtime change is picked up by the end-of-sweep reconcile, which
applies the ×0.5 recall demotion.

### D. Report + config + models

- `IngestReport` gains `semantic_deduped: int = 0` and `superseded: int = 0`;
  add to `to_dict`. The CLI sweep line reports them when non-zero.
- `config.py` `KNOBS`: `Knob("consolidate", "CAIRN_CONSOLIDATE", "true",
  "Semantic dedup + supersession during ingest (LLM tier only).")` + a
  `resolve_consolidate(env) -> bool` helper. Default on.
- No `Candidate` change needed (timestamp + text already present). The note's
  distilled text used for embedding/classify is the note body the distiller
  produced.

## Data flow (one candidate, LLM tier)

```
distilled memory M (text, ts)
  -> neighbor_index.nearest(M.text) = (N, cos)   # index embeds internally
       cos < gate ............................. write M (DISTINCT path)
       cos >= gate -> consolidator.classify(M, N):
           DISTINCT  ............................ write M
           DUPLICATE ............................ skip M, ledger M.hash
           SUPERSEDES ........................... write M, set N.superseded_by = M.permalink
  -> register M in batch index (if written)       # index embeds internally
```

## Threshold validation (`_CONSOLIDATE_GATE`)

A script (`scripts/eval_consolidate.py`, mirroring `eval_judge.py`) over the real
vault: embed all notes, for the known-duplicate pairs from the audit (SigNoz
endpoint ×2; the primer-demo scale pair) measure cosine, and for a sample of
distinct-but-topically-close neighbors measure cosine. Pick a gate that puts the
dup pairs above and the distinct pairs below, with margin; if they don't separate
cleanly, choose the gate that admits the dups (the LLM then adjudicates) while
keeping the above-gate volume small. Record the chosen value + the separation in
the spec/PR. Conservative bias: a higher gate means fewer classify calls and
fewer chances to drop a distinct memory.

Note: `scripts/eval_consolidate.py` embeds the full note text, whereas production
embeds the distilled memory text (and the index stores chunk text incl. `[verbatim]`);
the eval is an approximate proxy for the gate, not an exact match.

## Edge cases

- **No embedder / empty index / first-ever sweep:** `neighbor_index` is None or
  `nearest` returns None → write normally.
- **Classifier error / API down / malformed response:** → `DISTINCT` → write both.
  The candidate is never lost to a consolidation failure.
- **Non-LLM tier:** consolidation skipped entirely; exact dedup unchanged.
- **`CAIRN_CONSOLIDATE=false`:** kill-switch → consolidation skipped.
- **Same-sweep series (rebuild):** timestamp-ordered processing + in-memory batch
  index chains supersession (1GB←2GB←4GB); only the latest stays live.
- **Dry run:** no writes, no `superseded_by` edits, no ledger adds (consistent
  with existing dry-run behavior).
- **Idempotent re-sweep:** a skipped duplicate is ledgered, so it isn't
  reconsidered; re-setting an existing `superseded_by` to the same value is a
  no-op edit.
- **SUPERSEDES pointing at a this-sweep note:** valid — the old note was just
  written this sweep; editing its fresh `.md` is fine.
- **Incremental sweep of an OLDER transcript matching a NEWER existing note:**
  within-batch timestamp ordering can't order a candidate against the index's
  notes, so the classifier is given both timestamps and returns `DUPLICATE` (the
  new is an older/equal version) → the stale candidate is skipped, the newer note
  kept. (Only a strictly-newer candidate yields `SUPERSEDES`.)

## Testing

**`consolidate.py`:**
- `LLMConsolidator` parses `{"relation": "..."}` for each verdict; unknown value,
  malformed JSON, and a raised request all → `DISTINCT`.
- `resolve_consolidator`: returns LLM consolidator on anthropic+key+enabled; None
  when disabled, no key, or non-anthropic.

**Pipeline (fake `Consolidator` + fake `NeighborIndex`):**
- DUPLICATE → new not written, its hash ledgered, `report.semantic_deduped == 1`.
- SUPERSEDES → new written; the named old note's file gains `superseded_by: <new>`;
  `report.superseded == 1`.
- DISTINCT → both written.
- cosine below gate → classifier NOT called (assert via a spy), note written.
- `judge_tier != "llm"` (embedding/none) → consolidation skipped, classifier not called.
- consolidator raises → treated as DISTINCT, note written (fail-safe).
- within-batch: three candidates (RAM 1/2/4 GB) in one sweep, fake classifier says
  SUPERSEDES for each adjacent pair → only the newest live; the two older notes
  carry `superseded_by`.
- `CAIRN_CONSOLIDATE=false` → skipped.

**CLI / NeighborIndex:**
- `nearest` returns the higher-cosine of (index, in-memory batch); empty store → None.

**Config:** precedence + default-on for `consolidate`; `CAIRN_CONSOLIDATE=0` disables.

## File-by-file summary

| File | Change |
|---|---|
| `src/cairn/ingest/consolidate.py` (new) | `ConsolidationVerdict`, `Neighbor`, `NeighborIndex`/`Consolidator` protocols, `_CONSOLIDATE_GATE`, `LLMConsolidator`, `resolve_consolidator` |
| `src/cairn/ingest/pipeline.py` | Phase C: timestamp-ordered kept candidates; batch-embed; consolidate step (skip/supersede/distinct); in-memory batch index; `superseded_by` edit via a small frontmatter writer |
| `src/cairn/ingest/models.py` | `IngestReport.semantic_deduped`, `.superseded` (+ `to_dict`) |
| `src/cairn/config.py` | `consolidate` knob + `resolve_consolidate` |
| `src/cairn/cli.py` | open index pre-ingest; wire `NeighborIndex` + `resolve_consolidator`; report new counts |
| `scripts/eval_consolidate.py` (new) | gate validation on the real corpus |

## Open questions

None.
