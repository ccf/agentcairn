# agentcairn → Hermes MemoryProvider Plugin — Design

**Status:** approved 2026-06-21
**Goal:** Ship agentcairn as a first-class **memory backend for Hermes Agent** (NousResearch/hermes-agent) via its `MemoryProvider` plugin system, so a Hermes user gets agentcairn's local-first, vault-native memory — automatic capture + auto-recall + curated saves — unified with their existing Claude Code / Codex / Cursor memories. Then request an upstream community-provider docs listing.

## Background

Hermes (open-source, NousResearch, Feb 2026) has a `MemoryProvider` plugin system (8 providers today: Mem0, Hindsight, Holographic, etc.). **None is vault-native, human-editable plain Markdown you own** — that is agentcairn's differentiated slot. The closest competitor, `rohitg00/agentmemory` (near-identical positioning), integrated via a `MemoryProvider` over its REST API (issue [#6715](https://github.com/NousResearch/hermes-agent/issues/6715)); it self-ships the adapter and its upstream docs-listing request is still unanswered. So the achievable win is **a working, self-installable plugin + an upstream listing request** — not a guaranteed merge.

**Caveat:** Hermes is ~4 months old and its plugin API may not be stable. We isolate the Hermes-contract surface in one file and pin/document the targeted API version.

## Hermes contract (researched)

Subclass `MemoryProvider` (`agent/memory_provider.py`). Required: `name` (property), `is_available()` (no network), `initialize(session_id, **kwargs)` (receives `hermes_home`), `get_tool_schemas()`, `handle_tool_call(tool_name, args, **kwargs)`, `get_config_schema()`, `save_config(values, hermes_home)`. Optional lifecycle hooks: `system_prompt_block()`, `prefetch(query, *, session_id="")` (before each API call → recalled context), `queue_prefetch(query)`, `sync_turn(user, assistant, *, session_id="")` (after a turn — **MUST be non-blocking**), `on_session_end(messages)` (final flush), `shutdown()`. Plugin layout: `plugins/memory/<name>/__init__.py` with `register(ctx) → ctx.register_memory_provider(...)`, plus `plugin.yaml`. Secrets (`secret: True` + `env_var`) → `.env`; all storage paths must use the `hermes_home` kwarg, not hardcoded `~/.hermes`.

## Architecture

A thin **in-process adapter** at `integrations/hermes/` in the agentcairn repo. Both Hermes and agentcairn are Python, so the plugin `pip install agentcairn` into Hermes's environment and imports agentcairn directly — no subprocess, REST, or spawned MCP server. The adapter maps the Hermes contract onto agentcairn's **existing** public functions; it adds no new memory logic.

agentcairn functions wrapped (all exist today):
- `cairn.mcp.tools.recall_tool(index_path, query, *, embedder, k, rerank, project, scope)` → hydrated top-k notes.
- `cairn.mcp.tools.search_tool(...)` → compact id+snippet index.
- `cairn.mcp.tools.remember_tool(vault_root, text, *, title, tags, subdir)` → redacts + writes a non-lossy memory note.
- `cairn.ingest.ingest_transcript(transcript, *, vault_root, ledger, distiller, subdir, dry_run)` → redact → distill → non-lossy notes.
- `cairn.ingest.DedupLedger`, `cairn.ingest`’s `Transcript` type, the reindex entry (`cairn.index` reindex / `cairn reindex`), `cairn.paths` for vault/index resolution, `cairn init` scaffolding.

## Components

