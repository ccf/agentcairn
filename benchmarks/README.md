# agentcairn benchmark harness

Measures agentcairn **retrieval** quality (and optional end-to-end **QA accuracy**) on
LongMemEval-S and LoCoMo across a 5-arm ablation. Dev/research tool — NOT part of the
shipped `agentcairn` package.

## Quick start (offline, synthetic fixtures)

```bash
uv run pytest benchmarks/tests/      # offline, no keys, exact-recall regression
```

## Real datasets (manual)

```bash
# retrieval only (downloads revision/commit-pinned; SHA256 verified on/after first fetch)
PYTHONPATH=benchmarks uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --limit 50
PYTHONPATH=benchmarks uv run --group bench python -m cairn_bench.run --dataset locomo
```

- **LongMemEval-S**: HuggingFace `xiaowu0162/longmemeval-cleaned` (MIT), revision-pinned.
- **LoCoMo**: GitHub `snap-research/locomo` (**CC BY-NC 4.0** — NonCommercial; never vendored).

## How to read the numbers (do not overclaim)

- **No single headline number.** Report ranges per arm/dataset/granularity.
- **Retrieval ≠ QA.** Never compare a retrieval recall to a QA accuracy.
- **Ablation is relative.** The arms differ only in the retrieval config; the absolute
  numbers depend on the embedder, k, and dataset slice — all pinned in each result row.
- **`graph-boost` is near-inert** on these conversational corpora (they have no native
  wikilink graph); the row ≈ plain hybrid by design. Cairn's graph wedge is for real vaults.
- **The reranker may lose** on chat turns (ms-marco domain shift) — that's a real result.
- **QA judge is Anthropic, not GPT-4o** — our QA numbers are NOT comparable to published
  LongMemEval/LoCoMo leaderboards. Use for relative ablation signal only.
- **Wrong-gold ceilings**: LoCoMo has a documented ~6.4% wrong-gold rate; cap claims at ~93.6%.
- Per-category accuracy carries **Wilson 95% CIs** — many adjacent comparisons are
  statistically indistinguishable; don't over-read orderings.
- LongMemEval "paper-style" `recall_all@k`/`ndcg_any@k` are labeled separately from our
  fractional `recall@k`; don't conflate.

## LongMemEval-S retrieval (full 500-instance set, FastEmbed `nomic`)

```bash
PYTHONPATH=benchmarks uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --limit 500
```

Session-level (the granularity prior work reports) + turn-level macro-avg:

| arm | session r@5 | session MRR | turn r@5 | turn r@10 | turn MRR |
|---|---|---|---|---|---|
| bm25-only | 0.920 | 0.918 | 0.680 | 0.791 | 0.638 |
| vector-only | 0.936 | 0.916 | 0.507 | 0.692 | 0.454 |
| hybrid-rrf | 0.954 | 0.938 | 0.640 | 0.798 | 0.544 |
| **hybrid+reranker** | **0.969** | **0.963** | **0.788** | **0.891** | **0.716** |

Read honestly:
- **At full scale, session recall@5 discriminates** (0.920 BM25 → 0.969 reranker) — it does *not*
  saturate the way a small sample does. Our **0.969** session r@5 sits right alongside prior work's
  ≈0.95 over the same full 500-question set.
- **The cross-encoder reranker is again the largest lever** — turn r@5 0.640 → **0.788**, session
  r@5 0.954 → 0.969.
- **Turn-level is corpus-revealing:** here BM25-only (0.680) *beats* the RRF hybrid (0.640) because
  vector-only is weak on these single-turn evidence spans (0.507); the reranker is what pulls the
  defaulted config ahead (0.788). (Contrast LoCoMo, where vector-only edges out BM25.)

## Embedding-model sweep (LoCoMo) — why `nomic` is the default

