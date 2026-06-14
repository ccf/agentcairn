# Antigravity Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a first-class Antigravity plugin — bundle the MCP server + reused skill via `plugin/plugin.json` + `plugin/mcp_config.json`, and make `cairn install antigravity` install the plugin via `agy plugin install` (migrating away the stale global MCP entry).

**Architecture:** Mirror the Codex plugin cycle. Add the two Antigravity-native root files to the shared `plugin/` dir; reclassify `antigravity` from MCP host → plugin host (`kind="plugin"`, `cli="agy"`, single `agy plugin install {source}` command); add a JSON `migrate_antigravity_mcp_block` sibling to the TOML `migrate_codex_mcp_block`; route the CLI's per-host migration by id.

**Tech Stack:** Python 3.12+, `uv` (`uv run pytest` / `uv run ruff`), Typer, stdlib `json`/`shutil`/`subprocess`. Tests: `tests/`.

**Spec:** `docs/specs/2026-06-14-antigravity-plugin-design.md`. **Branch:** `feat/antigravity-plugin` (spec committed).

---

## File Structure

| File | Responsibility |
|---|---|
| `plugin/plugin.json` | **new** — Antigravity manifest (plugin root) |
| `plugin/mcp_config.json` | **new** — Antigravity MCP (wrapper map + CAIRN_VAULT) |
| `src/cairn/hosts/__init__.py` | reclassify `antigravity` → plugin host |
| `src/cairn/hosts/plugins.py` | add `migrate_antigravity_mcp_block` (JSON) |
| `src/cairn/cli.py` | `install` plugin branch: per-host migration dispatch (codex→TOML, antigravity→JSON) |
| `tests/test_plugin_assets.py` | validate the two new assets |
| `tests/test_hosts.py` | update the two antigravity MCP-host tests for the plugin-host reclassification |
| `tests/test_plugins.py` | `migrate_antigravity_mcp_block` + `install_plugin` antigravity tests |
| `tests/test_cli.py` | `install antigravity --print` routing + migration |
| `README.md`, `CLAUDE.md`, `website/src/lib/content.ts` | Antigravity → Plugin host |

---

## Task 1: Antigravity plugin assets

**Files:**
- Create: `plugin/plugin.json`, `plugin/mcp_config.json`
- Test: `tests/test_plugin_assets.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_plugin_assets.py` (it already has `ROOT`, `PLUGIN`, `_load` helpers from the Codex cycle):

```python
def test_antigravity_manifest_valid():
    m = _load(PLUGIN / "plugin.json")
    assert m["name"] == "agentcairn"
    assert "version" in m and "description" in m
    # Antigravity discovers components at the plugin root; the skill must exist
    assert (PLUGIN / "skills").is_dir()


def test_antigravity_mcp_config_is_wrapper_with_vault_env():
    mcp = _load(PLUGIN / "mcp_config.json")
    # wrapper form (NOT a bare map) — Antigravity expects mcpServers
    ac = mcp["mcpServers"]["agentcairn"]
    assert ac["command"] == "uvx" and ac["args"] == ["agentcairn"]
    # CAIRN_VAULT must be set (server has no vault default; remember() needs it)
    assert ac["env"]["CAIRN_VAULT"] == "~/agentcairn"
    assert ac["env"]["CAIRN_INDEX"] == "~/.cache/agentcairn/index.duckdb"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plugin_assets.py -k antigravity -q`
Expected: FAIL — `plugin/plugin.json` / `plugin/mcp_config.json` don't exist.

- [ ] **Step 3: Create `plugin/plugin.json`**

```json
{
  "name": "agentcairn",
  "version": "0.1.0",
  "description": "Local-first agent memory for Antigravity — recall, remember, and ambient capture into a Markdown vault you own.",
  "author": { "name": "Charles C. Figueiredo", "email": "ccf@ccf.io" },
  "homepage": "https://agentcairn.dev",
  "repository": "https://github.com/ccf/agentcairn",
  "license": "Apache-2.0",
  "keywords": ["memory", "mcp", "obsidian", "agent", "local-first"]
}
```

- [ ] **Step 4: Create `plugin/mcp_config.json`**

```json
{
  "mcpServers": {
    "agentcairn": {
      "command": "uvx",
      "args": ["agentcairn"],
      "env": {
        "CAIRN_VAULT": "~/agentcairn",
        "CAIRN_INDEX": "~/.cache/agentcairn/index.duckdb"
      }
    }
  }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_plugin_assets.py -q`
