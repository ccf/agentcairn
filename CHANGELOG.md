# Changelog

All notable changes to **agentcairn** are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [0.7.0] - 2026-06-11

### Changed
- **Ingestion now selects candidates by transcript structure, not text patterns.** A new normalized `EventKind` taxonomy + a positive-identification, fail-closed Claude Code classifier (keyed on `isMeta`/`toolUseResult`/`isCompactSummary`/`isVisibleInTranscriptOnly`/`origin`) means only genuinely human-authored turns become memories. This deterministically excludes tool output, slash-command/skill injections, `<task-notification>` events, and compaction summaries — without enumerating their text. An unmapped entry type or new harness yields zero candidates (safe, loud) rather than noise. `cairn ingest` now reports a per-kind skip tally; event provenance (origin project) is preserved through the pipeline for future use.

### Removed
- The text-pattern `is_framing_noise` denylist (0.6.1/0.6.2) — subsumed by structural classification. `sanitize_text` (escape/control stripping) stays.

## [0.6.2] - 2026-06-11

### Fixed
- **Broaden harness-framing filter to the full family.** 0.6.1 filtered slash-command and tool-output turns; this also drops `<task-notification>` background-task events (by far the most common — they were a large fraction of ingested noise), `<local-command-caveat>` boilerplate, and `<user-prompt-submit-hook>` output. The `<local-command*>` variants are now matched by prefix so future ones are covered too.

## [0.6.1] - 2026-06-11

### Fixed
- **Terminal escape sequences no longer leak into the vault.** Ingestion now strips ANSI/OSC escape codes and stray C0 control bytes from transcript text before anything is hashed, scored, or written — slash-command output (e.g. `/context`) and tool dumps were previously stored with raw `\e[…m` sequences and box-drawing art.
- **Harness framing is no longer ingested as memories.** User-role turns that are mechanically injected by the harness — slash-command output/markers (`<local-command-stdout>`, `<command-name>`, …), tool-result dumps (`<bash-stdout>`/`<bash-stderr>`), and "This session is being continued from a previous conversation…" compaction summaries — are now filtered out at candidate selection. (They were clearing the importance gate because their length, inflated by escape-code digits, scored above threshold.)

## [0.6.0] - 2026-06-11

### Added
- `cairn install` now supports **VS Code (Copilot)** (`cairn install vscode`) and **Antigravity** (`cairn install antigravity`). VS Code's config uses a `servers` top-level key (not `mcpServers`); the JSON writer now takes a configurable `root_key` to handle it. Antigravity reads `~/.gemini/config/mcp_config.json`.

### Removed
- Dropped the **Windsurf** host — Windsurf was renamed to Devin Desktop (2026-06-02) and its Cascade agent is EOL; the old `~/.codeium/windsurf` config path is no longer current. (Use `cairn install … --print` to wire up any unsupported host by hand.)

## [0.5.0] - 2026-06-11

### Added
- `cairn install <host>` — wire the agentcairn MCP server into other MCP hosts beyond Claude Code. Supports **Cursor**, **Claude Desktop**, **Windsurf**, **Gemini CLI** (JSON `mcpServers`) and **Codex** (TOML `[mcp_servers.agentcairn]`). `cairn install` with no argument detects installed hosts and previews (writes nothing); `--all` configures every detected host; `--print` emits the snippet without touching disk; `--vault`/`--index` override paths (absolute-ized before writing). Writes are non-destructive (other servers + unrelated keys preserved), idempotent, backup-first (`<config>.bak`), and atomic (temp file + rename, so a crash mid-write can't corrupt a live config); a malformed existing config is backed up and reported without being clobbered. The vault stays a single global `~/agentcairn`, so memory is shared across hosts.

### Changed
- README: new **"Agents supported"** matrix (Claude Code first-class plugin vs `cairn install` MCP-server hosts, with an ambient-capture column); the benchmark section ("Benchmarks measured") now presents LongMemEval-S as a table alongside LoCoMo and context efficiency as a table.

### Dependencies
- Added **`tomlkit`** (round-trips Codex TOML comments/formatting when merging the MCP entry).

## [0.4.0] - 2026-06-10

### Added
- `cairn warm` — pre-downloads the configured embedder + reranker models (best-effort, config-aware). The plugin's detached first-run job calls it so the first SessionEnd `sweep` and first `recall` aren't slowed by a model download.

## [0.3.0] - 2026-06-10

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

[Unreleased]: https://github.com/ccf/agentcairn/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/ccf/agentcairn/compare/v0.6.2...v0.7.0
[0.6.2]: https://github.com/ccf/agentcairn/compare/v0.6.1...v0.6.2
[0.6.1]: https://github.com/ccf/agentcairn/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/ccf/agentcairn/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/ccf/agentcairn/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/ccf/agentcairn/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/ccf/agentcairn/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/ccf/agentcairn/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/ccf/agentcairn/releases/tag/v0.1.0
