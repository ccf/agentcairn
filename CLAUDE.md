# agentcairn — project guide for Claude Code

**What this is:** a local-first agent-memory system. An Obsidian **Markdown vault is the source of truth**; a **rebuildable embedded DuckDB index** provides hybrid retrieval. Daemonless: a `cairn` CLI + an on-demand READ_ONLY MCP server. Capture is via **out-of-band transcript ingestion** + an agent `remember` tool — **not** live hooks.

**Status (2026-06-13):** Shipped and on PyPI — current **0.10.1**. The full loop is implemented
(`cairn.vault/index/embed/search/ingest/mcp` + CLI + Claude Code plugin). Specs live in `docs/specs/`
(the original is `2026-06-08-agentcairn-design.md`; later ones cover the config file, the Layer-B LLM
judge, antecedent resolution, and memory consolidation). Releases follow the cut-a-release ritual
(CHANGELOG → tag → Trusted-Publishing → GitHub Release).

**Plugins:** agentcairn ships a **Claude Code plugin**, a **Codex plugin**, and an **Antigravity plugin** — all three reuse the same `using-agentcairn-memory` skill and bundled MCP server. The Codex plugin is discoverable via the Codex plugin marketplace; the Antigravity plugin installs from a local directory (`agy plugin install` takes a dir or a registered marketplace, not a git repo — so `cairn install antigravity --source <plugin dir>`). `cairn install` routes by host kind: for plugin hosts (`claude-code`, `codex`, `antigravity`) it installs the plugin by shelling to the host's own CLI; for MCP hosts (`cursor`, `claude-desktop`, `vscode`, `gemini`) it writes the MCP server config. The Antigravity plugin bundles the MCP server + skill; `cairn install antigravity` (via `agy plugin install`) also migrates away any stale `mcpServers.agentcairn` entry from `~/.gemini/config/mcp_config.json`. Gemini, Cursor, and the other MCP hosts do not have plugins. **Cursor is the exception among MCP hosts:** it has no plugin system, but `cairn install cursor` ALSO installs the `using-agentcairn-memory` skill (alongside writing `~/.cursor/mcp.json`) via Cursor's `Host.skill_dir` (`~/.cursor/skills`), writing `~/.cursor/skills/using-agentcairn-memory/SKILL.md` — so any future host exposing a `skill_dir` can ship the memory skill the same way without being a plugin host.

**Capture pipeline (ingest):** transcripts from Claude Code, Codex, Antigravity CLI (`~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`), and Cursor (`<CursorUser>/globalStorage/state.vscdb`, SQLite `cursorDiskKV`) are ingested automatically
(auto-detected via a `HarnessAdapter` seam; `cairn sweep` picks up whichever harnesses are present). Gemini CLI transcript ingestion is not supported (Google is sunsetting Gemini CLI in favour of Antigravity CLI).
Then: redact → structural candidate selection (authored user turns only) →
judge (embedding/LLM tier; durability + optional distillation) → gate → distill → **consolidate**
(semantic dedup + supersession, LLM-tier, fail-safe) → write. Redaction-before-write is mandatory;
the judged cache is version-stamped (`_JUDGE_CACHE_VERSION`).

## Locked decisions
- **Name:** package/org/repo `agentcairn`; **CLI command `cairn`**.
- **Language:** Python 3.12+. Distribute via `uv`/`uvx`/`pipx`; MCP launched via `uvx agentcairn`.
- **Index is a disposable cache** — always rebuildable from Markdown (`cairn reindex`); never the source of truth.
- **Default embedder:** FastEmbed `nomic-embed-text-v1.5` (768-d) — a 5-model sweep settled it (best quality-per-dim; beat the original `bge-small-en-v1.5` 384-d default). Pluggable interface (Ollama/cloud opt-in).
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

## Tooling (uv — exclusive)

**Always use `uv`; never use pip, poetry, or global venvs.**

- Install / sync deps: `uv sync`
- Run any command: `uv run <cmd>` (e.g. `uv run pytest`, `uv run cairn --help`)
- Ephemeral tool runs: `uvx <tool>` — the MCP server will be launched as `uvx agentcairn`
- Contributor setup: `uv sync` then `uv run pre-commit install`

`uv.lock` is **committed** — it keeps the tool reproducible across machines.

## Conventions
- Specs/designs → `docs/specs/` (date-prefixed `YYYY-MM-DD-<topic>-design.md`). No `superpowers/` segment.
- `README.md` is the public positioning doc — advantages framed as design goals until validated.
- **License: Apache-2.0.** Start each source file with `# SPDX-License-Identifier: Apache-2.0`; keep `NOTICE` intact and propagate it in distributions; set `license = "Apache-2.0"` in `pyproject.toml` when scaffolding.

## Open items
- Validate large-vault HNSW rebuild / MCP cold-start latency (sets in-memory-vs-persisted threshold).
- Validate the local embedding baseline on a QA-style metric, not just R@5.