Expected: PASS.

- [ ] **Step 6: (Optional sanity) validate with the real `agy` if present**

Run: `command -v agy >/dev/null && agy plugin validate plugin || echo "agy not on PATH — skip"`
Expected: `[ok]` with `skills`/`mcpServers` processed (or the skip message in CI).

- [ ] **Step 7: Commit**

```bash
git add plugin/plugin.json plugin/mcp_config.json tests/test_plugin_assets.py
git commit -m "feat(plugin): Antigravity plugin assets — manifest + mcp_config (#antigravity-plugin)"
```

(If pre-commit reformats/aborts, `git add -A` and re-run. Applies to every task.)

---

## Task 2: antigravity → plugin host + JSON migration + CLI dispatch

**Files:**
- Modify: `src/cairn/hosts/__init__.py`, `src/cairn/hosts/plugins.py`, `src/cairn/cli.py`
- Test: `tests/test_hosts.py`, `tests/test_plugins.py`, `tests/test_cli.py`

These are coupled (the pre-commit hook runs the full suite, so reclassifying the host must land with the CLI/migration in one green commit).

- [ ] **Step 1: Write the failing tests**

(a) In `tests/test_hosts.py`, **replace** `test_antigravity_and_vscode_registered` with a vscode-only check plus an antigravity plugin-host check:

```python
def test_vscode_registered():
    vs = get_host("vscode")
    assert vs is not None and vs.format == "json"
    assert vs.root_key == "servers"  # VS Code uses "servers", not "mcpServers"


def test_antigravity_is_plugin_host():
    h = get_host("antigravity")
    assert h.kind == "plugin"
    assert h.cli == "agy"
    assert h.plugin_add == ("plugin", "install", "{source}")
    assert h.marketplace_add is None
```

