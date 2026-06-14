# Antigravity Plugin (output integration)

**Status:** Approved (2026-06-14)
**Affects:** `plugin/` (new root `plugin.json` + `mcp_config.json`), `src/cairn/hosts/` + `src/cairn/cli.py` (reclassify `antigravity` as a plugin host + JSON migration), `README.md`/`CLAUDE.md`/website. No change to the MCP server, ingest, consolidation, or the Markdown contract.

## Problem

Antigravity is a first-class memory *source* (0.13.0 ingests its transcripts) but second-class on *output*: a user only gets recall/remember by running `cairn install antigravity`, which writes a raw `mcpServers.agentcairn` block into `~/.gemini/config/mcp_config.json` — no skill guidance, no packaged plugin. We already ship first-class Claude Code and Codex **plugins** (bundled MCP + reused skill). Antigravity CLI (`agy`, v1.0.8) has a plugin model (`agy plugin install/uninstall/enable/disable/list/validate/import`), so we can make Antigravity a first-class plugin host too.

The same structural rule applies: **a host with an agentcairn plugin gets its MCP only from the plugin bundle, never also via a written config block.** So `cairn install antigravity` should install the plugin and migrate away a stale global MCP entry.

## Research — the Antigravity plugin model (reverse-engineered from `agy` 1.0.8)

