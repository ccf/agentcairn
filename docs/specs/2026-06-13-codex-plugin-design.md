# Codex Plugin + `cairn install` Plugin/MCP Split

**Status:** Approved (2026-06-13)
**Affects:** `plugin/` (new Codex manifest + Codex MCP/hooks files; reuse skill+scripts), `.agents/plugins/marketplace.json` (new), `src/cairn/hosts/` + `src/cairn/cli.py` (`install` rework), `README.md`/`CLAUDE.md`. No change to the MCP server, ingest, consolidation, or the memory pipeline.

## Problem

agentcairn is a first-class **Claude Code** plugin (manifest + MCP server + skill + hooks + slash-commands) and, since 0.11.0, ingests **Codex** transcripts. But the Codex *output* side is second-class: a Codex agent only gets recall/remember if the user manually runs `cairn install codex`, which writes a raw `[mcp_servers.agentcairn]` block into `~/.codex/config.toml` — no skill guidance telling the agent to use memory, no packaged plugin, no marketplace install.

Codex now has a plugin system (`developers.openai.com/codex/plugins/build`) whose format **deliberately mirrors Claude Code's**: `.codex-plugin/plugin.json`, `skills/<name>/SKILL.md` (identical), `hooks/hooks.json` (same schema; Codex sets both `PLUGIN_ROOT` and `CLAUDE_PLUGIN_ROOT`), a bundled `.mcp.json`, marketplace discovery via `codex plugin marketplace add` (and it reads the legacy `.claude-plugin/marketplace.json`). We can ship a real Codex plugin with heavy reuse.

This also exposes a structural rule: **a host that has an agentcairn plugin should get its MCP server *only* from the plugin bundle — never also via `cairn install` writing a config block** (double-registration). So `cairn install` should *install the plugin* for plugin-capable hosts, and only write MCP config for hosts with no plugin mechanism.

## Goal / decisions (brainstorm)

- Ship a **first-class Codex plugin** (full parity: manifest + reused skill + bundled MCP + hooks + marketplace entry), installable via `codex plugin marketplace add ccf/agentcairn` → `codex plugin add agentcairn`.
- **Reuse the existing `using-agentcairn-memory` skill and the hook scripts verbatim** — one source of truth.
- **`cairn install` splits into plugin-hosts vs MCP-hosts.** Plugin hosts (`claude-code`, `codex`) → install the plugin by shelling to the host's official CLI. MCP hosts (`cursor`, `claude-desktop`, `vscode`, `gemini`, `antigravity`) → unchanged (write MCP config).
- **Migration:** `cairn install codex` removes any stale `[mcp_servers.agentcairn]` from `~/.codex/config.toml` (backup-first) before/while installing the plugin, so the bundled MCP isn't double-registered.
- **Full-parity hooks** for Codex (SessionStart digest + SessionEnd ingest), accepting that Codex's honoring of a SessionStart `additionalContext` injection is unconfirmed and will be verified during dogfood (graceful degradation if not — the script still scaffolds/warms and exits 0).
- One spec covers both the plugin and the `cairn install` rework (coupled by the "only bundle" rule).

## Architecture

### A. Packaging — one `plugin/` tree, dual manifest

The existing `plugin/` directory serves both harnesses. Add Codex-specific files alongside the Claude ones; **reuse** the skill and scripts:

```
plugin/
  .claude-plugin/plugin.json     # existing (Claude Code)
  .codex-plugin/plugin.json      # NEW — Codex manifest
  .mcp.json                      # existing — Claude format (mcpServers wrapper + ${user_config.*})
  .mcp.codex.json                # NEW — Codex bare server map, no env (server defaults)
  hooks/hooks.json               # existing (Claude; ${user_config.*} args)
  hooks/hooks.codex.json         # NEW — same scripts, no user_config args, ${PLUGIN_ROOT}
  scripts/session-start.sh       # REUSED verbatim
  scripts/session-end.sh         # REUSED verbatim
  skills/using-agentcairn-memory/SKILL.md   # REUSED verbatim
  commands/*.md                  # Claude-only (Codex has no documented slash-commands)
```

