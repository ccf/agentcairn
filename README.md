# 🪨 agentcairn

**Local-first memory for AI agents — that you can actually read, edit, and own.**

> **Status: design phase (June 2026).** The architecture is specified ([`docs/specs/`](docs/specs/)) and validated against prior art, but implementation hasn't started. The install and CLI examples below are **planned**, not yet real. Feedback and ideas welcome.

agentcairn gives your coding agent durable, high-quality memory — but instead of locking it in an opaque database or a cloud service, **your memories live as plain Markdown in an [Obsidian](https://obsidian.md) vault you own.** A fast, rebuildable [DuckDB](https://duckdb.org) index sits on top for retrieval. Open your vault, read what the agent remembered, fix a wrong fact by hand, or drop in your own notes — and the agent picks it all up.

## Why agentcairn is different

Most agent-memory systems make a database or cloud store the source of truth and treat files (if any) as a one-way export. agentcairn inverts that:

- **📂 Your vault is the source of truth — not an export.** Memory is human-readable Markdown with frontmatter and `[[wikilinks]]`. Edit it in Obsidian; the index honors your edits.
- **♻️ The index is disposable.** DuckDB is a rebuildable cache (`cairn reindex`). Your memory survives a model upgrade, a corrupted index, a schema change, or uninstalling the tool — **zero data loss**, because the truth is just files on disk.
- **🧠 Non-lossy by construction.** The full note is always retained. Distillation only *adds* derived notes that link back to the source — it never silently drops facts it didn't think to extract at write time.
- **🕸️ A free, deterministic knowledge graph.** Your `[[wikilinks]]` and frontmatter *are* the graph — no LLM extraction, no hallucinated entities.
- **🪶 Daemonless, zero external DB.** One embedded DuckDB file does semantic vector search, BM25 full-text, and graph traversal. No always-on server, no Neo4j/Postgres/Qdrant, no required cloud key — just a `cairn` CLI and an on-demand MCP server.
- **🔒 Local-first & private.** Runs offline with local embeddings by default; no telemetry. Cloud models are an optional quality upgrade, never a requirement.

## How it works (planned)

```
Obsidian vault (Markdown + frontmatter + [[links]])   ← source of truth
        │  parse                       ▲ write (agent + you)
        ▼                              │
   rebuildable DuckDB index  (vector + BM25 + graph + recency/importance)
        ▲                              │
        │ reconcile-on-spawn           ▼ READ_ONLY queries
   transcript ingestion ──────────────►  MCP tools: remember · recall · search · build_context
```

- **Capture** reads your agent harness's session transcripts (append-only, already on disk) *out-of-band* — robust by design, with no fragile live hooks — plus an agent-driven `remember` tool for curated, high-value memories.
- **Retrieval** fuses BM25, semantic vectors, and the wikilink graph (Reciprocal Rank Fusion) with recency/importance scoring, and **degrades gracefully** down to keyword-only when no embedding model is available — so recall is *never* silently dead.
- **Hybrid intelligence:** competitive local embeddings (FastEmbed / `bge-small`) out of the box; optional Ollama or cloud models for higher recall.

### Planned CLI

```bash
# planned — not yet implemented
cairn ingest                       # distill recent agent sessions into the vault
cairn recall "how did we fix the auth bug?"
cairn reindex                      # rebuild the index from Markdown (always safe)
cairn serve                        # on-demand MCP server for your agent harness
```

## Roadmap

- **v1** — the core loop: transcript ingestion → Markdown → rebuildable DuckDB index → hybrid recall; MCP + CLI; secret redaction; local embeddings.
- **v1.1** — bi-temporal validity (expressed in frontmatter), cross-encoder reranking, cloud/Ollama embedding tiers.
- **v2** — Obsidian plugin surface, MotherDuck cloud sync, optional LLM entity extraction.

## Prior art & thanks

Learning from [basic-memory](https://github.com/basicmachines-co/basic-memory) (Markdown-as-memory + rebuildable index), Simon Späti's [Obsidian RAG on DuckDB](https://www.ssp.sh/blog/obsidian-rag-duckdb-sql/), and [DuckDB](https://duckdb.org)'s VSS + FTS extensions.

## License

TBD.
