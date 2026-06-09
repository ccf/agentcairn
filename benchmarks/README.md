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
uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --limit 50
uv run --group bench python -m cairn_bench.run --dataset locomo
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
