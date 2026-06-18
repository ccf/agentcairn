# Plugin-side vault-scoped completion — design

**Date:** 2026-06-18
**Status:** Approved (brainstorm) → ready for implementation plan

## Problem

0.18 made the DuckDB index a pure function of the vault and added `migrate_legacy_index`
(rehomes the old global `~/.cache/agentcairn/index.duckdb` → `indexes/<vault_key>.duckdb`). The
fix covered the CLI and `cairn install`'s generated MCP config — but **missed the bundled plugin
manifests**. They still pin `CAIRN_INDEX` to the old global path, and the plugin version was never
bumped, so the corrected `--vault`-only hook scripts (already in the repo since 0.18) were never
delivered to installed users.

A 2026-06-18 dogfood confirmed the live impact: the Claude Code plugin's MCP server is launched
with `CAIRN_INDEX=~/.cache/agentcairn/index.duckdb` (via `${user_config.index_path}`), a path the
0.18 rehome moved away — so **`recall`/`search`/`build_context` error with `no index`** in real
sessions. The core feature is down for plugin users.

## Goal

Complete the migration on the bundled manifests so the MCP server and hooks derive the index purely
from `CAIRN_VAULT` (→ the vault-scoped index). After a plugin update, recall works again. Remove
`index_path`/`CAIRN_INDEX` from the plugin surface entirely (decided: the env-level `CAIRN_INDEX`
override still exists for genuine power users; the plugin no longer offers a pinnable path that the
rehome can orphan).

Non-goals: any `src/cairn` change (the server/CLI already resolve from the vault); a settings.json
migrator (the user's stale `index_path` value goes unread once the manifest stops referencing it);
the orphan scratch-index cleanup (handled during re-dogfood, separately).

## Design

### Manifest changes (all three harnesses)

- **`plugin/.mcp.json`** (Claude Code): remove the `"CAIRN_INDEX": "${user_config.index_path}"`
  line from `env`; keep `"CAIRN_VAULT": "${user_config.vault_path}"`.
- **`plugin/.mcp.codex.json`** (Codex, bare map): remove the hardcoded `"CAIRN_INDEX": "~/.cache/agentcairn/index.duckdb"`.
- **`plugin/mcp_config.json`** (Antigravity, wrapper form): remove the hardcoded `CAIRN_INDEX`.
- **`plugin/hooks/hooks.json`**: drop the `"${user_config.index_path}"` element from both the
  SessionStart and SessionEnd `args` arrays. The scripts already read only `$1` (vault) since 0.18,
  so passing only `["${CLAUDE_PLUGIN_ROOT}/scripts/<script>", "${user_config.vault_path}"]` is correct.
- **`plugin/.claude-plugin/plugin.json`**: delete the `index_path` userConfig field; **bump
  `version` from `0.1.0` to `0.2.0`** so `claude plugin update` delivers the new manifest + scripts.
- **`plugin/commands/memory.md` / `ingest.md`**: remove the `CAIRN_INDEX` wording (index derives
  from the vault; `cairn doctor`/`index-status`/`sweep` take `--vault`).

The MCP server is `uvx agentcairn` (resolves to ≥0.20, which already derives the index from
`CAIRN_VAULT` when `CAIRN_INDEX` is unset), so removing the pin is sufficient — no server change.

### Release mechanics

Plugin-manifest-only change (no Python code touched). Bump `plugin.json` to `0.2.0`, merge to main,
and cut a cairn **0.20.1** release (CHANGELOG + tag) so the tag/marketplace ref and changelog stay
consistent; the PyPI wheel carries the manifest fix with otherwise-unchanged code. The user then
runs `claude plugin update agentcairn` (and the Codex/Antigravity equivalents) and the MCP server
re-resolves the index from `CAIRN_VAULT`.

### Why no migrator

When the updated manifest no longer references `${user_config.index_path}`, the value still present
in the user's `settings.json` userConfig is simply never read — inert, not harmful. The 0.18
`migrate_stale_cairn_index` already covers the standalone MCP-host configs (cursor/vscode/codex);
the Claude Code plugin path needs no equivalent because the pin lives in the plugin-owned manifest,
not the user's config.

## Testing

Assertions in `plugin/tests/test_plugin.py` (run by CI's `validate` job, outside the repo-root
`testpaths`):

- Each MCP manifest (`.mcp.json`, `.mcp.codex.json`, `mcp_config.json`) has `CAIRN_VAULT` set and
  **no `CAIRN_INDEX`** key anywhere in its env.
- `hooks.json`: both `args` arrays contain `${user_config.vault_path}` and **not**
  `${user_config.index_path}`.
- `plugin.json`: `userConfig` has **no `index_path`** key, and `version != "0.1.0"`.
- The existing session-start/session-end script-execution tests still pass unchanged (scripts read
  only `$1`).

Full verify: `uv run pytest -q`, `uv run ruff check/format --check src tests`, and explicitly
`uv run --no-project --with pytest pytest plugin/tests/ -q`.

## Rollout & re-dogfood

Single plugin-fix release (0.20.1). After merge, the user updates the plugin, then re-dogfoods:
confirm the live `recall` MCP tool returns real notes (not `no index`), `cairn doctor` is OK, and
`cairn link` populates the Obsidian graph. The orphan `e717…duckdb` scratch index (test leakage,
correctly isolated by vault-scoping) is deleted during that pass.
