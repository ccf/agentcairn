# Codex Plugin + `cairn install` Plugin/MCP Split — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a first-class Codex plugin (manifest + reused skill + bundled MCP + hooks + marketplace entry) and rework `cairn install` so plugin-capable hosts (`claude-code`, `codex`) install the plugin via the host CLI while other hosts keep writing MCP config.

**Architecture:** Reuse the existing `plugin/` tree — add a `.codex-plugin/plugin.json`, a Codex-format `.mcp.codex.json` (bare server map with literal `CAIRN_VAULT`), and a `hooks/hooks.codex.json` that calls the existing scripts; add a Codex-preferred `.agents/plugins/marketplace.json`. Split the host registry into `kind="mcp"` vs `kind="plugin"`; plugin hosts shell out to `codex`/`claude` plugin CLIs (new `hosts/plugins.py`), and `cairn install codex` first strips any stale `[mcp_servers.agentcairn]` from `~/.codex/config.toml`.

**Tech Stack:** Python 3.12+, `uv` (`uv run pytest`, `uv run ruff`), Typer CLI, `tomlkit`, `subprocess`, `shutil.which`. Tests: pytest under `tests/`.

**Spec:** `docs/specs/2026-06-13-codex-plugin-design.md`. **Branch:** `feat/codex-plugin` (spec committed).

---

## File Structure

| File | Responsibility |
|---|---|
| `plugin/.codex-plugin/plugin.json` | **new** — Codex plugin manifest (skill/mcp/hooks pointers + interface) |
| `plugin/.mcp.codex.json` | **new** — Codex bare server map with literal `CAIRN_VAULT`/`CAIRN_INDEX` |
| `plugin/hooks/hooks.codex.json` | **new** — reuse scripts, no user_config args, `${PLUGIN_ROOT}` |
| `.agents/plugins/marketplace.json` | **new** — Codex-preferred marketplace entry (source `./plugin`) |
| `src/cairn/hosts/__init__.py` | add `kind` + plugin-host fields + `detect()`; reclassify `codex`; add `claude-code` |
| `src/cairn/hosts/plugins.py` | **new** — `install_plugin()` + `migrate_codex_mcp_block()` |
| `src/cairn/hosts/writers.py` | expose `_backup`/`_atomic_write`; remove dead `write_codex_toml` + `"codex-toml"` branch |
| `src/cairn/cli.py` | `install`: branch on `kind`, `--source`, both-kind preview, plugin-host notice, codex migration |
| `tests/test_plugin_assets.py` | **new** — validate the four static JSON assets |
| `tests/test_hosts.py` | registry `kind`/detection tests; drop codex-toml writer tests |
| `tests/test_plugins.py` | **new** — `install_plugin`/`migrate_codex_mcp_block` |
| `tests/test_cli.py` | plugin-host `install --print`/routing; fix codex-dependent test |
| `README.md`, `CLAUDE.md` | document Codex plugin + plugin-vs-mcp split |

---

## Task 1: Codex plugin static assets

**Files:**
- Create: `plugin/.codex-plugin/plugin.json`, `plugin/.mcp.codex.json`, `plugin/hooks/hooks.codex.json`, `.agents/plugins/marketplace.json`
- Test: `tests/test_plugin_assets.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_plugin_assets.py`:

