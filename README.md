# 🪨 agentcairn

**Local-first memory for AI agents — that you can actually read, edit, and own.**

> **Status: v1 implemented (June 2026).** The full core loop is built, tested, and benchmarked: `cairn.vault` · `cairn.index` · `cairn.embed` · `cairn.search` · `cairn.ingest` · `cairn.mcp` + CLI. Install/usage below is real. v1.1 work (reranker-on, cloud embedding tiers, bi-temporal validity) is informed by the benchmark results below.

agentcairn gives your coding agent durable, high-quality memory — but instead of locking it in an opaque database or a cloud service, **your memories live as plain Markdown in an [Obsidian](https://obsidian.md) vault you own.** A fast, rebuildable [DuckDB](https://duckdb.org) index sits on top for retrieval. Open your vault, read what the agent remembered, fix a wrong fact by hand, or drop in your own notes — and the agent picks it all up.

## Why agentcairn is different

Most agent-memory systems make a database or cloud store the source of truth and treat files (if any) as a one-way export. agentcairn inverts that:

- **📂 Your vault is the source of truth — not an export.** Memory is human-readable Markdown with frontmatter and `[[wikilinks]]`. Edit it in Obsidian; the index honors your edits.
- **♻️ The index is disposable.** DuckDB is a rebuildable cache (`cairn reindex`). Your memory survives a model upgrade, a corrupted index, a schema change, or uninstalling the tool — **zero data loss**, because the truth is just files on disk.
- **🧠 Non-lossy by construction.** The full note is always retained. Distillation only *adds* derived notes that link back to the source — it never silently drops facts it didn't think to extract at write time.
- **🔒 Redaction before every write.** Secrets are scrubbed (regex + entropy + URL-credential detection) before anything — body, title, or tags — reaches the plaintext vault. We write files you can read, so we treat a leaked credential as the worst failure mode.
- **🕸️ A free, deterministic knowledge graph.** Your `[[wikilinks]]` and frontmatter *are* the graph — no LLM extraction, no hallucinated entities.
- **🪶 Daemonless, zero external DB.** One embedded DuckDB file does semantic vector search, BM25 full-text, and graph traversal. No always-on server, no Neo4j/Postgres/Qdrant, no required cloud key — just a `cairn` CLI and an on-demand MCP server.
- **🔍 Honestly measured.** A reproducible LongMemEval-S + LoCoMo harness ships in [`benchmarks/`](benchmarks/) — with real numbers, ablations, and explicit caveats instead of one cherry-picked headline (see below).

## How it works

```
Obsidian vault (Markdown + frontmatter + [[links]])   ← source of truth
        │  parse                       ▲ write (agent + you), redacted
        ▼                              │
   rebuildable DuckDB index  (vector + BM25 + graph-boost)
        ▲                              │
        │ reconcile-on-spawn           ▼ READ_ONLY queries
   transcript ingestion ──────────────►  MCP tools: remember · recall · search · build_context · recent
```

- **Capture** reads your agent harness's session transcripts (append-only, already on disk) *out-of-band* — robust by design, with no fragile live hooks — then redacts → dedups → importance-gates → distills into the vault, non-lossily. Plus an agent-driven `remember` tool for curated, high-value memories.
- **Retrieval** fuses BM25 + semantic vectors with Reciprocal Rank Fusion, applies an optional graph-boost, and **degrades gracefully** down to keyword-only when no embedding model is available — so recall is *never* silently dead. An optional cross-encoder reranker adds precision.
- **Hybrid intelligence:** offline local embeddings (FastEmbed / `bge-small`) out of the box — strongest *in the hybrid fusion* (vector-only trails BM25 on short turns; see the benchmark); optional Ollama or cloud models lift recall further.

### CLI

```bash
uvx agentcairn                                       # on-demand MCP server for your agent harness
cairn ingest --vault ~/vault                         # distill recent agent sessions into the vault
cairn sweep  --vault ~/vault                          # ingest + reindex in one pass (cron-friendly)
cairn recall "how did we fix the auth bug?"          # hybrid recall from the CLI
cairn reindex ~/vault                                # rebuild the index from Markdown (always safe)
cairn doctor                                         # health-check the index
```

## Honestly measured

We benchmark agentcairn the way we'd want a memory system measured — **reproducibly, with ablations, and without a single cherry-picked headline number.** The harness ([`benchmarks/`](benchmarks/)) runs **LongMemEval-S** and **LoCoMo** through a version-pinned downloader (datasets are never vendored), scores retrieval deterministically (recall/nDCG@k, MRR — no API key needed, runs in CI on a synthetic fixture), and offers an opt-in LLM-judged QA layer.

Retrieval ablation on the full LoCoMo set (turn-level, macro-avg, FastEmbed `bge-small`):

| arm | recall@5 | recall@10 | MRR |
|---|---|---|---|
| BM25 only | 0.527 | 0.604 | 0.459 |
| vector only | 0.483 | 0.578 | 0.388 |
| hybrid (RRF) | 0.546 | 0.633 | 0.462 |
| hybrid + graph-boost | 0.546 | 0.633 | 0.462 |
| **hybrid + reranker** | **0.660** | **0.735** | **0.608** |

What we read from this — and say out loud:
- **Hybrid beats either arm alone** — RRF fusion is worth it.
- **The cross-encoder reranker is the biggest lever** (+0.11 recall@5 over hybrid); the "ms-marco domain-shift might hurt" worry didn't materialize on conversational data.
- **Local `bge-small` vector-only trails BM25** here — the embedder is the weak link on short chat turns (hence the v1.1 cloud/Ollama tiers).
- **graph-boost is inert on these corpora** — LoCoMo/LongMemEval have no native `[[wikilink]]` graph, so the boost has nothing to fire on. It's for *real interlinked vaults*, not chat logs, and we don't pretend otherwise.

QA-accuracy numbers (LLM-judged) are available too, but use an Anthropic judge rather than the papers' GPT-4o, so they are **not comparable to published leaderboards** — valid for relative ablation signal only. See [`benchmarks/README.md`](benchmarks/README.md) for how to run it and how to read the numbers.

## Roadmap

- **v1 — done.** The core loop: transcript ingestion → redaction → Markdown → rebuildable DuckDB index → hybrid recall; MCP server + CLI; secret redaction; local embeddings; reproducible benchmark harness.
- **v1.1 — next, prioritized by the benchmark above:**
  - ✅ **Reranker on by default** — the largest measured retrieval lever; `CAIRN_RERANK=0` to disable. *(shipped)*
  - **Ollama embedding tier** — ✅ local models via `CAIRN_EMBEDDER=ollama` (`CAIRN_EMBED_MODEL`/`OLLAMA_HOST`); cloud (OpenAI/Voyage) still pending.
  - **Bi-temporal validity** (frontmatter `valid_from`/`valid_until`/`superseded_by`) — temporal questions retrieve the right turns but answer poorly today; the gap is reasoning-over-time, not retrieval.
  - In-memory HNSW for large-vault retrieval latency.
- **v2** — Obsidian plugin surface, MotherDuck cloud sync, optional LLM entity extraction.

## Prior art & thanks

Learning from [basic-memory](https://github.com/basicmachines-co/basic-memory) (Markdown-as-memory + rebuildable index), Simon Späti's [Obsidian RAG on DuckDB](https://www.ssp.sh/blog/obsidian-rag-duckdb-sql/), and [DuckDB](https://duckdb.org)'s VSS + FTS extensions. Benchmarks use [LongMemEval](https://github.com/xiaowu0162/LongMemEval) (MIT) and [LoCoMo](https://github.com/snap-research/locomo) (CC BY-NC 4.0).

## Development

agentcairn uses [uv](https://docs.astral.sh/uv/) exclusively for dependency management and tooling.

**Do not use pip, poetry, or global virtual environments.**

```bash
# First-time setup
uv sync                         # create .venv and install all deps (including dev)
uv run pre-commit install       # install git hooks (ruff + pytest run on every commit)

# Daily use
uv run pytest                   # run the test suite
uv run cairn --help             # run the CLI
uvx agentcairn                  # run the installed tool ephemerally (as the MCP server does)

# Formatting and linting
uv run ruff format .            # format all Python files
uv run ruff check --fix .       # lint with auto-fix
uv run pre-commit run --all-files

# Benchmarks (offline retrieval metrics need no API key)
uv run pytest benchmarks/tests/                                      # offline synthetic-fixture suite
PYTHONPATH=benchmarks uv run --group bench python -m cairn_bench.run --dataset locomo
```

The MCP server is launched via `uvx agentcairn` — no global install required.

## License

[Apache License 2.0](LICENSE) — permissive, with an explicit patent grant. Copyright © 2026 Charles C. Figueiredo.
