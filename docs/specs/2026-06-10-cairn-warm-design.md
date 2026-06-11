# `cairn warm` (plugin model pre-warm) — Design

**Status:** Approved (brainstorm) — 2026-06-10
**Scope:** One small, contained feature. One implementation plan.

## Goal

Eliminate the plugin's first-use slowness: the first SessionEnd `cairn sweep` and first MCP `recall` block on **downloading models on first use** — the FastEmbed embedder (`nomic-embed-text-v1.5`, hundreds of MB) and the cross-encoder reranker (`Xenova/ms-marco-MiniLM-L-6-v2`, ~80 MB). Add a `cairn warm` command that pre-downloads the *configured* models, and call it from the SessionStart hook's existing detached first-run job so the first real sweep/recall is fast.

## Context & principle

agentcairn is local-first and **daemonless**; the plugin's SessionStart hook already runs a fully-detached background job on first run (no index) that does `cairn init` + warms the `uvx` cache. The governing principle: **never block or slow the session.** Pre-warming therefore happens in that same detached job — model downloads run in the background; the user's session is never delayed.

Model-load mechanics (verified):
- **Embedder:** `get_embedder("fastembed")` downloads the model on construction (the constructor probes for `dim`). `ollama`/`fake`/`none` download nothing locally.
- **Reranker:** a lazy singleton; downloads on the first `rerank_candidates(...)` call. On by default; `CAIRN_RERANK=0` disables it.

## Decision

A dedicated **`cairn warm` CLI command** (vs. plugin-only command-chaining). Rationale: a clean, reusable, *config-aware* primitive — it also helps CLI users (`cairn warm` before first use) and is the natural per-host pre-flight step for the future multi-agent work (#36). Cost: a package change → a `0.4.0` release; the hook's `agentcairn>=0.2` pin degrades gracefully until then.

## `cairn warm` command

In `src/cairn/cli.py`:

- Resolve the configured embedder name: `os.environ.get("CAIRN_EMBEDDER") or "fastembed"` (matches the MCP server's resolution).
- **Warm the embedder** when it's a real local/remote model: construct `get_embedder(name)` for `fastembed` (downloads nomic) or `ollama` (probes the server). Skip `fake`/`none` (nothing to warm). Wrapped best-effort — report success/failure, never raise.
- **Warm the reranker** when reranking is enabled (`resolve_rerank()` default; `CAIRN_RERANK=0` skips): call `rerank_candidates("warm", [{"text": "hello"}])` to force the cross-encoder download. Wrapped best-effort, independent of the embedder step.
- Print a one-line status for each step (`embedder ready: <name>` / `reranker ready` / `skipped`). Exit 0 even if a step fails (it's a best-effort pre-fetch). No flags — it warms exactly what the configured env will use.

It is **idempotent**: once a model is cached (by any cairn invocation), `warm` returns near-instantly.

## Plugin wiring

In `plugin/scripts/session-start.sh`, the first-run detached job changes from:

```sh
( $CAIRN init "$VAULT" ) </dev/null >/dev/null 2>&1 &
```

to:

```sh
( $CAIRN init "$VAULT"; $CAIRN warm ) </dev/null >/dev/null 2>&1 &
```

Still fully detached (stdin/stdout/stderr to `/dev/null`) and best-effort, so it never blocks or delays the session. On `<0.4.0`, `cairn warm` errors into `/dev/null` and the rest of the plugin is unaffected — the pre-warm simply activates once `0.4.0` is published.

## Release

`cairn warm` is new → cut **`0.4.0`** after merge (bump `__version__`, promote CHANGELOG `[Unreleased]` → `[0.4.0]`, tag `v0.4.0`, GitHub Release, PyPI auto-publishes). The plugin pre-warm is inert until `0.4.0` is live.

## Testing

- `cairn warm` with `CAIRN_EMBEDDER=fake` + `CAIRN_RERANK=0` → fast no-op, exit 0, reports both skipped/ready without any network.
- **Best-effort:** monkeypatch `get_embedder` to raise → `warm` still exits 0 and reports the embedder failure (doesn't crash, doesn't block the reranker step).
- `CAIRN_RERANK=0` → the reranker step is skipped (assert via a spied/monkeypatched `rerank_candidates` that it is NOT called).
- Reranker warm path: with rerank enabled, `rerank_candidates` is invoked once (spied; no real model — monkeypatched to a no-op to stay offline).
- Plugin (offline): `session-start.sh`'s first-run detached job includes the `warm` step (assert the script text), and the existing first-run early-exit still exits 0 instantly.
- Real model downloads are **not** tested (network/slow); the tests use `fake`/monkeypatching to stay offline.

## Out of scope (YAGNI)

- Flags on `cairn warm` (`--embedder`, etc.) — it reads the configured env.
- A progress bar / download size reporting.
- Warming on every SessionStart (only the first-run detached job warms; subsequent sessions already have cached models).
- Pre-warming in the MCP server itself or via a separate background daemon (anti-daemonless).