```python
# tests/test_plugin_assets.py
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_codex_manifest_valid_and_pointers_resolve():
    m = _load(PLUGIN / ".codex-plugin" / "plugin.json")
    assert m["name"] == "agentcairn"
    assert m["mcpServers"] == "./.mcp.codex.json"
    assert m["hooks"] == "./hooks/hooks.codex.json"
    assert m["skills"] == "./skills/"
    # every relative pointer resolves to an existing path under plugin/
    assert (PLUGIN / ".mcp.codex.json").is_file()
    assert (PLUGIN / "hooks" / "hooks.codex.json").is_file()
    assert (PLUGIN / "skills").is_dir()
    assert m["interface"]["displayName"] == "agentcairn"


def test_codex_mcp_is_bare_map_with_vault_env():
    mcp = _load(PLUGIN / ".mcp.codex.json")
    # bare server map — NOT wrapped under "mcpServers"
    assert "mcpServers" not in mcp
    ac = mcp["agentcairn"]
    assert ac["command"] == "uvx" and ac["args"] == ["agentcairn"]
    # CAIRN_VAULT must be set (server has no vault default; remember() needs it)
    assert ac["env"]["CAIRN_VAULT"] == "~/agentcairn"
    assert ac["env"]["CAIRN_INDEX"] == "~/.cache/agentcairn/index.duckdb"


def test_codex_hooks_reference_existing_scripts():
    h = _load(PLUGIN / "hooks" / "hooks.codex.json")
    starts = h["hooks"]["SessionStart"][0]["hooks"][0]["args"]
    ends = h["hooks"]["SessionEnd"][0]["hooks"][0]["args"]
    # script path uses ${PLUGIN_ROOT} and points at a real script; no user_config args
    assert starts == ["${PLUGIN_ROOT}/scripts/session-start.sh"]
    assert ends == ["${PLUGIN_ROOT}/scripts/session-end.sh"]
    assert (PLUGIN / "scripts" / "session-start.sh").is_file()
    assert (PLUGIN / "scripts" / "session-end.sh").is_file()


def test_codex_marketplace_lists_plugin_with_local_source():
    mk = _load(ROOT / ".agents" / "plugins" / "marketplace.json")
    plug = mk["plugins"][0]
    assert plug["name"] == "agentcairn"
    assert plug["source"] == {"source": "local", "path": "./plugin"}
    assert (ROOT / "plugin").is_dir()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plugin_assets.py -q`
Expected: FAIL — files don't exist (`FileNotFoundError` / json load errors).

- [ ] **Step 3: Create `plugin/.codex-plugin/plugin.json`**

```json
{
  "name": "agentcairn",
  "version": "0.1.0",
  "description": "Local-first agent memory for Codex — recall, remember, and ambient capture into a Markdown vault you own.",
  "author": { "name": "Charles C. Figueiredo", "email": "ccf@ccf.io" },
  "homepage": "https://agentcairn.dev",
  "repository": "https://github.com/ccf/agentcairn",
  "license": "Apache-2.0",
  "keywords": ["memory", "mcp", "obsidian", "agent", "local-first"],
  "skills": "./skills/",
  "mcpServers": "./.mcp.codex.json",
  "hooks": "./hooks/hooks.codex.json",
  "interface": {
    "displayName": "agentcairn",
    "shortDescription": "Local-first agent memory: recall, remember, ambient capture.",
    "longDescription": "agentcairn gives Codex a persistent, local-first memory: an Obsidian-compatible Markdown vault you own, with hybrid recall, a remember tool, and out-of-band capture of your sessions. No external database, no daemon.",
    "developerName": "Charles C. Figueiredo",
    "category": "Developer Tools",
    "capabilities": ["Interactive", "Write"],
    "websiteURL": "https://agentcairn.dev"
  }
}
```

- [ ] **Step 4: Create `plugin/.mcp.codex.json`**

```json
{
  "agentcairn": {
    "command": "uvx",
    "args": ["agentcairn"],
    "env": {
      "CAIRN_VAULT": "~/agentcairn",
      "CAIRN_INDEX": "~/.cache/agentcairn/index.duckdb"
    }
  }
}
```

- [ ] **Step 5: Create `plugin/hooks/hooks.codex.json`**

```json
{
  "hooks": {
    "SessionStart": [
      { "hooks": [ { "type": "command", "command": "sh",
        "args": ["${PLUGIN_ROOT}/scripts/session-start.sh"], "timeout": 20 } ] }
    ],
    "SessionEnd": [
      { "hooks": [ { "type": "command", "command": "sh",
        "args": ["${PLUGIN_ROOT}/scripts/session-end.sh"], "timeout": 120 } ] }
    ]
  }
}
```

