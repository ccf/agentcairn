# User config file (`~/.agentcairn/config.toml`) — Design

**Status:** Approved (brainstorm) — 2026-06-12
**Scope:** Tame env-var sprawl (~11 knobs) with a single TOML config file as a lower-precedence layer under env vars. Fully backward-compatible.

## Problem

agentcairn now has ~11 tuning knobs (`CAIRN_VAULT`, `CAIRN_INDEX`, `CAIRN_EMBEDDER`, `CAIRN_EMBED_MODEL`, `CAIRN_RERANK`, `CAIRN_USAGE`, `CAIRN_USAGE_PATH`, `CAIRN_JUDGE`, `CAIRN_JUDGE_MODEL`, `CAIRN_JUDGE_TIMEOUT`, plus `OLLAMA_HOST` and `ANTHROPIC_API_KEY`). Managing these through shell exports is poor UX, and worse: the plugin's **detached** SessionEnd sweep only sees whatever environment the hook process inherited — turning on the Layer-B LLM judge currently requires getting exports into that inheritance chain. Users need one obvious, durable place to set things.

## Decisions (locked in brainstorm)

1. **Location:** `~/.agentcairn/config.toml` (the vault stays visible at `~/agentcairn`; `~/.agentcairn` is the hidden config home). `CAIRN_CONFIG` env var overrides the path (tests, exotic setups). No XDG fallback (YAGNI).
2. **Key scheme: mechanical mapping, no hand-maintained schema.** Flat lowercase keys map by rule to env-var names: `judge` → `CAIRN_JUDGE`, `judge_model` → `CAIRN_JUDGE_MODEL`, `vault` → `CAIRN_VAULT`, … plus a small passthrough set for non-`CAIRN_*` vars: `anthropic_api_key` → `ANTHROPIC_API_KEY`, `ollama_host` → `OLLAMA_HOST`. Every future `CAIRN_*` knob is file-configurable automatically — the file schema can never drift from the env surface. (Pretty sectioned schema rejected: per-knob mapping maintenance is exactly the drift class we keep hunting.)
3. **Precedence:** explicit CLI arg > env var > **config file** > built-in default. Env over file keeps the per-host `CAIRN_VAULT`/`CAIRN_INDEX` baked into MCP entries by `cairn install` working unchanged.
4. **One new CLI surface:** `cairn config` (inspect) and `cairn config --init` (scaffold). No `get`/`set` mutations — users edit the TOML.

## Architecture

### The merge seam (`src/cairn/config.py`)

```python
def cairn_env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    """The unified settings mapping: config-file values (translated to env-var
    names) overlaid by the real environment. Single seam for every knob read."""
```

- Loads `CAIRN_CONFIG` or `~/.agentcairn/config.toml` with stdlib `tomllib`; missing file → `{}`. Cached per process (`functools.lru_cache`-style with an explicit `_reset()` for tests).
- Key translation: `name` → `CAIRN_<NAME>` uppercased, except the passthrough set `{"anthropic_api_key": "ANTHROPIC_API_KEY", "ollama_host": "OLLAMA_HOST"}`.
- **Type coercion:** non-string TOML values are stringified to match env semantics — `rerank = false` → `"false"` (same as `CAIRN_RERANK=false`), `judge_timeout = 10` → `"10"`. Booleans stringify lowercase.
- **Unknown keys** (translate to an env name no resolver reads): warn once to stderr (typo protection: `judg_model` must not fail silently), never error.
- A malformed TOML file: one stderr warning, treated as empty (config must never break ingestion/recall).

### Call-site migration

All direct `os.environ` reads for knobs switch to `cairn_env()`:
`cli.py` (`_default_index`, vault default, embedder default, the judge-unavailable warning, the dry-run judge env), `usage.py` (`CAIRN_USAGE`, `CAIRN_USAGE_PATH`), `mcp/server.py` (vault/index/embedder/rerank), `ingest/judge.py` (`resolve_judge`'s default env). The existing `config.py` resolvers (`fastembed_model`, `ollama_config`, `resolve_rerank`, `judge_config`) keep their `env: Mapping` parameter but default it to `cairn_env()` instead of `os.environ`. After this, **any knob read through the seam is file-configurable with zero extra work**.

### `cairn config` command (`cli.py`)

- **`cairn config`** — table of every known knob: effective value, and source (`env` / `file` / `default`). Secrets masked (`ANTHROPIC_API_KEY` shows `sk-ant-…last4`). Answers "why isn't my judge on?" in one command.
- **`cairn config --init`** — writes a fully-commented template: every knob present but commented out, with its default and a one-line description; file mode **0600** (the API key may live here); parent dir created; **refuses to overwrite** an existing file (prints its path instead).
- The known-knob list lives in one registry table in `config.py` (name, env var, default, description, secret flag) used by both the template and the inspection output — one place to add a future knob's docs.

## What this fixes operationally

The plugin's detached sweep invokes `cairn`, which reads the file itself — so `CAIRN_JUDGE=anthropic` + the API key **no longer need to be in any shell profile or hook environment**. Enabling the LLM judge becomes: `cairn config --init`, uncomment `judge = "anthropic"` and `anthropic_api_key = "…"`, done — survives shells, launchd, cron, and every MCP host.

## Error handling

- Missing file → empty (silent). Malformed file → warn once, empty. Unknown key → warn once. Unreadable file (perms) → warn once, empty.
- `cairn config --init` with existing file → message + exit 0 (not an error; idempotent onboarding).

## Testing (offline)

- Precedence matrix: file-only, env-only, both (env wins), neither (default) — for a representative knob of each type (path, string, bool, float, secret passthrough).
- Type coercion: TOML bool/int/float → env-string semantics (`rerank = false` disables exactly like `CAIRN_RERANK=false`).
- Unknown key warns once; malformed TOML degrades to empty with warning; `CAIRN_CONFIG` path override honored.
- `cairn config` output: sources labeled correctly; secret masked; `--init` writes 0600, all knobs commented, refuses overwrite.
- End-to-end: a config file with `judge = "none"` changes `cairn ingest` judge tier without any env var.
- Cache reset between tests (`_reset()`); no test reads the real `~/.agentcairn`.

## Docs

- README: the install/usage sections lead with the two-command story (`cairn config --init`, edit); the env-var documentation remains as the override layer ("any setting can also be set as an env var; env wins").
- Plugin README/config docs: enabling the LLM judge via the file.

## Rollout

**0.9.0** — new feature, fully backward-compatible: env-only users see zero behavior change (empty/missing file is a no-op layer).

## Out of scope (YAGNI / later)

- XDG `~/.config/agentcairn/` fallback.
- Per-project / per-vault config files.
- `cairn config set/get` mutations.
- Secret encryption / keychain integration.
- Writing the config file from `cairn install` or the plugin.
