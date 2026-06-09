# Reranker on by default — Design Spec (v1.1)

**Date:** 2026-06-09
**Status:** approved (design); implementation to follow.

## 1. Goal & motivation

Make the cross-encoder reranker **on by default**, exposed as a first-class env/CLI knob to turn off. Motivated by the v1 benchmark: on the full LoCoMo retrieval ablation the reranker is the **largest measured lever** (`hybrid+reranker` recall@5 0.660 vs `hybrid` 0.546, +0.11; MRR 0.608 vs 0.462), lifting every category. The original "ms-marco domain-shift might hurt on code/markdown" caution is mild for agentcairn, whose content is distilled-conversation prose (close to the validated LoCoMo data), not code — and the knob lets anyone disable it.

First v1.1 sub-project. Reuses the off-by-default reranker built in v1 (`src/cairn/search/rerank.py`); changes only defaults + configurability + docs.

## 2. Decisions (from brainstorming)

- **Default:** reranker **ON** everywhere (CLI `recall`, MCP `search`/`recall`), with a first-class off-switch.
- **Config mechanism:** **env + CLI only** (no config file — deferred). Add `CAIRN_RERANK` env (mirrors `CAIRN_EMBEDDER`) + a CLI `--rerank/--no-rerank` flag.
- **Precedence:** explicit CLI flag → `CAIRN_RERANK` env → default `True`.
- **Engine stays neutral:** `cairn.search.engine.search(rerank=False)` is unchanged — a neutral library primitive. The default-*on* policy lives at the application/config layer, so nothing internal (or the benchmark, which passes `rerank=` explicitly per arm) silently changes behavior.

## 3. Architecture

New tiny module **`src/cairn/config.py`** — the shared home for env-resolution helpers (the embedder-tier knob will join it later):
- `parse_bool(value: str) -> bool` — `"1"/"true"/"yes"/"on"` (case-insensitive) → True; `"0"/"false"/"no"/"off"` → False.
- `resolve_rerank(explicit: bool | None = None, env: Mapping[str, str] = os.environ) -> bool` — returns `explicit` when not None; else if `CAIRN_RERANK` is set, `parse_bool` it; else `True`. Unparseable `CAIRN_RERANK` → default `True` (don't crash a query on a typo'd env).

Wiring:
- **CLI `recall`** (`src/cairn/cli.py`): change `--rerank` (currently `bool = Option(False)`) to a tri-state `--rerank/--no-rerank` flag defaulting to `None` (`rerank: bool | None = Option(None, "--rerank/--no-rerank", ...)`); compute `resolve_rerank(rerank)`; pass to `search(..., rerank=resolved)`.
- **MCP** (`src/cairn/mcp/server.py`): `build_server` resolves the default once — `rerank_default = resolve_rerank(None, os.environ)` — and the `search`/`recall` `@mcp.tool()` wrappers default their `rerank` parameter to `rerank_default` (the agent can still pass `rerank=` explicitly to override per call). The wrappers pass the value through to `tools.search_tool`/`recall_tool` as today.
- **`tools.search_tool`/`recall_tool`** (`src/cairn/mcp/tools.py`): keep their `rerank: bool` parameter; the server supplies the resolved value. (Their literal default is irrelevant since the server passes it explicitly; leave them as `rerank: bool = False` to stay neutral, or accept whatever the server passes.)
- **`cairn.search.engine`**: unchanged.

## 4. Docs / honesty updates (part of the feature)

- `src/cairn/search/rerank.py` module docstring: replace the "OFF by default … enable only after validating" framing with: *on by default; validated on conversational data (LoCoMo, +0.11 recall@5); disable with `CAIRN_RERANK=0` if it underperforms on your corpus. The ms-marco cross-encoder is tuned for short passages — code-heavy vaults are unvalidated.*
- CLI `recall` `--rerank/--no-rerank` help text; MCP `search`/`recall` tool docstrings note the env knob.
- `README.md` roadmap: move "reranker on by default" from v1.1-next to done (when merged).

## 5. Testing

- **`tests/test_config.py`** (new): `parse_bool` truth table; `resolve_rerank` — explicit True/False wins over env; `CAIRN_RERANK` `1/true/yes/on` → True, `0/false/no/off` → False; unset → True; junk (`"maybe"`) → True (default, no raise).
- **CLI** (`tests/test_cli.py`): `recall` with `--no-rerank` calls `search` with `rerank=False`; `CAIRN_RERANK=0` (monkeypatched env) with no flag → `rerank=False`; default (no flag, no env) → `rerank=True`. Spy on `cairn.cli.search` to capture the `rerank` kwarg (build a tiny fake index or monkeypatch `open_search`+`search`).
- **MCP** (`tests/mcp/test_server.py`): `build_server` with `CAIRN_RERANK` unset → the registered `search`/`recall` tools' default rerank is True; with `monkeypatch.setenv("CAIRN_RERANK","0")` → default False. (Assert via `resolve_rerank` and/or by introspecting the tool default; keep it deterministic, no model.)
- Keep the existing model-gated reranker integration test untouched.
- All offline; no model download in the default test run (the cross-encoder is only constructed when a real rerank actually executes, which the unit tests avoid by spying/monkeypatching).

## 6. Non-goals (deferred)

- No config **file** (`.cairn/config.toml`) — env + CLI only this round; the config-file layer is a later v1.1 step once multiple knobs exist.
- No reranker-**model** switching — keep `Xenova/ms-marco-MiniLM-L-6-v2`.
- No markdown/code-specific reranker benchmark — separate effort; we ship the honest "validated on conversational" caveat and the off-switch.
- No change to `search()`'s engine default or to the benchmark.

## 7. Risks

- **Latency / first-query model download:** default-on means the first CLI `recall` or MCP query triggers the one-time ~90 MB cross-encoder download + load (lazy singleton, then warm). Acceptable for interactive use; the off-switch covers latency/cost-sensitive deployments. Documented.
- **Unvalidated on code-heavy vaults:** mitigated by the honest docstring + `CAIRN_RERANK=0`.