- [ ] **Step 6: Create `.agents/plugins/marketplace.json`**

```json
{
  "name": "agentcairn",
  "interface": { "displayName": "agentcairn" },
  "plugins": [
    {
      "name": "agentcairn",
      "source": { "source": "local", "path": "./plugin" },
      "category": "Developer Tools"
    }
  ]
}
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_plugin_assets.py -q`
Expected: PASS (4 tests).

- [ ] **Step 8: Commit**

```bash
git add plugin/.codex-plugin plugin/.mcp.codex.json plugin/hooks/hooks.codex.json .agents tests/test_plugin_assets.py
git commit -m "feat(plugin): Codex plugin assets — manifest, mcp, hooks, marketplace"
```

(If pre-commit reformats/aborts, `git add -A` and re-run the commit. This applies to every task below.)

---

## Task 2: Host registry — `kind`, plugin hosts, `detect()`

**Files:**
- Modify: `src/cairn/hosts/__init__.py`
- Test: `tests/test_hosts.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_hosts.py`:

```python
def test_codex_is_plugin_host():
    h = get_host("codex")
    assert h.kind == "plugin"
    assert h.cli == "codex"
    assert h.plugin_add == ("plugin", "add", "agentcairn")


def test_claude_code_is_plugin_host():
    h = get_host("claude-code")
    assert h.kind == "plugin"
    assert h.cli == "claude"
    assert h.plugin_add == ("plugin", "install", "agentcairn@agentcairn")


def test_mcp_hosts_keep_kind_mcp():
    assert get_host("cursor").kind == "mcp"
    assert get_host("vscode").kind == "mcp"


def test_plugin_host_detected_via_cli_on_path(monkeypatch):
    import cairn.hosts as hosts

    monkeypatch.setattr(hosts.shutil, "which", lambda c: "/usr/bin/" + c if c == "codex" else None)
    ids = {h.id for h in hosts.detected_hosts()}
    assert "codex" in ids  # codex CLI present
    assert "claude-code" not in ids  # claude CLI absent
```

Update the existing `test_get_host_known_and_unknown` in `tests/test_hosts.py` — its `assert get_host("codex").format == "codex-toml"` line is no longer true. Replace that one line with:

```python
    assert get_host("codex").kind == "plugin"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_hosts.py -q`
Expected: FAIL — `Host` has no `kind`/`cli`/`plugin_add`; `get_host("claude-code")` is None.

- [ ] **Step 3: Extend the `Host` dataclass and registry**

In `src/cairn/hosts/__init__.py`, add `import shutil` at the top (next to `import sys`). Extend `Host` with the new fields (after `detect_template`):

```python
    kind: str = "mcp"  # "mcp" (write a config file) | "plugin" (install via host CLI)
    cli: str | None = None  # plugin hosts: the host's CLI binary (e.g. "codex", "claude")
    marketplace_add: tuple[str, ...] | None = None  # argv after the cli; "{source}" is substituted
    plugin_add: tuple[str, ...] | None = None  # argv after the cli to install the plugin
```

Replace `detect_path`/add a `detect()` method that works for both kinds:

```python
    def detect(self) -> bool:
        """Is this host present? MCP hosts: their detect path exists. Plugin
        hosts: their CLI is on PATH."""
        if self.kind == "plugin":
            return self.cli is not None and shutil.which(self.cli) is not None
        return self.detect_path().exists()
```

Update `detected_hosts()` to use it:

```python
def detected_hosts() -> list[Host]:
    """Hosts that appear present (MCP: config dir exists; plugin: CLI on PATH)."""
    return [h for h in HOSTS if h.detect()]
```

In the `HOSTS` list, **replace the `codex` entry** and **add `claude-code`** (place the two plugin hosts together, e.g. at the end):

