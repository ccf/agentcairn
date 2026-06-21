# agentcairn — Hermes memory provider

agentcairn is a [Hermes](https://github.com/hermesagent/hermes) `MemoryProvider` plugin that gives Hermes local-first, vault-native memory backed by your own plain-Markdown Obsidian vault.

**It is the only Hermes memory provider that is:**

- **Vault-native and human-editable.** Memories are plain Markdown files with YAML frontmatter and `[[wikilinks]]` — open them in Obsidian, fix a wrong fact by hand, drop in your own notes, and the agent picks it all up on the next session.
- **Local-first by default.** No cloud account, no network call, no always-on server required. The only storage is files on your disk.
- **Deterministic graph.** Your `[[wikilinks]]` and frontmatter *are* the graph — no LLM entity extraction, no hallucinated edges.
- **Secret-redacted before every write.** Regex + entropy + URL-credential detection runs before any text reaches the vault.
- **Non-lossy.** Distillation only adds derived notes; it never drops facts it didn't extract at write time.
- **Cross-agent.** It writes to the same `CAIRN_VAULT` used by the Claude Code, Codex, and Cursor plugins — one unified brain across agents.

## How it works

- **`prefetch`** — at the start of each turn, agentcairn runs a hybrid BM25 + semantic vector recall against your vault and injects relevant memories into the context automatically.
- **`sync_turn`** — each user/assistant turn is buffered in memory (no I/O on the hot path).
- **`on_session_end`** — when the session ends, the buffered turns are distilled into durable vault notes on a daemon thread (fail-safe: a capture error logs a warning and is dropped; it never crashes Hermes). The same importance gate and redaction pipeline used by agentcairn's normal capture applies.
- **`shutdown`** — waits up to 30 seconds for any in-flight capture thread to finish before the process exits.
- **Explicit tools** — `memory_save`, `memory_recall`, and `memory_search` are exposed as Hermes tools for curated, on-demand memory operations.

## Install

```bash
# 1. Install agentcairn into Hermes's Python environment
pip install agentcairn

# 2. Copy the plugin
cp -r integrations/hermes ~/.hermes/plugins/memory/agentcairn

# 3. Register it
hermes memory setup agentcairn
```

## Config

Configure via Hermes's plugin config mechanism. All keys are optional.

| Key | Default | Description |
|---|---|---|
| `vault_path` | `~/agentcairn` (or `$CAIRN_VAULT`) | Path to your Obsidian vault. Shared with Claude Code / Codex / Cursor if you use those. |
| `embedder` | `fastembed` | Embedding backend. `fastembed` (local, no key) or `ollama`. |
| `rerank` | `false` | Enable cross-encoder reranking on recall results for higher precision. |

**No secrets are required by default.** Storage is local. Session-end auto-capture uses agentcairn's **local extractive distiller** — fast, fully local, no API key required — with the same importance gate and redaction pipeline as normal agentcairn capture.

The optional LLM judge (`CAIRN_JUDGE=anthropic` + `ANTHROPIC_API_KEY`) applies to the normal `cairn sweep`/`ingest` CLI path over your shared vault, not to this plugin's in-process session-end capture.

## Verify

1. In a Hermes session, call `memory_save` with a fact (or just state a durable decision — it will be captured at session end).
2. Open `~/agentcairn` (or your configured `vault_path`) in Finder / Obsidian and confirm a new Markdown file appeared with YAML frontmatter and `[[wikilinks]]`.
3. Start a new Hermes session and confirm the memory is recalled automatically in the first turn.

To rebuild the index from the Markdown files alone (e.g., after moving the vault or upgrading agentcairn):

```bash
cairn reindex --vault ~/agentcairn
```

## Notes

- Capture is fail-safe: an error during `on_session_end` logs `[agentcairn] capture failed (dropped): ...` to stderr and is silently dropped. It never raises into Hermes.
- This plugin targets the Hermes MemoryProvider API as documented in 2026-06. The Hermes plugin API is still evolving; a version pin or update may be needed as it stabilizes.