And **replace** `test_antigravity_only_does_not_falsely_detect_gemini` (antigravity is no longer detected via `~/.gemini/config`; it's detected via the `agy` CLI):

```python
def test_gemini_detection_and_antigravity_via_cli(tmp_path, monkeypatch):
    import cairn.hosts as hosts

    monkeypatch.setenv("HOME", str(tmp_path))
    # No agy on PATH → antigravity (plugin host) not detected; Antigravity-only
    # ~/.gemini/config must not falsely detect the Gemini CLI either.
    monkeypatch.setattr(hosts.shutil, "which", lambda c: None)
    (tmp_path / ".gemini" / "config").mkdir(parents=True)
    ids = {h.id for h in hosts.detected_hosts()}
    assert "gemini" not in ids
    assert "antigravity" not in ids  # plugin host needs the agy CLI on PATH
    # agy present → antigravity detected
    monkeypatch.setattr(hosts.shutil, "which", lambda c: "/usr/bin/agy" if c == "agy" else None)
    assert "antigravity" in {h.id for h in hosts.detected_hosts()}
    # real Gemini CLI (settings.json) still detected
    (tmp_path / ".gemini" / "settings.json").write_text("{}")
    assert "gemini" in {h.id for h in hosts.detected_hosts()}
```

(b) Append to `tests/test_plugins.py`:

```python
def test_install_plugin_antigravity_single_command():
    out = install_plugin(get_host("antigravity"), source="ccf/agentcairn", dry=True)
    assert out == "agy plugin install ccf/agentcairn"  # single step, no marketplace-add


def test_migrate_antigravity_removes_entry_preserving_rest(tmp_path):
    import json as _j

    from cairn.hosts.plugins import migrate_antigravity_mcp_block

    p = tmp_path / "mcp_config.json"
    p.write_text(
        _j.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}, "agentcairn": {"command": "uvx"}}})
    )
    note = migrate_antigravity_mcp_block(p, dry=False)
    assert note is not None
    data = _j.loads(p.read_text())
    assert "agentcairn" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "x"}  # sibling preserved
    assert data["theme"] == "dark"  # unrelated key preserved
    assert p.with_name("mcp_config.json.bak").exists()


def test_migrate_antigravity_noop_and_missing(tmp_path):
    import json as _j

    from cairn.hosts.plugins import migrate_antigravity_mcp_block

    assert migrate_antigravity_mcp_block(tmp_path / "nope.json", dry=False) is None
    p = tmp_path / "mcp_config.json"
    p.write_text(_j.dumps({"mcpServers": {"other": {"command": "x"}}}))
    assert migrate_antigravity_mcp_block(p, dry=False) is None  # no agentcairn entry
    assert not p.with_name("mcp_config.json.bak").exists()  # nothing changed → no backup
```

(c) Append to `tests/test_cli.py`:

```python
def test_install_antigravity_print_shows_agy_command(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "antigravity", "--print"])
    assert r.exit_code == 0, r.output
    assert "agy plugin install ccf/agentcairn" in r.output


def test_install_antigravity_print_reports_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".gemini" / "config" / "mcp_config.json"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('{"mcpServers": {"agentcairn": {"command": "uvx"}}}')
    r = runner.invoke(app, ["install", "antigravity", "--print"])
    assert r.exit_code == 0, r.output
    assert "mcpServers.agentcairn" in r.output  # migration surfaced
    assert "agentcairn" in cfg.read_text()  # --print writes nothing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hosts.py tests/test_plugins.py tests/test_cli.py -q`
Expected: FAIL — antigravity still an MCP host (no `kind`/`cli`/`plugin_add`); `migrate_antigravity_mcp_block` undefined; install routes antigravity as MCP.

- [ ] **Step 3: Reclassify the antigravity host**

In `src/cairn/hosts/__init__.py`, **replace** the line
`    Host("antigravity", "Antigravity", "json", "~/.gemini/config/mcp_config.json"),`
with:

```python
    Host(
        "antigravity",
        "Antigravity",
        "plugin",  # format unused for plugin hosts; benign placeholder
        "~/.gemini/config/mcp_config.json",  # used only by the stale-MCP migration
        kind="plugin",
        cli="agy",
        marketplace_add=None,  # `agy plugin install` is a single step
        plugin_add=("plugin", "install", "{source}"),
    ),
```

- [ ] **Step 4: Add the JSON migration helper**

In `src/cairn/hosts/plugins.py`, add `import json` to the imports (next to `import shutil`), and add this function after `migrate_codex_mcp_block`:

```python
def migrate_antigravity_mcp_block(path: Path, *, dry: bool = False) -> str | None:
    """Remove a stale mcpServers.agentcairn entry from a JSON mcp_config.json so the
    bundled plugin MCP isn't double-registered. Backup-first; preserves everything
    else. Returns a note if it removed the entry, else None (no-op)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e}); fix it or use --print") from e
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or "agentcairn" not in servers:
        return None
    if dry:
        return f"would remove mcpServers.agentcairn from {path}"
    backup(path)
    del servers["agentcairn"]
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return f"removed stale mcpServers.agentcairn from {path}"
```

- [ ] **Step 5: Route the CLI migration by host id**

In `src/cairn/cli.py`, the install function imports `migrate_codex_mcp_block` from `cairn.hosts.plugins`; extend that import to also bring `migrate_antigravity_mcp_block`:

```python
    from cairn.hosts.plugins import (
        install_plugin,
        migrate_antigravity_mcp_block,
        migrate_codex_mcp_block,
    )
```

Then in the plugin branch of the `for h in targets:` loop, **replace** the codex-only migration block:

```python
                if h.id == "codex":
                    note = migrate_codex_mcp_block(h.config_path(), dry=print_only)
                    if note:
                        typer.echo(f"  {note}")
```

with a per-host dispatch:

```python
                # Strip a stale agentcairn MCP entry only AFTER a successful install
                # (install_plugin raises on failure), so an aborted install leaves the
                # user's existing MCP wiring intact rather than half-removed.
                migrators = {
                    "codex": migrate_codex_mcp_block,
                    "antigravity": migrate_antigravity_mcp_block,
                }
                migrate = migrators.get(h.id)
                if migrate is not None:
                    note = migrate(h.config_path(), dry=print_only)
                    if note:
                        typer.echo(f"  {note}")
```

- [ ] **Step 6: Run the targeted tests**

Run: `uv run pytest tests/test_hosts.py tests/test_plugins.py tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 7: Run the full suite (catch fallout)**

Run: `uv run pytest -q`
Expected: PASS. If any other test asserted antigravity as an MCP-writer host (grep: `grep -rn "antigravity" tests/ | grep -ivE "harness|ingest"`), update it — antigravity no longer writes `~/.gemini/config/mcp_config.json` via `write_host`. Also confirm `uv run ruff check .` is clean.

- [ ] **Step 8: Commit**

```bash
git add src/cairn/hosts/__init__.py src/cairn/hosts/plugins.py src/cairn/cli.py tests/test_hosts.py tests/test_plugins.py tests/test_cli.py
git commit -m "feat(install): Antigravity is a plugin host (agy plugin install + JSON migration)"
```

---

## Task 3: Docs + full verification

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `website/src/lib/content.ts`

- [ ] **Step 1: Update README.md**

In the "Agents supported" table, change the **Antigravity** row from an MCP-server row to a **Plugin** row, matching the Codex row's style: support "Plugin", setup `cairn install antigravity`, and an ambient marker consistent with how Antigravity captures (it has no recognized plugin hooks; capture is via `cairn sweep` — use the same ◐ "capture via sweep" treatment the Antigravity ingest footnote already uses, or "Plugin (skill + MCP)"). Keep `cairn install` plugin-vs-mcp prose accurate: plugin hosts are now Claude Code, Codex, **Antigravity**. One or two sentences/cells; match surrounding tone; don't overclaim hooks.

- [ ] **Step 2: Update CLAUDE.md**

In the **Plugins** paragraph, add Antigravity to the set of plugin hosts (Claude Code, Codex, Antigravity) installed via the host's own CLI, noting Antigravity's plugin bundles the MCP + skill and that `cairn install antigravity` migrates away the stale global `mcp_config.json` entry. One or two sentences; match voice.

- [ ] **Step 3: Update the website hosts table**

In `website/src/lib/content.ts`, change the **Antigravity** row in `agents.rows` from `support: "MCP server + ingest", ambient: "partial"` to `support: "Plugin + ingest", setup: "cairn install antigravity", ambient: "partial"`. Update the `note`/`body` if they still imply Antigravity is MCP-only (it's now a plugin host that bundles the MCP). Keep claims truthful. Then `cd website && npm run build` to confirm it builds.

- [ ] **Step 4: Full suite + linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green. (If ruff-format would rewrite, run `uv run ruff format .` and re-stage.)

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md website/src/lib/content.ts
git commit -m "docs: Antigravity is a first-class plugin host"
```

---

## Self-Review

**1. Spec coverage:**
- Root `plugin.json` + `mcp_config.json` (wrapper + CAIRN_VAULT) → Task 1. ✓
- antigravity → plugin host (kind/cli/plugin_add single command, marketplace_add None) → Task 2 Step 3 + tests. ✓
- `migrate_antigravity_mcp_block` (JSON, backup-first, preserve siblings, noop/missing/malformed) → Task 2 Step 4 + tests. ✓
- CLI per-host migration dispatch, after successful install → Task 2 Step 5. ✓
- Reuse skill; commands auto-convert; no Antigravity hooks → no hooks file added (Task 1 adds only manifest + mcp_config). ✓
- Existing antigravity MCP-host tests updated → Task 2 Step 1(a). ✓
- Docs (README/CLAUDE/website) → Task 3. ✓
- Pipeline/MCP server/ingest untouched → no task touches them. ✓

**2. Placeholder scan:** No TBD/TODO; complete code in code steps; doc steps describe prose edits (appropriate). ✓

**3. Type consistency:** `migrate_antigravity_mcp_block(path, *, dry)` matches `migrate_codex_mcp_block`'s signature and the CLI dispatch call; `install_plugin(host, *, source, dry)` unchanged; antigravity `plugin_add=("plugin","install","{source}")` + `marketplace_add=None` produce exactly `agy plugin install <source>` via the existing `_commands` helper (skips None). `backup`/`atomic_write` already imported in plugins.py. ✓

**Note for the executor:** Task 2 is the judgment/integration piece (host reclassification + JSON migration + CLI routing) — give it the full two-stage spec-then-quality review. Tasks 1 and 3 verify by diff. After Task 3, dogfood: `cairn install antigravity --source <local plugin dir>` → `agy plugin list` shows agentcairn, the skill loads, recall/remember resolve, and the stale `~/.gemini/config/mcp_config.json` entry is gone. Adjust the `--source` default if `agy plugin install ccf/agentcairn` isn't a valid target (same as the Codex `agentcairn@agentcairn` discovery). Release (0.14.0) is a separate follow-up.