**Why separate `.mcp` / `hooks` files (not shared):** Codex's `.mcp.json` is a **bare server map** with **no `${user_config.*}` interpolation**; Claude's has an `mcpServers` wrapper and uses `${user_config.vault_path}`. The two formats are incompatible in one file, and the manifest's `mcpServers`/`hooks` fields each point to a path, so each harness points at its own file.

#### `.mcp.codex.json` (NEW)

Bare map (no `mcpServers` wrapper). It **must set `CAIRN_VAULT`** to the literal default `~/agentcairn`: the MCP server resolves `vault` from `CAIRN_VAULT` → else `None`, and `remember` raises if vault is unset (`cairn.mcp.server:resolve_config`; only the *index* has a built-in default). The server expands a leading `~` in `CAIRN_VAULT` (so the literal `~/agentcairn` works regardless of the launching cwd). `CAIRN_INDEX` is set too for parity with the Claude `.mcp.json`, though it would default. Codex doesn't interpolate `${user_config.*}`, so these are literals; power users override via `~/.codex/config.toml [plugins."agentcairn".mcp_servers.agentcairn]`.

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

#### `.codex-plugin/plugin.json` (NEW)

Mirrors the Claude manifest's identity fields; points `mcpServers`/`hooks` at the Codex files; adds the `interface` block Codex uses for marketplace display. `version` mirrors the Claude `plugin.json` version field (the plugin-level version, independent of the PyPI package version) and is bumped with plugin changes.

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

#### `hooks/hooks.codex.json` (NEW)