### `integrations/hermes/__init__.py` — `CairnMemoryProvider`
- `name` → `"agentcairn"`.
- `is_available()` → `cairn` importable **and** a vault path resolvable from config/env; **no network, no model load**.
- `initialize(session_id, **kwargs)` → read `hermes_home`; resolve `vault_path` (config → `CAIRN_VAULT` → default `~/agentcairn`); `cairn init` the vault if absent (idempotent); resolve + stash the index path; open a `DedupLedger` stored under `hermes_home/agentcairn/`.
- `system_prompt_block()` → one short line: agentcairn memory active, vault path, "your memories are plain Markdown in this vault."
- `prefetch(query, *, session_id="")` → `recall_tool(index, query, embedder=cfg.embedder, k=cfg.k, rerank=cfg.rerank)`; format the hydrated notes into a compact context block string; return it. Read-only, synchronous (fast).
- `sync_turn(user, assistant, *, session_id="")` → append the turn to an in-memory buffer keyed by `session_id`. No write here (keeps turns cheap).
- `on_session_end(messages)` → spawn a **daemon thread** that: builds a `Transcript` from `messages` (direct construction from agentcairn's public `Transcript` type); calls `ingest_transcript(transcript, vault_root=vault, ledger=ledger, distiller=<configured>, subdir="memories")`; runs an **incremental reindex** so the next session's `prefetch` sees the new notes. Exceptions are caught and logged to stderr — never propagate (fail-safe; Hermes must not crash on capture failure).
- `get_tool_schemas()` → declare `memory_save`, `memory_recall`, `memory_search`.
- `handle_tool_call(tool_name, args, **kwargs)` → dispatch: `memory_save(text, title?, tags?)` → `remember_tool(vault, …)` + incremental reindex; `memory_recall(query, k?)` → `recall_tool(...)`; `memory_search(query, k?)` → `search_tool(...)`.
- `get_config_schema()` → fields: `vault_path` (non-secret; default shared `~/agentcairn`), `embedder` (default `fastembed`), `rerank` (bool, default per `CAIRN_RERANK`), `judge` (optional; default off → local extractive distill; `anthropic` is a secret field → `ANTHROPIC_API_KEY` to `.env`).
- `save_config(values, hermes_home)` → persist non-secret config under `hermes_home`.
- `shutdown()` → join/flush any pending capture thread (best-effort, bounded).

### `integrations/hermes/_contract.py` — Hermes surface isolation
The base-class import and any Hermes-API-specific shapes live here, imported lazily, so churn in the Hermes API touches one file. agentcairn does **not** depend on Hermes; this module degrades gracefully if the Hermes base isn't importable (for standalone testing).

### `integrations/hermes/plugin.yaml`
`name: agentcairn`, version, description, the targeted Hermes plugin-API version, and `hooks: [prefetch, sync_turn, on_session_end, system_prompt_block, shutdown]`.

### `integrations/hermes/README.md`
Install + config + the differentiator pitch (vault-native, own-your-data, cross-agent), and the verification demo (save in Hermes → see the Markdown note in `~/agentcairn` → recall in a new session).

### Optional core helper (only if needed)
If `Transcript` can't be cleanly constructed from a `messages` list using the public type, add one small builder `cairn.ingest.transcript_from_messages(messages, *, harness="hermes") -> Transcript` in agentcairn core. Prefer zero core change; this is the only sanctioned addition.

## Data flow

`hermes memory setup agentcairn` → `register(ctx)` → `ctx.register_memory_provider(CairnMemoryProvider())`. Per session: `initialize` (resolve shared vault) → each turn: `prefetch(query)` injects recalled notes; `sync_turn` buffers → `on_session_end` distills the buffered conversation into the shared vault + reindexes (daemon thread). Agent may call `memory_save`/`memory_recall`/`memory_search` anytime. Result: Hermes memories land in the **same** vault as the user's Claude Code/Cursor memories — one cross-agent brain.

## Error handling / fail-safe

- `is_available()` returns `False` (never raises) if agentcairn isn't importable or no vault is resolvable.
- All capture (session-end distill, `memory_save`) runs/handles errors so a failure logs to stderr and is dropped — Hermes continues. Mirrors agentcairn's "a wrong call never crashes / never drops a distinct memory" philosophy.
- **Redaction (`cairn.ingest.redact`) is always applied before any write** — inherited by going through `remember_tool` / `ingest_transcript`.
- `prefetch` failures return an empty context block (no crash).
- Daemon-thread captures are bounded; `shutdown()` flushes best-effort.

## Testing

agentcairn's suite stays **Hermes-independent**: tests provide a local stub of the `MemoryProvider` base and a fake `ctx`. Tests (pytest, against a `tmp_path` vault):
- `is_available()` true with a vault, false without; no network.
- `prefetch(query)` returns a context block containing a previously-written note; empty-vault → empty block, no error.
- `on_session_end(messages)` (driven synchronously in tests via a hook to run the capture inline) writes ≥1 distilled note to the vault and a subsequent `recall_tool` finds it; redaction applied (a planted secret is scrubbed).
- `handle_tool_call("memory_save", …)` writes a note; `memory_recall`/`memory_search` return it after reindex.
- messages→`Transcript` mapping (or `transcript_from_messages`) shape.
- `register(ctx)` calls `ctx.register_memory_provider` once with a `CairnMemoryProvider`.
- Capture failure path: a forced distill error is swallowed (no exception escapes `on_session_end`).

## Out of scope (follow-ups)

- The upstream Hermes docs-listing issue (an action, not code; filed after the plugin works).
- A PyPI/standalone package for the plugin (ships in-repo under `integrations/hermes/` for v1).
- Context-engine plugin (the other Hermes provider type) — memory only.
- Multi-vault / per-session vault routing (shared vault + config override only).

## Definition of done

- `integrations/hermes/` contains a working `CairnMemoryProvider` (+ `plugin.yaml`, `README.md`, `_contract.py`) that registers in Hermes and, against a shared `CAIRN_VAULT`, does auto-recall (`prefetch`), auto-capture (`on_session_end` distill + reindex, daemon-thread, fail-safe), and the 3 curated tools.
- Redaction enforced on every write; capture never crashes Hermes.
- agentcairn core unchanged except (at most) `transcript_from_messages`.
- Tests pass Hermes-independently (stubbed base) against a temp vault; `npm`-free, runs under the existing pytest suite.
- README documents install + the save→see-Markdown→recall verification demo.
