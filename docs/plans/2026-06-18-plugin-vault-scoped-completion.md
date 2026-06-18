# Plugin-side Vault-Scoped Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the stale `CAIRN_INDEX`/`index_path` pins from the bundled plugin manifests (all three harnesses) and bump the plugin version, so the MCP server + hooks derive the index from `CAIRN_VAULT` and a plugin update restores recall.

**Architecture:** Pure manifest/doc edits — no `src/cairn` change. The `uvx agentcairn` MCP server (≥0.20) already derives the index from `CAIRN_VAULT` when `CAIRN_INDEX` is unset; the hook scripts already read only `$1` (vault) since 0.18. We delete every `CAIRN_INDEX`/`index_path` reference and bump `plugin.json` version so the fix ships.

**Tech Stack:** JSON manifests, Markdown command docs, pytest (`plugin/tests/test_plugin.py`, run by CI's `validate` job). Spec: `docs/specs/2026-06-18-plugin-vault-scoped-completion-design.md`.

**How to run the plugin test suite** (it is OUTSIDE the repo-root `testpaths`): `uv run --no-project --with pytest pytest plugin/tests/test_plugin.py -q`.

---

## File Structure

- **Modify** `plugin/.mcp.json`, `plugin/.mcp.codex.json`, `plugin/mcp_config.json` — drop `CAIRN_INDEX` from each `env`.
- **Modify** `plugin/hooks/hooks.json` — drop the `${user_config.index_path}` arg from both hook `args`.
- **Modify** `plugin/.claude-plugin/plugin.json` — remove the `index_path` userConfig field; bump `version` `0.1.0` → `0.2.0`.
- **Modify** `plugin/commands/memory.md`, `plugin/commands/ingest.md` — drop `CAIRN_INDEX` wording.
- **Modify** `plugin/tests/test_plugin.py` — assertions for all of the above.
- **Modify** `CHANGELOG.md`.

---

## Task 1: Remove `CAIRN_INDEX` from all three MCP manifests

**Files:**
- Modify: `plugin/.mcp.json`, `plugin/.mcp.codex.json`, `plugin/mcp_config.json`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing tests** — add to `plugin/tests/test_plugin.py` (reuses the existing `_json` helper and `PLUGIN` path):

```python
def test_mcp_manifests_have_no_cairn_index():
    """The index is vault-derived; no plugin MCP manifest may pin CAIRN_INDEX."""
    for rel in (".mcp.json", ".mcp.codex.json", "mcp_config.json"):
        data = _json(PLUGIN / rel)
        blob = json.dumps(data)
        assert "CAIRN_INDEX" not in blob, f"{rel} still pins CAIRN_INDEX"
        assert "CAIRN_VAULT" in blob, f"{rel} must still set CAIRN_VAULT"
```

(`json` is already imported at the top of the file; confirm with `grep -n '^import json' plugin/tests/test_plugin.py`.)

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run --no-project --with pytest pytest plugin/tests/test_plugin.py -q -k cairn_index`
Expected: FAIL (each manifest still contains `CAIRN_INDEX`).

- [ ] **Step 3: Edit the three manifests** — in each, delete the `CAIRN_INDEX` entry from the `env` object, keeping `CAIRN_VAULT`. Read each file first; the result must be valid JSON (watch the trailing comma after removing the last env key).

`plugin/.mcp.json` becomes:
```json
{
  "mcpServers": {
    "agentcairn": {
      "command": "uvx",
      "args": ["agentcairn"],
      "env": {
        "CAIRN_VAULT": "${user_config.vault_path}"
      }
    }
  }
}
```

`plugin/.mcp.codex.json` — remove its `"CAIRN_INDEX": "~/.cache/agentcairn/index.duckdb"` line (it's the Codex bare-map form; keep the `CAIRN_VAULT` line and fix the comma so the `env` object stays valid JSON).

`plugin/mcp_config.json` — remove its `"CAIRN_INDEX": "~/.cache/agentcairn/index.duckdb"` line (Antigravity wrapper form; keep `CAIRN_VAULT`, fix the comma).

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run --no-project --with pytest pytest plugin/tests/test_plugin.py -q`
Expected: PASS (new test green; the existing `test_mcp_config_wires_uvx_agentcairn` still passes — it only asserts CAIRN_VAULT).

- [ ] **Step 5: Commit**

```bash
git add plugin/.mcp.json plugin/.mcp.codex.json plugin/mcp_config.json plugin/tests/test_plugin.py
git commit -m "fix(plugin): drop stale CAIRN_INDEX pin from all MCP manifests"
```

---

## Task 2: Drop `index_path` from hooks args + plugin.json userConfig; bump version

**Files:**
- Modify: `plugin/hooks/hooks.json`, `plugin/.claude-plugin/plugin.json`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing tests** — add to `plugin/tests/test_plugin.py`:

```python
def test_hooks_pass_vault_not_index_path():
    hooks = _json(PLUGIN / "hooks" / "hooks.json")
    blob = json.dumps(hooks)
    assert "${user_config.vault_path}" in blob
    assert "index_path" not in blob  # the index is vault-derived; no index arg


def test_plugin_manifest_drops_index_path_and_bumps_version():
    man = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert "index_path" not in man["userConfig"]  # removed
    assert "vault_path" in man["userConfig"]  # still present
    assert man["version"] != "0.1.0"  # bumped so `claude plugin update` ships the fix
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run --no-project --with pytest pytest plugin/tests/test_plugin.py -q -k "index_path or bumps_version"`
Expected: FAIL (`index_path` present in hooks args + userConfig; version is `0.1.0`).

- [ ] **Step 3: Edit the two files**

`plugin/hooks/hooks.json` — in BOTH the SessionStart and SessionEnd `args` arrays, remove the trailing `"${user_config.index_path}"` element so each reads:
```json
"args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-start.sh", "${user_config.vault_path}"],
```
and
```json
"args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-end.sh", "${user_config.vault_path}"],
```
(The scripts already read only `$1` since 0.18, so the dropped `$2` was unused.)

`plugin/.claude-plugin/plugin.json` — delete the entire `index_path` userConfig block:
```json
    "index_path": {
      "type": "string",
      "title": "Index path",
      "description": "DuckDB index location (rebuildable cache).",
      "default": "~/.cache/agentcairn/index.duckdb"
    }
```
(fix the comma after the `vault_path` block so `userConfig` stays valid JSON), and change `"version": "0.1.0"` to `"version": "0.2.0"`.

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run --no-project --with pytest pytest plugin/tests/test_plugin.py -q`
Expected: PASS (new tests + existing `test_plugin_manifest_valid` which only checks `vault_path` is present).

- [ ] **Step 5: Commit**

```bash
git add plugin/hooks/hooks.json plugin/.claude-plugin/plugin.json plugin/tests/test_plugin.py
git commit -m "fix(plugin): drop index_path arg + userConfig; bump plugin version to 0.2.0"
```

---

## Task 3: Command docs + CHANGELOG + full verify

**Files:**
- Modify: `plugin/commands/memory.md`, `plugin/commands/ingest.md`, `CHANGELOG.md`

- [ ] **Step 1: Edit the command docs** — remove the `CAIRN_INDEX` wording so they describe the vault-derived index.

`plugin/commands/memory.md` line 4 currently:
> Run `uvx --from agentcairn cairn doctor` and `uvx --from agentcairn cairn index-status` (both honor `CAIRN_INDEX` for the index location) and summarize the vault location, note/chunk counts, and any health warnings.

Change the parenthetical to: `(both take \`--vault\`; the index is derived from it)`.

`plugin/commands/ingest.md` line 4 currently:
> Run `uvx --from agentcairn cairn sweep --vault "${CAIRN_VAULT:-$HOME/agentcairn}"` to ingest and reindex recent sessions (the index location is taken from `CAIRN_INDEX`, falling back to the default cache), then report how many memories were written.

Change the parenthetical to: `(the index is derived from the vault)`.

- [ ] **Step 2: Verify no `CAIRN_INDEX` / `index_path` remains in the plugin**

Run: `cd /Users/ccf/git/agentcairn && grep -rnE 'CAIRN_INDEX|index_path' plugin/ | grep -v tests/`
Expected: **no output** (every reference removed). If anything remains, fix it.

- [ ] **Step 3: CHANGELOG** — add under `## [Unreleased]` (create the section under the header if missing):

```markdown
### Fixed
- **Plugin recall outage from the 0.18 index migration.** The bundled plugin manifests still pinned
  `CAIRN_INDEX` to the old global index path that 0.18 rehomed away, so the plugin MCP server failed
  with `no index`. Removed `CAIRN_INDEX`/`index_path` from all plugin MCP manifests, hooks, and
  userConfig, and bumped the plugin to 0.2.0; the index now derives from `CAIRN_VAULT`. Update the
  plugin (`claude plugin update agentcairn`, and the Codex/Antigravity equivalents) to restore recall.
```

- [ ] **Step 4: Full verify** — run and confirm:

Run: `cd /Users/ccf/git/agentcairn && uv run --no-project --with pytest pytest plugin/tests/test_plugin.py -q`
Expected: all green.
Run: `uv run pytest -q`
Expected: all green (3 pre-existing skips OK; this change doesn't touch `src/`/`tests/`, so the main suite is unaffected).
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean (no Python changed, but confirm).

- [ ] **Step 5: Commit**

```bash
git add plugin/commands/memory.md plugin/commands/ingest.md CHANGELOG.md
git commit -m "docs(plugin): drop CAIRN_INDEX wording; changelog for plugin vault-scoped fix"
```

---

## Self-Review Notes (author)

- **Spec coverage:** `.mcp.json`/`.mcp.codex.json`/`mcp_config.json` → Task 1; `hooks.json` arg + `plugin.json` userConfig removal + version bump → Task 2; command docs + CHANGELOG + verify → Task 3. The "no migrator needed" and "no src/cairn change" decisions are reflected (no such tasks). Release (cut 0.20.1, plugin update, re-dogfood) is the post-merge ritual, not a plan task.
- **No placeholders:** every edit shows the exact before/after JSON or wording; the grep gate in Task 3 catches any missed reference.
- **Consistency:** version target `0.2.0` (Task 2) matches the spec; the grep gate (`plugin/` minus `tests/`) is the single source of truth that all references are gone.
- **Tests are the right layer:** JSON-shape assertions in `plugin/tests/test_plugin.py` (CI `validate` job); the existing script-execution tests are intentionally untouched (scripts already read only `$1`, and `_run_hook` over-providing `$2` is harmless).
