# agentcairn — project guide for Claude Code

**What this is:** a local-first agent-memory system. An Obsidian **Markdown vault is the source of truth**; a **rebuildable embedded DuckDB index** provides hybrid retrieval. Daemonless: a `cairn` CLI + an on-demand READ_ONLY MCP server. Capture is via **out-of-band transcript ingestion** + an agent `remember` tool — **not** live hooks.

**Status (2026-06-08):** Design phase. The full spec is committed at
`docs/specs/2026-06-08-agentcairn-design.md` — **read it first.** No implementation code yet.
Next step: turn the spec into an implementation plan (superpowers `writing-plans`).
Build order: `cairn.vault → cairn.index → cairn.embed → cairn.search → cairn.ingest → cairn.mcp`/CLI.

## Locked decisions
- **Name:** package/org/repo `agentcairn`; **CLI command `cairn`**.
- **Language:** Python 3.12+. Distribute via `uv`/`uvx`/`pipx`; MCP launched via `uvx agentcairn`.
- **Index is a disposable cache** — always rebuildable from Markdown (`cairn reindex`); never the source of truth.
- **Default embedder:** FastEmbed `bge-small-en-v1.5` (384-d), behind a pluggable interface (Ollama/cloud opt-in).
- **Retrieval:** BM25 + vector + wikilink-graph-boost + recency/importance, fused with RRF (k=60); degradation ladder down to BM25-only ("never silently dead").
- **Concurrency:** MCP opens DuckDB **READ_ONLY**; one short-lived CLI process is the sole writer. The `.duckdb` lives on **local disk, NOT inside the synced vault folder**.
- **Markdown contract:** basic-memory conventions (frontmatter `title/type/permalink/tags`; observations `- [category] text #tag (ctx)`; relations `- rel_type [[Target]]`; bare `[[link]]` ⇒ implicit `links_to`) + Dataview-compatible inline fields.

## Hard parts / constraints (don't relearn the hard way)
- `cairn.vault` (parse/write Markdown preserving frontmatter order, unresolved forward-refs, and link-rewrite-on-move) is the **hardest** component — build and test it first.
- **Secret redaction before any write is mandatory** (we write plaintext to disk).
- DuckDB VSS HNSW persistence is *experimental* → prefer **in-memory HNSW rebuilt at MCP spawn**. DuckDB-WASM has **no VSS** → a future Obsidian plugin gets BM25+graph only (no in-browser semantic search).
- **No single headline benchmark number** — vendor LoCoMo/LongMemEval figures are self-reported and disputed; validate on LongMemEval-S + LoCoMo with committed scripts before any comparative claim.

## The wedge (keep it crisp)
Closest competitor is `rohitg00/agentmemory` (it already has hybrid+graph, local embeddings, decay, Obsidian *export*). Our narrow-but-real edge: **vault-as-truth (not export) · disposable/rebuildable index · non-lossy by construction · free wikilink graph · daemonless, zero external DB.** If a change blurs this, reconsider it.

## Conventions
- Specs/designs → `docs/specs/` (date-prefixed `YYYY-MM-DD-<topic>-design.md`). No `superpowers/` segment.
- `README.md` is the public positioning doc — advantages framed as design goals until validated.

## Open items
- Choose an OSS license (currently TBD).
- Validate large-vault HNSW rebuild / MCP cold-start latency (sets in-memory-vs-persisted threshold).
- Validate the local embedding baseline on a QA-style metric, not just R@5.
