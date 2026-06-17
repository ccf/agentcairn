# Vault-scoped index — design

**Date:** 2026-06-17
**Status:** Approved (brainstorm) → ready for implementation plan

## Problem

The DuckDB index is meant to be a rebuildable cache *of a specific vault*, but its
location is not derived from the vault. `cairn sweep`/`reindex`/`recall` default the
index to a **single global path** (`~/.cache/agentcairn/index.duckdb`, via
`_default_index()` / `CAIRN_INDEX`), independent of `--vault`.

This is a footgun. During the 2026-06-17 dogfood audit the production index had silently
drifted off the real vault: it held 493 notes (vault had 559) and every stored
`notes.path` pointed at a nonexistent `/var/folders/.../tmp.*/scratch-vault/...` path. Root
cause: a dogfood run (our own docs say "sweep … into a scratch vault") executed
`cairn sweep --vault /tmp/scratch-vault` from a shell with `CAIRN_INDEX` unset, so a
throwaway vault's notes — and dead temp paths — were written straight into the global
production index. `recall` text still worked (text lives in the index) but `build_context`
and path reads were broken, and recall served a frozen snapshot while `doctor` reported
`status: OK`. A manual `cairn reindex` reported 246 added / 314 updated / 179 removed.

The dedup **ledger** already avoids this class of bug — it is vault-scoped
(`ledgers/<vault_key>.sha256`). The index should follow the same principle.

## Goal

Make the index a **pure function of the vault**: by default, a given vault always resolves
to its own index, and no other vault (scratch, test, or otherwise) can write into it. Keep
an explicit `--index` / `CAIRN_INDEX` override as a deliberate escape hatch (tests, separate
index locations). Make drift loud instead of silent.

Non-goals: changing the index schema or embedding model; a mismatch-guard on the escape
hatch (obviated by vault-scoping — deferred); reworking the ledger beyond moving its keying
into the shared helper.

## Design

### 1. Shared path resolver — `src/cairn/paths.py`

A single home for vault-derived paths. Today `vault_key` is computed inline twice (in
`sweep` and `ingest`); this consolidates it and adds the index derivation.

```
cache_root() -> Path                         # ~/.cache/agentcairn
resolve_vault(explicit: Path | None, env) -> Path
    # --vault → CAIRN_VAULT → default ~/agentcairn  (matches the `vault` knob default)
vault_key(vault: Path) -> str                # sha256(str(vault.resolve()))[:16]  (unchanged)
resolve_index(explicit: Path | None, vault: Path, env) -> Path
    # --index → CAIRN_INDEX → cache_root()/indexes/<vault_key>.duckdb
default_ledger(vault: Path) -> Path          # cache_root()/ledgers/<vault_key>.sha256
judged_cache(vault: Path) -> Path            # cache_root()/ledgers/<vault_key>.judged.jsonl
```

Precedence everywhere mirrors the existing config layering: explicit arg → env → derived
default. `resolve_index` is the only place that knows the `indexes/<key>.duckdb` layout.

### 2. Command surface

| Command | Today | Change |
|---|---|---|
| `sweep`, `ingest`, `reindex` | take a vault; `idx = index or _default_index()` | `idx = resolve_index(index, vault, env)`; use `default_ledger`/`judged_cache` |
| `recall`, `search`, `recent`, `doctor`, `index-status` | take only `--index` | add `--vault` (default → `CAIRN_VAULT` → `~/agentcairn`); derive index via `resolve_index`; `--index` still wins |
| MCP server (`mcp/server.py`) | `index or CAIRN_INDEX or _DEFAULT_INDEX` | `resolve_index(index, resolved_vault, env)` — it already resolves `CAIRN_VAULT` for project-boost |

The escape hatch (`--index` / `CAIRN_INDEX`) keeps highest precedence in every command, so
tests and advanced users are unaffected.

### 3. Migration (so installed users benefit, not just fresh installs)

1. **`cairn install` stops writing `CAIRN_INDEX`.** It already writes `CAIRN_VAULT`, which
   is now sufficient. (`hosts/entry.py` `mcp_entry` drops `CAIRN_INDEX` from the env map.)