Five FastEmbed models, full LoCoMo set, turn-level macro-avg. BM25-only is
embedder-independent (≈ .527 recall@5 for all) and `graph-boost` ≈ hybrid on these
corpora, so both are omitted below. To reproduce any row:

```bash
CAIRN_EMBED_MODEL=<model> PYTHONPATH=benchmarks uv run --group bench \
  python -m cairn_bench.run --dataset locomo
```

recall@5 / recall@10 / MRR, by embedder:

| arm | all-MiniLM-L6-v2 (384d) | bge-small-en-v1.5 (384d) | mxbai-embed-large-v1 (1024d) | **nomic-embed-text-v1.5 (768d)** | bge-large-en-v1.5 (1024d) |
|---|---|---|---|---|---|
| vector-only | .386 / .476 / .318 | .483 / .578 / .388 | .502 / .616 / .416 | **.536 / .637 / .433** | .535 / .649 / .455 |
| hybrid-rrf  | .507 / .598 / .422 | .546 / .633 / .462 | .547 / .643 / .466 | **.562 / .648 / .477** | .566 / .655 / .486 |
| hybrid+reranker | .659 / .730 / .607 | .660 / .735 / .608 | .661 / .735 / .609 | **.662 / .735 / .608** | .660 / .735 / .608 |

Three findings:

1. **`nomic` and `bge-large` are co-leaders; the 384-d models trail.** `nomic` (768-d) and
   `bge-large` (1024-d) are neck-and-neck on vector-only and are the only models whose
   vector-only beats BM25. `bge-large` takes a hair more on hybrid recall@10, at 1024-d cost
   and ~2.5× the indexing wall-clock.
2. **Dimension is not the lever — model quality is.** The clean proof: `mxbai-large` is also
   1024-d but lands *below* 768-d `nomic`. You cannot buy recall with wider vectors.
3. **The reranker erases the spread.** With the default cross-encoder on, every model
   converges to ~.66 recall@5 — it re-scores the same candidate pool, so the embedder choice
   barely moves the *defaulted* number. The embedder matters most on the no-reranker /
   vector-forward path.

**Decision:** ship **`nomic-embed-text-v1.5`** as the default embedder — best quality-per-dim,
same speed class as `bge-small`, no Ollama server needed, and it lifts vector-only above BM25.
(`bge-small` remains a fine lighter 384-d option via `CAIRN_EMBED_MODEL=BAAI/bge-small-en-v1.5`.)
Switching the default changes `model_id`, so an existing index reconciles (re-embeds the vault)
on first run after upgrade — non-lossy, since the Markdown vault is the source of truth.

## Context-token savings (`--token-savings`)

How much smaller is the context agentcairn *recalls* than the full history you'd otherwise
carry into the model? Measured retrieval-only with the default config (hybrid + cross-encoder
reranker, k=10), over a sample of each dataset:

| dataset (sample) | queries | mean haystack | mean recalled (k=10) | context reduction |
|---|---|---|---|---|
| LoCoMo (3 convos) | 497 | 25,646 tok | 529 tok | **51.1× mean / 50.3× median** |
| LongMemEval-S (full 500) | 470 | 136,552 tok | 2,207 tok | **64.7× mean / 61.6× median** |

```bash
PYTHONPATH=benchmarks uv run --group bench python -m cairn_bench.run \
  --dataset longmemeval-s --token-savings --limit 20
```

Read honestly:
- **Estimate, not a billed cost.** Tokens via a ~4-chars/token heuristic; "full" = the entire
  indexed haystack (what you'd paste if you dumped the vault), "recalled" = the top-k chunks
  agentcairn returns. The factor is `full / recalled`.
- The reduction is **~50× at k=10 on both corpora**; the *absolute* savings scale with history
  size (~25k tokens/query on LoCoMo, ~134k tokens/query on LongMemEval-S).
- It measures *context size*, independent of retrieval quality (recall/nDCG tables above).
- Samples, not full sets — rerun with a larger `--limit` for tighter intervals before quoting.
