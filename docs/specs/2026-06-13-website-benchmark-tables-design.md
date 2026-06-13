# Website Benchmark Tables

**Status:** Approved (2026-06-13)
**Affects:** `website/src/lib/content.ts` (data), `website/src/components/Measured.astro` (markup). Docs-only / marketing-site change — no `cairn` package code.

## Problem

The website's "Benchmarks measured" section (`Measured.astro`) renders **one** table (LoCoMo retrieval) and reduces **LongMemEval-S** and **context efficiency** to prose sentences — even though the README has full validated tables for both. The section under-sells the measured results.

## Goal / constraints

- Present **both retrieval benchmarks (LoCoMo + LongMemEval-S) as tables**, plus context efficiency as a small third table.
- **Honesty (CLAUDE.md hard rule):** every number must mirror the README's validated tables (which come from the committed `benchmarks/` harness). No invented or rounded-differently figures. A source-of-truth comment in `content.ts` points back to the README/`benchmarks/` so the two stay in sync.
- Reuse the existing table styling; the page is positioning, not the exhaustive report — the README/`benchmarks/README.md` remains the complete record.

## Decisions (brainstorm)

- LongMemEval-S table is **trimmed (b)** to the three headline columns (`session r@5`, `session MRR`, `turn r@5`) the prose already emphasizes; the full 5-metric table stays in the README.
- Context efficiency becomes a **third small table**.

## Design

### Data (`website/src/lib/content.ts` — the `benchmark` object)

Add two arrays (mirroring README's validated numbers) and captions; keep the existing `rows` (LoCoMo) and `caveats`. Replace the `longmemeval` and `contextEfficiency` prose strings with table data + short captions:

```ts
export const benchmark = {
  locomoCaption:
    "LoCoMo retrieval, turn-level macro-avg, FastEmbed nomic-embed-text-v1.5 (the default).",
  rows: [ /* unchanged LoCoMo rows: BM25 / vector / hybrid / hybrid+reranker (strong) */ ],

  // LongMemEval-S — full 500-instance set. Numbers mirror README "Retrieval —
  // LongMemEval-S" (source of truth: benchmarks/). Trimmed to headline columns;
  // full turn r@10/MRR live in the README.
  longmemevalCaption: "LongMemEval-S, full 500-instance set. Full turn r@10/MRR in the README.",
  longmemevalRows: [
    { arm: "BM25 only",          sessionR5: "0.920", sessionMrr: "0.918", turnR5: "0.680", strong: false },
    { arm: "vector only",        sessionR5: "0.936", sessionMrr: "0.916", turnR5: "0.507", strong: false },
    { arm: "hybrid (RRF)",       sessionR5: "0.954", sessionMrr: "0.938", turnR5: "0.640", strong: false },
    { arm: "hybrid + reranker",  sessionR5: "0.969", sessionMrr: "0.963", turnR5: "0.788", strong: true },
  ],

  // Context efficiency — mirrors README "Context efficiency" table (default config,
  // hybrid + reranker, k=10). Estimate (~4 chars/token), not a billed cost.
  contextCaption: "Context the default config recalls vs the full history. Estimate (~4 chars/tok).",
  contextRows: [
    { dataset: "LoCoMo",        haystack: "25,646 tok",  recalled: "529 tok",   reduction: "51.1×" },
    { dataset: "LongMemEval-S", haystack: "136,552 tok", recalled: "2,207 tok", reduction: "64.7×" },
  ],

  caveats: [ /* unchanged */ ],
};
```

(The `caption` field is renamed `locomoCaption` for clarity; the prose `longmemeval`/`contextEfficiency` strings are removed.)

### Markup (`website/src/components/Measured.astro`)

Three labeled sub-blocks inside the existing `<Section id="measured">`, each = a small heading + a table reusing the current table classes + a serif caption. To stay DRY, the two retrieval tables share the same column-rendering pattern (they differ only in headers/keys), but given there are just two distinct shapes (4-col retrieval-ish and the context table), inline markup per table is acceptable and clearer than a generic table component here.

- **Sub-block 1 — "Retrieval — LoCoMo":** the existing table (`arm · r@5 · r@10 · MRR`) over `benchmark.rows`, `locomoCaption`.
- **Sub-block 2 — "Retrieval — LongMemEval-S":** new table (`arm · session r@5 · session MRR · turn r@5`) over `longmemevalRows`, `longmemevalCaption`. `strong` rows get the same emphasis class as LoCoMo's.
- **Sub-block 3 — "Context efficiency":** new small table (`dataset · haystack · recalled · reduction`) over `contextRows`, `reduction` cell emphasized; `contextCaption`.
- The `caveats` list stays at the bottom of the section, unchanged.

Each table keeps `class="w-full border-collapse font-mono text-[13px]"` and the existing header/row classes so the three read as a matched set. Sub-block headings use a small muted label (e.g. `font-sans text-[13px] text-[var(--color-ink-muted)]`), spacing between blocks via `mt-8`.

## Non-goals

- No new benchmark runs or number changes — pure presentation of existing validated figures.
- No QA-accuracy table (the README keeps it qualitative — Anthropic judge, not comparable to leaderboards).
- No responsive redesign beyond what the trimmed columns already buy; the 4-column tables fit the existing prose width.

## Testing / verification

- `cd website && npm run build` (or the project's build cmd) succeeds — Astro type-checks the `benchmark` object usage, so a missing/renamed field fails the build.
- Each website number is cross-checked against the README table it mirrors (LoCoMo rows, LongMemEval headline columns, context-efficiency rows) — a reviewer diffs the two.
- Visual check: three tables render, `strong`/reduction emphasis applied, captions present, caveats intact.

## File-by-file

| File | Change |
|---|---|
| `website/src/lib/content.ts` | add `longmemevalRows`/`contextRows` + captions; rename `caption`→`locomoCaption`; drop `longmemeval`/`contextEfficiency` prose strings |
| `website/src/components/Measured.astro` | render three labeled tables (LoCoMo + LongMemEval-S + context efficiency); keep caveats |

## Open questions

None.