2. **Hook scripts** (`plugin/.../scripts/session-start.sh`, `session-end.sh`) drop the index
   argument and call `cairn … --vault "$VAULT"` so `cairn` derives the index. The plugin
   `user_config.index_path` (and the `hooks.json` `${user_config.index_path}` arg) becomes
   optional/unused. Scripts must NOT compute `vault_key` themselves (sha256 in `sh` is
   awkward) — deriving in `cairn` is the single source of truth.
3. **Config migrator** strips a stale `CAIRN_INDEX` from existing host configs (JSON env for
   cursor/vscode/gemini; TOML for codex), reusing the `migrate_*` pattern already used for
   stale MCP blocks. Runs only after a successful install (same guard as the existing
   migrators).
4. **Legacy global index auto-rehome.** On first run where `CAIRN_INDEX` is unset and the
   legacy `~/.cache/agentcairn/index.duckdb` exists: open it read-only, read a sample
   `notes.path`, infer the vault root as the prefix before `/memories/`, compute that vault's
   key, and **move** the file to `indexes/<key>.duckdb` if that slot is empty. This re-homes
   the index correctly regardless of which vault it belonged to. If inference fails (no
   notes, unparseable path, slot occupied), leave the legacy file in place — a missing
   derived index simply rebuilds on the next `sweep`/`reindex` (the index is rebuildable by
   definition). This is a one-time best-effort step, never fatal.

### 4. `doctor` drift check

`doctor` is now vault-aware (knows both the vault and the derived index), so it can compare
the two:

- `indexed_missing` — count of `notes.path` rows whose file does not exist on disk (dead
  paths / wrong-vault index).
- `disk_unindexed` — count of `<vault>/memories/*.md` whose permalink is absent from the
  index.

If either is > 0, status becomes a loud **`DRIFT`** line with both counts and the remedy
(`cairn reindex <vault>`), instead of `OK`. `index-status` keeps its terse output; the drift
check lives in `doctor`. This is precisely the signal that was missing when the bug went
unnoticed.

## Error handling

- Path resolution never raises on a missing vault/index — resolution returns a path; the
  command decides (e.g. `doctor` on a missing index already reports "no index", exit 1).
- Legacy auto-rehome is wrapped best-effort: any failure (read error, parse failure,
  occupied slot) falls back to lazy rebuild and is non-fatal.
- The config migrator follows the existing post-successful-install guard so a failed install
  never strips a still-needed `CAIRN_INDEX`.

## Testing

- **Unit (`paths.py`):** resolution precedence (explicit > env > derived) for vault and
  index; `vault_key` stable for the same resolved path and distinct across vaults; derived
  default lands at `indexes/<key>.duckdb`.
- **Command:** bare `sweep --vault V` and bare `recall --vault V` (no `--index`, no env)
  resolve the *same* `indexes/<key>.duckdb`; an explicit `--index` overrides both; `CAIRN_VAULT`
  drives recall when `--vault` is omitted.
- **Migration:** `cairn install cursor` writes `CAIRN_VAULT` but **not** `CAIRN_INDEX`; the
  migrator strips a stale `CAIRN_INDEX` from cursor (JSON) and codex (TOML) fixtures; legacy
  auto-rehome — synthesize an index whose note paths are under `…/vaultX/memories/…`, assert
  it is moved to `indexes/<key(vaultX)>.duckdb`; inference-failure case leaves the legacy file
  untouched.
- **Doctor:** an index with a dead path yields `DRIFT` with `indexed_missing>0`; a vault with
  an unindexed note yields `DRIFT` with `disk_unindexed>0`; a matching pair yields `OK`.
- **Hook scripts:** `session-end.sh`/`session-start.sh` invoke `cairn` with `--vault` and no
  `--index` (assert via the rendered command / a thin smoke test).

## Rollout

Single release. Behavioral change is backward-compatible for anyone pinning `--index`/
`CAIRN_INDEX`; installed users are migrated on their next `cairn install` and, for the index
file itself, on first run via auto-rehome. CHANGELOG note should call out that the default
index moved to `indexes/<vault_key>.duckdb` and that `CAIRN_INDEX` remains as an override.