```python
    Host(
        "codex",
        "Codex CLI",
        "plugin",  # format is unused for plugin hosts; keep a benign value
        "~/.codex/config.toml",  # used only by the stale-MCP migration
        kind="plugin",
        cli="codex",
        marketplace_add=("plugin", "marketplace", "add", "{source}"),
        plugin_add=("plugin", "add", "agentcairn"),
    ),
    Host(
        "claude-code",
        "Claude Code",
        "plugin",
        "~/.claude",  # detect() ignores this for plugin hosts; CLI presence wins
        kind="plugin",
        cli="claude",
        marketplace_add=("plugin", "marketplace", "add", "{source}"),
        plugin_add=("plugin", "install", "agentcairn@agentcairn"),
    ),
```

(`format` is the 3rd positional arg; `"plugin"` is a harmless placeholder — `write_host` is never called for plugin hosts.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_hosts.py -q`
Expected: PASS (new tests + the updated line). The `detect_path`-based tests (`test_detected_hosts_uses_home`, `test_antigravity_only_does_not_falsely_detect_gemini`) still pass — they test MCP hosts, and `detect()` falls back to `detect_path().exists()` for those.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/hosts/__init__.py tests/test_hosts.py
git commit -m "feat(hosts): kind discriminator + plugin hosts (codex, claude-code)"
```

---

## Task 3: `hosts/plugins.py` — install + codex migration

**Files:**
- Create: `src/cairn/hosts/plugins.py`
- Modify: `src/cairn/hosts/writers.py` (expose `_backup`/`_atomic_write`)
- Test: `tests/test_plugins.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_plugins.py`:

```python
# tests/test_plugins.py
# SPDX-License-Identifier: Apache-2.0
import tomllib
from pathlib import Path

import pytest

from cairn.hosts import get_host
from cairn.hosts.plugins import install_plugin, migrate_codex_mcp_block


def test_install_plugin_dry_emits_commands():
    out = install_plugin(get_host("codex"), source="ccf/agentcairn", dry=True)
    assert "codex plugin marketplace add ccf/agentcairn" in out
    assert "codex plugin add agentcairn" in out


def test_install_plugin_claude_dry_uses_install_at_marketplace():
    out = install_plugin(get_host("claude-code"), source="ccf/agentcairn", dry=True)
    assert "claude plugin marketplace add ccf/agentcairn" in out
    assert "claude plugin install agentcairn@agentcairn" in out


def test_install_plugin_errors_when_cli_absent(monkeypatch):
    import cairn.hosts.plugins as pl

    monkeypatch.setattr(pl.shutil, "which", lambda c: None)
    with pytest.raises(ValueError, match="codex"):
        install_plugin(get_host("codex"), source="ccf/agentcairn", dry=False)


def test_install_plugin_runs_commands_in_order(monkeypatch):
    import cairn.hosts.plugins as pl

    monkeypatch.setattr(pl.shutil, "which", lambda c: "/usr/bin/codex")
    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(argv, **kw):
        calls.append(argv)
        return _R()

    monkeypatch.setattr(pl.subprocess, "run", _run)
    install_plugin(get_host("codex"), source="ccf/agentcairn", dry=False)
    assert calls[0] == ["codex", "plugin", "marketplace", "add", "ccf/agentcairn"]
    assert calls[1] == ["codex", "plugin", "add", "agentcairn"]


def test_migrate_codex_removes_block_preserving_rest(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '# my codex config\nmodel = "gpt-5"\n\n'
        '[mcp_servers.other]\ncommand = "npx"\n\n'
        '[mcp_servers.agentcairn]\ncommand = "uvx"\nargs = ["agentcairn"]\n'
    )
    note = migrate_codex_mcp_block(p, dry=False)
    assert note is not None  # something was removed
    doc = tomllib.loads(p.read_text())
    assert "agentcairn" not in doc.get("mcp_servers", {})
    assert doc["mcp_servers"]["other"] == {"command": "npx"}  # sibling preserved
    assert "# my codex config" in p.read_text()  # comment preserved
    assert p.with_name("config.toml.bak").exists()  # backed up


def test_migrate_codex_noop_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('model = "gpt-5"\n')
    assert migrate_codex_mcp_block(p, dry=False) is None
    assert not p.with_name("config.toml.bak").exists()  # nothing changed → no backup


def test_migrate_codex_missing_file_is_noop(tmp_path):
    assert migrate_codex_mcp_block(tmp_path / "nope.toml", dry=False) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_plugins.py -q`
Expected: FAIL — `cairn.hosts.plugins` doesn't exist.

- [ ] **Step 3: Expose the IO helpers in `writers.py`**

In `src/cairn/hosts/writers.py`, the `_backup` and `_atomic_write` helpers already exist. Leave them where they are; `plugins.py` will import them (`from cairn.hosts.writers import _backup, _atomic_write`). No code change needed in this step — just confirm both functions are module-level in `writers.py` (they are).

- [ ] **Step 4: Create `src/cairn/hosts/plugins.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""Plugin-host support for `cairn install`: install the agentcairn plugin via the
host's own CLI (codex/claude), and migrate a host away from a previously-written
raw MCP config block so the bundled plugin MCP isn't double-registered."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import tomlkit

from cairn.hosts import Host
from cairn.hosts.writers import _atomic_write, _backup


def _commands(host: Host, source: str) -> list[list[str]]:
    """The argv lists to run: marketplace-add then plugin-add, with {source}
    substituted. cli is guaranteed non-None for plugin hosts."""
    out: list[list[str]] = []
    for tmpl in (host.marketplace_add, host.plugin_add):
        if tmpl is None:
            continue
        out.append([host.cli] + [a.replace("{source}", source) for a in tmpl])
    return out


def install_plugin(host: Host, *, source: str, dry: bool = False) -> str:
    """Install the agentcairn plugin into a plugin host via its CLI. With dry=True,
    return the commands (the `--print` view) and run nothing. Raises ValueError if
    the host CLI is not on PATH (real run only)."""
    cmds = _commands(host, source)
    rendered = "\n".join(" ".join(c) for c in cmds)
    if dry:
        return rendered
    if host.cli is None or shutil.which(host.cli) is None:
        raise ValueError(
            f"'{host.cli}' not found on PATH; install {host.label} first, "
            f"or run `cairn install {host.id} --print` to see the commands"
        )
    results: list[str] = []
    for argv in cmds:
        r = subprocess.run(argv, check=False, capture_output=True, text=True)
        tail = (r.stderr or r.stdout or "").strip().splitlines()
        msg = tail[-1] if tail else ""
        # Tolerate idempotent re-runs ("already added/installed") — report, don't fail.
        results.append(f"$ {' '.join(argv)}  →  {'ok' if r.returncode == 0 else msg}")
    return "\n".join(results)


def migrate_codex_mcp_block(path: Path, *, dry: bool = False) -> str | None:
    """Remove a stale [mcp_servers.agentcairn] table from a Codex config.toml so the
    bundled plugin MCP isn't double-registered. Backup-first; preserves everything
    else (tomlkit). Returns a note if it removed the block, else None (no-op)."""
    if not path.exists():
        return None
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"{path} is not valid TOML ({e}); fix it or use --print") from e
    servers = doc.get("mcp_servers")
    if not isinstance(servers, dict) or "agentcairn" not in servers:
        return None
    if dry:
        return f"would remove [mcp_servers.agentcairn] from {path}"
    _backup(path)
    del servers["agentcairn"]
    _atomic_write(path, tomlkit.dumps(doc))
    return f"removed stale [mcp_servers.agentcairn] from {path}"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_plugins.py -q`
Expected: PASS (7 tests).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/hosts/plugins.py tests/test_plugins.py
git commit -m "feat(hosts): install_plugin + codex stale-MCP migration"
```

---

## Task 4: Remove dead `write_codex_toml`

**Files:**
- Modify: `src/cairn/hosts/writers.py`
- Modify: `tests/test_hosts.py` (drop the codex-toml writer tests)

- [ ] **Step 1: Delete the codex-toml writer tests**

In `tests/test_hosts.py`, delete these now-obsolete test functions entirely (codex is no longer an MCP writer): `test_codex_writer_adds_tables_and_preserves`, `test_codex_writer_idempotent`, `test_codex_writer_dry_writes_nothing`, `test_codex_writer_rejects_malformed_but_backs_up`, and any other `test_codex_writer_*` (e.g. the dry-no-backup one ~line 216). Grep to be sure none remain:

Run: `grep -n "write_codex_toml" tests/test_hosts.py`
Expected after deletion: no matches.

- [ ] **Step 2: Run tests to verify the suite is green without them**

Run: `uv run pytest tests/test_hosts.py -q`
Expected: PASS (the codex-toml tests are gone; nothing else references `write_codex_toml` yet — the function still exists).

- [ ] **Step 3: Remove the dead function and dispatch branch**

In `src/cairn/hosts/writers.py`: delete the entire `write_codex_toml(...)` function, and in `write_host` delete the `codex-toml` branch so it reads:

```python
def write_host(host: Host, entry: dict, *, dry: bool = False) -> str:
    """Dispatch to the right writer for the host's config format."""
    if host.format == "json":
        return write_json_mcp(host.config_path(), entry, root_key=host.root_key, dry=dry)
    raise ValueError(f"unknown host format: {host.format!r}")
```

Remove the now-unused `import tomlkit` from `writers.py` (it was only used by `write_codex_toml`). Keep `_backup`/`_atomic_write` (still used by `plugins.py` and `write_json_mcp`).

- [ ] **Step 4: Run the full host + plugin suite**

Run: `uv run pytest tests/test_hosts.py tests/test_plugins.py -q`
Expected: PASS. `grep -n "write_codex_toml" src/ tests/` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/hosts/writers.py tests/test_hosts.py
git commit -m "refactor(hosts): drop dead write_codex_toml (codex is a plugin host)"
```

---

## Task 5: `cairn install` rework — route by kind

**Files:**
- Modify: `src/cairn/cli.py` (the `install` command, ~line 383–460)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py` (it already imports `runner = CliRunner()` and `app` — reuse them):

```python
def test_install_codex_print_shows_plugin_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "codex", "--print"])
    assert r.exit_code == 0, r.output
    assert "codex plugin marketplace add ccf/agentcairn" in r.output
    assert "codex plugin add agentcairn" in r.output