Same two scripts, invoked with **no vault/index args** (the scripts already default to `~/agentcairn` + the standard index — Codex doesn't interpolate `${user_config.*}`), using `${PLUGIN_ROOT}` (Codex sets it). Modeled on Codex's documented hooks shape (no `matcher` required). `session-end.sh` runs `cairn sweep` which auto-detects the Codex harness (0.11.0); `session-start.sh` emits a recent-memory digest as `additionalContext` (Codex support for this is verified at dogfood; on non-support it harmlessly no-ops + warms the cache).

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

> The scripts are unchanged. `session-start.sh`/`session-end.sh` already treat `$1`/`$2` as optional (`${1:-$HOME/agentcairn}`, `${2:-…index.duckdb}`), so omitting args yields the defaults. They reference no `*_PLUGIN_ROOT` env internally (only the hook config does, to locate the script).

### B. Marketplace discovery

- Keep `.claude-plugin/marketplace.json` (Claude uses it; Codex reads it as legacy).
- **Add `.agents/plugins/marketplace.json`** (Codex-preferred repo marketplace) listing the plugin with a local source path, so `codex plugin marketplace add ccf/agentcairn` discovers it first-class:

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

(`path` is relative to the repo root the marketplace is added from. A `policy` block is omitted — defaults apply; we add it only if a dogfood install shows it's required.)

### C. `cairn install` rework — plugin hosts vs MCP hosts

Extend the host registry with a `kind` discriminator and a small plugin-host descriptor; route `install` by kind.

**`src/cairn/hosts/__init__.py`** — add to `Host`:
- `kind: str = "mcp"` — `"mcp"` (write a config file) or `"plugin"` (install via host CLI).
- For plugin hosts, three fields: `cli: str | None = None` (e.g. `"codex"`, `"claude"`), and a way to build the marketplace-add + install commands. Concretely add:
  - `marketplace_add: tuple[str, ...] | None` — e.g. `("plugin", "marketplace", "add", "{source}")`
  - `plugin_add: tuple[str, ...] | None` — e.g. `("plugin", "add", "agentcairn")` (Codex) / `("plugin", "install", "agentcairn@agentcairn")` (Claude)
- `detect_path()` semantics by kind: MCP hosts unchanged (config dir/file exists); **plugin hosts detect via `shutil.which(cli) is not None`** (the CLI is on PATH). Add a `detect()` method that encapsulates this so `detected_hosts()` works for both kinds.

Registry after the change:
```
PLUGIN hosts:
  claude-code   cli=claude  marketplace_add=(plugin,marketplace,add,{source})  plugin_add=(plugin,install,agentcairn@agentcairn)
  codex         cli=codex   marketplace_add=(plugin,marketplace,add,{source})  plugin_add=(plugin,add,agentcairn)
MCP hosts (unchanged):
  cursor, claude-desktop, vscode, gemini, antigravity
```
`{source}` defaults to `ccf/agentcairn` (GitHub shorthand) and is overridable with a new `--source` flag (for dogfooding a local checkout path).

**`src/cairn/hosts/plugins.py`** (NEW) — plugin-host install logic, isolated from the MCP writers:
- `install_plugin(host, *, source, dry) -> str`: if `shutil.which(host.cli)` is None → raise `ValueError("'<cli>' not found on PATH; install <Host label> first, or see <docs>")`. Build the two command lists (substituting `{source}`). With `dry=True`, return them joined (the `--print` view). Else run each via `subprocess.run([...], check=False, capture_output=True, text=True)`, tolerate "already added/installed" (report, don't fail), and return a summary. `marketplace add` then `plugin add/install`, in order.
- `migrate_codex_mcp_block(path, *, dry) -> str | None`: if `~/.codex/config.toml` has `[mcp_servers.agentcairn]`, remove just that key (tomlkit, preserving everything else), backup-first; return a note (or None if nothing to migrate). Reuses the `_backup`/`_atomic_write` helpers (move them to a shared module or import from `writers`).

**`src/cairn/cli.py` `install`** — branch on `host.kind`:
- MCP host → current path (`mcp_entry` + `write_host`). Unchanged.
- Plugin host → for `codex`, first call `migrate_codex_mcp_block` (report if it removed a block); then `install_plugin(host, source=source, dry=print_only)`. `--print` shows the migration note + the `codex plugin …` commands; no `--vault`/`--index` are written (the plugin's bundled MCP config sets the vault/index — note this in the output if the user passed them).
- The no-arg detect/preview lists both kinds (plugin hosts shown as "→ plugin via `<cli>`").
- Add `--source` option (default `ccf/agentcairn`).
- Help text updated: `Host id: claude-code / codex (plugins) · cursor / claude-desktop / vscode / gemini / antigravity (mcp).`

### D. Data flow (install a plugin host)

```
cairn install codex [--source ccf/agentcairn] [--print]
  → get_host("codex")  (kind="plugin", cli="codex")
  → shutil.which("codex")  — error if absent
  → migrate_codex_mcp_block(~/.codex/config.toml)   # remove stale [mcp_servers.agentcairn], backup
  → codex plugin marketplace add ccf/agentcairn
  → codex plugin add agentcairn                      # bundles .mcp.codex.json + skill + hooks
  (--print: emit the migration note + the two commands; run nothing)
```

## Error handling

- Host CLI not on PATH → clear `ValueError` naming the CLI + how to get it; `--print` still works (it doesn't need the CLI).
- `codex/claude plugin …` non-zero exit → surface stderr in the per-host summary; under `--all`, continue other hosts and exit non-zero at the end (matches current behavior).
- "marketplace already added" / "plugin already installed" → treated as success (idempotent), reported as such.
- Malformed `~/.codex/config.toml` during migration → same as the existing writers: raise `ValueError("… not valid TOML; fix it or use --print")`, backup already taken.
- A plugin host passed `--vault`/`--index` → those don't apply (plugin MCP uses server defaults); print a one-line notice rather than silently ignoring.

## Testing / verification

- **Static-asset tests:** `.codex-plugin/plugin.json`, `.mcp.codex.json`, `.agents/plugins/marketplace.json` parse as JSON and carry required keys (`name`, `mcpServers`/`skills`/`hooks` pointers resolve to existing files; marketplace `plugins[].source.path` exists). `hooks.codex.json` parses and its script paths exist.
- **Registry:** `kind` routing — `get_host("codex").kind == "plugin"`, `get_host("cursor").kind == "mcp"`; `detected_hosts()` includes a plugin host iff its CLI is on PATH (monkeypatch `shutil.which`).
- **`cairn install <plugin host> --print`:** emits the exact `codex plugin marketplace add ccf/agentcairn` / `codex plugin add agentcairn` (and the `claude … install agentcairn@agentcairn`) commands; honors `--source`; shows the migration note. No files written, no subprocess run.
- **Migration:** a `config.toml` containing `[mcp_servers.agentcairn]` plus an unrelated table → after `migrate_codex_mcp_block`, the agentcairn block is gone, the unrelated table and comments are preserved, a `.bak` exists; a config without the block → no-op, returns None.
- **MCP hosts unchanged:** existing `test_hosts.py`/`test_cli.py` MCP-writer tests still pass (the `codex` MCP-writer test is removed/replaced since codex is no longer an MCP host).
- **Plugin install (mocked):** with `shutil.which` returning a path and `subprocess.run` patched, `install_plugin` runs marketplace-add then plugin-add in order and tolerates a simulated "already installed" non-zero/again-ok result.
- **Dogfood (manual, recorded):** real `codex plugin marketplace add <local-checkout>` + `codex plugin add agentcairn`; start a Codex session; confirm the `recall`/`remember` MCP tools resolve and the `using-agentcairn-memory` skill loads; confirm `~/.codex/config.toml` has no `[mcp_servers.agentcairn]` after migration; spot-check SessionEnd swept the session.

## File-by-file

| File | Change |
|---|---|
| `plugin/.codex-plugin/plugin.json` | **new** — Codex manifest (skill/mcp/hooks pointers + interface) |
| `plugin/.mcp.codex.json` | **new** — Codex bare server map (no env; server defaults) |
| `plugin/hooks/hooks.codex.json` | **new** — reuse scripts, no user_config args, `${PLUGIN_ROOT}` |
| `.agents/plugins/marketplace.json` | **new** — Codex-preferred marketplace entry (source `./plugin`) |
| `src/cairn/hosts/__init__.py` | add `kind` + plugin-host fields (`cli`, `marketplace_add`, `plugin_add`); `detect()` by kind; reclassify `codex` as a plugin host; add `claude-code` plugin host |
| `src/cairn/hosts/plugins.py` | **new** — `install_plugin()` + `migrate_codex_mcp_block()` |
| `src/cairn/hosts/writers.py` | export `_backup`/`_atomic_write` for reuse (or move to a shared `_io` helper); **remove the now-dead `write_codex_toml` and the `"codex-toml"` branch in `write_host`** (codex is no longer an MCP host) |
| `src/cairn/cli.py` | `install`: branch on `host.kind`; `--source`; detect/preview both kinds; updated help |
| `tests/test_hosts.py`, `tests/test_cli.py` | plugin-host routing/detection/migration/`--print` tests; drop the codex MCP-writer test |
| `README.md`, `CLAUDE.md` | document the Codex plugin + the plugin-vs-mcp `cairn install` split |

## Non-goals

- **No Gemini or Cursor plugin** (next cycles). They remain MCP hosts.
- **No change** to the MCP server, its tools, ingest, consolidation, or the Markdown contract.
- **No Codex slash-commands** (the format is undocumented) — agent guidance comes from the reused skill.
- The existing Claude Code plugin's files (`.claude-plugin/plugin.json`, `.mcp.json`, `hooks/hooks.json`, `commands/`) are unchanged except that `claude-code` becomes installable via `cairn install claude-code`.

## Open questions

None.