- **Manifest:** `plugin.json` at the **plugin root** (`agy plugin validate <dir>` errors "missing plugin.json"). Unlike Claude (`.claude-plugin/plugin.json`) and Codex (`.codex-plugin/plugin.json`), Antigravity's manifest is at the top level.
- **Components auto-discovered at the root:** `skills/`, `agents/`, `commands/`, `mcpServers`, `hooks`. Minimal manifest (`{name, version, description}`) validates.
- **MCP bundling:** a root **`mcp_config.json`** (auto-discovered; also accepts a `mcpServers: "./mcp_config.json"` pointer) with the **wrapper** form `{"mcpServers": {"agentcairn": {...}}}` (like Claude — NOT Codex's bare map). It must set `CAIRN_VAULT` (the MCP server has no vault default; `~` is expanded server-side). Validated "mcpServers: 1 processed".
- **Skills:** `skills/<name>/SKILL.md` reused verbatim ("skills: 1 processed"). **Commands:** Antigravity auto-converts Claude `commands/*.md` to skills ("commands: 5 processed (converted to skills)") — accepted (recall/remember are useful; harmless). **Hooks:** the Claude `hooks/hooks.json` is **not recognized** ("hooks: skipped"); Antigravity has its own hooks mechanism. No ambient hooks for Antigravity — capture remains via out-of-band `cairn sweep` (0.13.0).
- **Install:** `agy plugin install <target>` where target is a **local directory** (confirmed working) or `<plugin>@<marketplace>`. There is no separate "marketplace add" step (unlike Codex's two-step). Also `agy plugin import [claude|gemini]` imports those harnesses' plugins.
- **Validated on the shared dir:** adding `plugin.json` + `mcp_config.json` to the real `plugin/` tree (which also holds `.claude-plugin/`, `.codex-plugin/`, `.mcp*.json`, `commands/`, `hooks/`) validates as an Antigravity plugin: skills ✓, commands→skills ✓, mcpServers ✓, Claude hooks ignored. The shared-dir pattern extends cleanly.

## Goal / decisions (brainstorm)

- Ship a **first-class Antigravity plugin** via the shared `plugin/` dir: a root `plugin.json` + a root `mcp_config.json` (wrapper, with `CAIRN_VAULT`), reusing the `using-agentcairn-memory` skill. No Antigravity hooks file (unrecognized).
- Make **`antigravity` a plugin host** (was MCP host): `cairn install antigravity` shells `agy plugin install <source>`.
- **Migration (decision "b"):** after a successful install, remove a stale `mcpServers.agentcairn` entry from `~/.gemini/config/mcp_config.json` (JSON, backup-first) — strict "only bundle". Accepted consequence: the Antigravity desktop app (which shares `~/.gemini`) then relies on the plugin's MCP too.
- **Output-only scope.** Ingest (0.13.0) unchanged. Pipeline/MCP server unchanged.

## Architecture

### A. Packaging — shared `plugin/` dir

Add two root files (the `agy`-native manifest + its MCP file); reuse everything else:

```
plugin/
  .claude-plugin/plugin.json     # existing (Claude)
  .codex-plugin/plugin.json      # existing (Codex)
  plugin.json                    # NEW — Antigravity manifest (root)
  .mcp.json                      # existing (Claude; wrapper + ${user_config.*})
  .mcp.codex.json                # existing (Codex; bare map)
  mcp_config.json                # NEW — Antigravity MCP (wrapper map + CAIRN_VAULT)
  skills/using-agentcairn-memory/SKILL.md   # REUSED
  commands/*.md                  # Claude commands; Antigravity auto-converts to skills
  hooks/…                        # Claude/Codex hooks; Antigravity ignores them
```

**`plugin/plugin.json`** (NEW) — version mirrors the Claude/Codex plugin version field:
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

**`plugin/mcp_config.json`** (NEW):
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

### B. `cairn install` — antigravity as a plugin host

In `src/cairn/hosts/__init__.py`, **replace** the MCP-host entry
`Host("antigravity", "Antigravity", "json", "~/.gemini/config/mcp_config.json")`
with a plugin-host entry:
```python
    Host(
        "antigravity",
        "Antigravity",
        "plugin",
        "~/.gemini/config/mcp_config.json",  # used only by the stale-MCP migration
        kind="plugin",
        cli="agy",
        marketplace_add=None,  # agy install is a single step (no separate marketplace-add)
        plugin_add=("plugin", "install", "{source}"),
    ),
```
`install_plugin` already substitutes `{source}` and skips a `None` `marketplace_add`, so it emits exactly `agy plugin install <source>`. `--source` (the existing flag) defaults to `ccf/agentcairn`; the exact accepted published form (local dir vs `<plugin>@<marketplace>`) is verified at dogfood — `agy plugin install <local dir>` is confirmed working, and `install_plugin` passes `--source` straight through, so a local path or marketplace ref both work without code change.

**Migration** — add to `src/cairn/hosts/plugins.py` a JSON sibling of `migrate_codex_mcp_block`:
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

In `src/cairn/cli.py` `install`, the plugin branch runs the host-specific migration **after** a successful `install_plugin` (the Codex ordering lesson — a failed install must not strip the user's MCP). Generalize the current `if h.id == "codex": migrate_codex_mcp_block(...)` to also handle antigravity:
```python
                out = install_plugin(h, source=source, dry=print_only)
                ...
                if h.id == "codex":
                    note = migrate_codex_mcp_block(h.config_path(), dry=print_only)
                elif h.id == "antigravity":
                    note = migrate_antigravity_mcp_block(h.config_path(), dry=print_only)
                else:
                    note = None
                if note:
                    typer.echo(f"  {note}")
```

`antigravity` leaves the MCP-host set (`cursor`, `claude-desktop`, `vscode`, `gemini` remain MCP hosts). `detect()` for the plugin host keys off `shutil.which("agy")`.

### C. Data flow (install)

```
cairn install antigravity [--source …] [--print]
  → get_host("antigravity")  (kind="plugin", cli="agy")
  → shutil.which("agy")  — error if absent
  → agy plugin install <source>            # bundles plugin.json + mcp_config.json + skill
  → migrate_antigravity_mcp_block(~/.gemini/config/mcp_config.json)  # remove stale agentcairn, backup
  (--print: emit the command + the would-remove migration note; run/write nothing)
```

## Error handling

- `agy` not on PATH → `install_plugin` raises a clear ValueError; `--print` still works.
- `agy plugin install` non-zero exit → raises (per-host `except` reports `✗`, exits 1); migration is skipped (runs only after success), so a failed install leaves the global MCP entry intact.
- Malformed `~/.gemini/config/mcp_config.json` → migration raises `ValueError("… not valid JSON …")`, backup already taken; under `--print` the migration is `dry` and never raises on a well-formed-but-absent block (it returns the would-remove note or None).
- Missing/empty global config or no `agentcairn` entry → migration no-op (None), no backup.

## Testing / verification

- **Static assets:** `plugin/plugin.json` + `plugin/mcp_config.json` parse; `mcp_config.json` is the wrapper form with `CAIRN_VAULT` set; manifest has `name == "agentcairn"`. (Optionally assert the shared dir validates by checking the files Antigravity auto-discovers exist: `skills/`, `mcp_config.json`.)
- **Host registry:** `get_host("antigravity").kind == "plugin"`, `.cli == "agy"`, `.plugin_add == ("plugin","install","{source}")`, `.marketplace_add is None`; detected via `shutil.which("agy")`.
- **`install_plugin`** for antigravity emits `agy plugin install ccf/agentcairn` (and honors `--source`); CLI-absent → ValueError; non-zero exit → ValueError.
- **`migrate_antigravity_mcp_block`:** removes only `mcpServers.agentcairn`, preserves sibling servers + other keys, backup-first; missing file / no entry → None (no backup); malformed JSON → ValueError; `dry` writes nothing.
- **CLI:** `cairn install antigravity --print` shows the `agy plugin install` command + the migration note; writes nothing. The existing MCP-host tests are updated (antigravity is no longer an MCP-writer host).
- **Dogfood:** `cairn install antigravity --source <local plugin dir>` → `agy plugin list` shows agentcairn; start an `agy` session and confirm the `recall`/`remember` MCP tools resolve and the skill loads; confirm `~/.gemini/config/mcp_config.json` has no `agentcairn` entry after migration.

## File-by-file

| File | Change |
|---|---|
| `plugin/plugin.json` | **new** — Antigravity manifest (root) |
| `plugin/mcp_config.json` | **new** — Antigravity MCP (wrapper map + CAIRN_VAULT) |
| `src/cairn/hosts/__init__.py` | reclassify `antigravity` → plugin host (kind/cli/plugin_add; marketplace_add=None) |
| `src/cairn/hosts/plugins.py` | add `migrate_antigravity_mcp_block` (JSON) |
| `src/cairn/cli.py` | `install` plugin branch: per-host migration dispatch (codex→TOML, antigravity→JSON) |
| `tests/test_hosts.py`, `tests/test_plugins.py`, `tests/test_cli.py`, `tests/test_plugin_assets.py` | antigravity plugin-host routing, JSON migration, asset validation; drop the antigravity MCP-writer test |
| `README.md`, `CLAUDE.md`, `website/src/lib/content.ts` | Antigravity → Plugin host |

## Non-goals

- **No Antigravity hooks** (format unrecognized) — capture via `cairn sweep`. No change to the ingest adapter.
- **No change** to the MCP server, ingest, consolidation, reindex, or the Markdown contract.
- **No new Antigravity marketplace tooling** — `cairn install antigravity` shells `agy plugin install <source>`; the published-source form (registered marketplace vs local dir) is whatever `--source` is given (default `ccf/agentcairn`), verified/adjusted at dogfood like the Codex `agentcairn@agentcairn` discovery.
- The Claude `commands/` auto-converting to Antigravity skills is accepted (harmless/useful), not suppressed.

## Open questions

None.