def test_install_claude_code_print_and_source_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "claude-code", "--print", "--source", "/local/checkout"])
    assert r.exit_code == 0, r.output
    assert "claude plugin marketplace add /local/checkout" in r.output
    assert "claude plugin install agentcairn@agentcairn" in r.output


def test_install_codex_print_reports_stale_mcp_migration(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cfg = tmp_path / ".codex" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[mcp_servers.agentcairn]\ncommand = "uvx"\nargs = ["agentcairn"]\n')
    r = runner.invoke(app, ["install", "codex", "--print"])
    assert r.exit_code == 0, r.output
    assert "mcp_servers.agentcairn" in r.output  # migration is surfaced
    assert cfg.read_text().startswith("[mcp_servers.agentcairn]")  # --print writes nothing
```

Fix the existing `test_install_all_print_labels_each_host` — it creates `~/.codex` and asserts `"# Codex CLI"` in a `--print` MCP snippet, which no longer holds (codex is a plugin host detected via the CLI, not `~/.codex`). Replace its body so it tests MCP-host labeling without codex:

```python
def test_install_all_print_labels_each_host(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    (tmp_path / ".gemini").mkdir()
    (tmp_path / ".gemini" / "settings.json").write_text("{}")
    r = runner.invoke(app, ["install", "--all", "--print"])
    assert r.exit_code == 0, r.output
    assert "# Cursor" in r.output
    assert "# Gemini CLI" in r.output
    assert not (tmp_path / ".cursor" / "mcp.json").exists()  # --print writes nothing
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k install -q`
Expected: FAIL — install doesn't yet handle plugin hosts / `--source` (unknown option) / migration.

- [ ] **Step 3: Rework the `install` command**

In `src/cairn/cli.py`, replace the `install` function with this kind-aware version (keeps the MCP path identical for MCP hosts):

```python
@app.command()
def install(
    host: str = typer.Argument(
        None, help="Host id: claude-code / codex (plugins) · cursor / claude-desktop / "
        "vscode / gemini / antigravity (mcp)."
    ),
    all_hosts: bool = typer.Option(False, "--all", help="Configure every detected host."),
    print_only: bool = typer.Option(False, "--print", help="Print the config/commands; write nothing."),
    vault: Path = typer.Option(None, "--vault", help="Vault path (mcp hosts; default ~/agentcairn)."),
    index: Path = typer.Option(
        None, "--index", help="Index path (mcp hosts; default ~/.cache/agentcairn/index.duckdb)."
    ),
    source: str = typer.Option(
        "ccf/agentcairn", "--source", help="Plugin marketplace source (plugin hosts)."
    ),
) -> None:
    """Install agentcairn into another agent: the plugin for plugin hosts
    (Claude Code, Codex), or the MCP server config for MCP hosts (Cursor, …)."""
    from cairn.hosts import HOSTS, detected_hosts, get_host
    from cairn.hosts.entry import mcp_entry
    from cairn.hosts.plugins import install_plugin, migrate_codex_mcp_block
    from cairn.hosts.writers import write_host

    settings = cairn_env()
    default_vault = Path(settings.get("CAIRN_VAULT") or (Path.home() / "agentcairn"))
    default_index = Path(
        settings.get("CAIRN_INDEX") or (Path.home() / ".cache" / "agentcairn" / "index.duckdb")
    )
    v = str((vault or default_vault).expanduser().resolve())
    idx = str((index or default_index).expanduser().resolve())
    ids = ", ".join(h.id for h in HOSTS)

    if host is None and not all_hosts:  # detect + preview, write nothing
        present = detected_hosts()
        if not present:
            typer.echo(f"No supported agents detected. Supported: {ids}")
            return
        typer.echo("Detected — run `cairn install <id>` (or `--all`):")
        for h in present:
            where = f"plugin via `{h.cli}`" if h.kind == "plugin" else str(h.config_path())
            typer.echo(f"  {h.id:15} {h.label}  → {where}")
        return

    if all_hosts:
        targets = detected_hosts()
        if not targets:
            typer.echo(f"No supported agents detected. Supported: {ids}")
            return
    else:
        h = get_host(host)
        if h is None:
            typer.echo(f"unknown host '{host}'. Supported: {ids}")
            raise typer.Exit(1)
        targets = [h]

    failures = 0
    for h in targets:
        try:
            if h.kind == "plugin":
                if (vault or index) and not print_only:
                    typer.echo(f"  note: --vault/--index don't apply to {h.label} (set in the plugin's config)")
                if h.id == "codex":
                    note = migrate_codex_mcp_block(h.config_path(), dry=print_only)
                    if note:
                        typer.echo(f"  {note}")
                out = install_plugin(h, source=source, dry=print_only)
                header = f"# {h.label} (plugin via `{h.cli}`)" if print_only else f"✓ {h.label}:"
                typer.echo(header)
                typer.echo(out)
            else:
                entry = mcp_entry(v, idx)
                out = write_host(h, entry, dry=print_only)
                if print_only:
                    typer.echo(f"# {h.label} ({h.config_path()})")
                    typer.echo(out)
                else:
                    typer.echo(f"✓ {h.label}: {out}")
        except Exception as e:  # best-effort per host; continue under --all
            failures += 1
            typer.echo(f"✗ {h.label}: {e}")
    if failures:
        raise typer.Exit(1)
```

- [ ] **Step 4: Run the install tests**

Run: `uv run pytest tests/test_cli.py -k install -q`
Expected: PASS (new plugin-host tests + the reworked `--all` test + the existing cursor/preview/unknown-host tests).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS. (If any other test referenced `cairn install codex` writing TOML, update it — grep `grep -rn "install.*codex" tests/`.)

- [ ] **Step 6: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): install routes plugin hosts to the plugin, mcp hosts to config"
```

---

## Task 6: Docs + full verification

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Update README.md**

Find the `cairn install` host table / examples (the recon noted lines ~103, 110 list `cairn install codex`). Update so the table distinguishes **plugin hosts** (Claude Code, Codex → `cairn install <host>` installs the plugin via the host CLI; MCP bundled) from **MCP hosts** (Cursor, Claude Desktop, VS Code, Gemini, Antigravity → writes MCP config). Add a one-line Codex example: `cairn install codex` (installs the plugin; removes any stale MCP block). Keep claims truthful — Gemini/Cursor are MCP-only; only Claude Code + Codex have a plugin. One short paragraph or table edit; match surrounding tone.

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, find where the plugin / install story is described. Add one or two sentences: agentcairn ships a Codex plugin (mirrors the Claude Code plugin — reused skill + hooks + bundled MCP) discoverable via the Codex marketplace; `cairn install` installs the *plugin* for plugin hosts (Claude Code, Codex) and writes MCP config for the rest. Match the file's voice; do not restructure.

- [ ] **Step 3: Full suite + linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green. (If ruff-format would rewrite, run `uv run ruff format .` and re-stage.)

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: document the Codex plugin + plugin-vs-mcp install split"
```

---

## Self-Review

**1. Spec coverage:**
- Packaging (`.codex-plugin/plugin.json`, `.mcp.codex.json` with `CAIRN_VAULT`, `hooks.codex.json` reusing scripts, marketplace) → Task 1. ✓
- Reuse skill + scripts verbatim → Task 1 (no new skill/script files; manifest/hooks point at existing ones). ✓
- Host registry split `kind` mcp/plugin + `detect()` + claude-code/codex → Task 2. ✓
- `install_plugin` (shell to CLI, idempotent, CLI-absent error) + `migrate_codex_mcp_block` → Task 3. ✓
- Remove dead `write_codex_toml` / `codex-toml` branch → Task 4. ✓
- `cairn install` routes by kind, `--source`, both-kind preview, plugin-host `--vault/--index` notice, codex migration → Task 5. ✓
- Docs (README + CLAUDE.md) → Task 6. ✓
- Non-goals respected: no MCP server/ingest/pipeline change; no Gemini/Cursor plugin; no Codex slash-commands. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete content; commands have expected output. Task 6 doc steps describe edits to prose (no code), which is appropriate for docs. ✓

**3. Type consistency:** `Host.kind`/`cli`/`marketplace_add`/`plugin_add`/`detect()` defined in Task 2 and used identically in Tasks 3 & 5; `install_plugin(host, *, source, dry)` and `migrate_codex_mcp_block(path, *, dry)` signatures match between Task 3 (definition) and Task 5 (call sites). `_commands` substitutes `{source}` consistent with the registry templates. ✓

**Note for the executor:** All tasks are mechanical given the complete code — verify each diff directly; the most judgment is in Task 5 (CLI routing) — give that diff a careful read. Each task ends green (it updates the tests it breaks). After Task 6, dogfood manually: `codex plugin marketplace add <local checkout>` + `codex plugin add agentcairn`, start a Codex session, confirm `recall`/`remember` resolve and the skill loads, and `~/.codex/config.toml` has no `[mcp_servers.agentcairn]`. Then the release ritual is a separate follow-up.
