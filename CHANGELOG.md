# Changelog

All notable changes to **agentcairn** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

### Added
- `cairn savings` — a local, no-telemetry cumulative token-savings ledger: records each real recall (`full_haystack_tokens` vs `recalled_tokens`) to `~/.cache/agentcairn/usage.jsonl` and reports how much context recall has saved vs. dumping the whole vault. Surfaced via `cairn savings` (`--json`/`--oneline`), the `/agentcairn:savings` plugin command, and a line in the SessionStart digest. On by default and local; disable with `CAIRN_USAGE=0`. (Estimated, ~4 chars/token — a model of context size, not a measured cost.)

## [0.2.0] - 2026-06-10

### Added
- `cairn recent` — most-recently-modified notes (`--project` path-substring filter, `-n`/`--num`, `--json`); powers the plugin's SessionStart digest.
- `cairn init` — scaffold an Obsidian-ready vault (idempotent, non-destructive).
- **Claude Code plugin** (in-repo marketplace): auto-wires the `uvx agentcairn` MCP server, surfaces recent memory at SessionStart (with zero-step vault auto-init), distills each session at SessionEnd, and adds the `using-agentcairn-memory` skill plus `/agentcairn:recall|remember|memory|ingest` commands.

## [0.1.0] - 2026-06-10

### Added
- Initial public release. Markdown **vault is the source of truth**; a rebuildable, ephemeral DuckDB index is a disposable cache.
- Hybrid retrieval — vector (cosine) + BM25 (FTS) + wikilink-graph boost, fused with RRF, with a cross-encoder reranker **on by default** (`CAIRN_RERANK=0` to disable).
- `cairn` CLI: `parse`, `reindex`, `index-status`, `recall`, `ingest`, `sweep`, `doctor`, `serve`; on-demand MCP server via `uvx agentcairn` (`recall`/`search`/`build_context`/`recent`/`remember`).
- Embedders: FastEmbed (default `nomic-embed-text-v1.5`, configurable via `CAIRN_EMBED_MODEL`) and an Ollama tier (`CAIRN_EMBEDDER=ollama`).
- Bi-temporal validity: `valid_from`/`valid_until`/`superseded_by` frontmatter; recall soft-demotes superseded/expired notes (non-lossy — never hidden).
- Out-of-band capture from coding-agent transcripts (redacted, non-lossy `remember`).
- Published to PyPI via GitHub Trusted Publishing (OIDC, no stored secrets).

[Unreleased]: https://github.com/ccf/agentcairn/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/ccf/agentcairn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ccf/agentcairn/releases/tag/v0.1.0
