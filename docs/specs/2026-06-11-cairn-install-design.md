# `cairn install` — per-host MCP setup — Design

**Status:** Approved (brainstorm) — 2026-06-11
**Scope:** Sub-project (a) of the multi-agent plugin epic ([#36](https://github.com/ccf/agentcairn/issues/36)). One implementation plan.
**Sibling sub-projects (separate specs, later):** (b) per-host transcript locators, (c) per-host ambient capture.

## Goal

Make agentcairn's MCP server trivial to set up in any MCP host beyond Claude Code. The server (`uvx agentcairn`) is already portable; this adds a `cairn install <host>` command that **writes/merges** the host's MCP config (non-destructively, idempotently) so `recall`/`search`/`build_context`/`recent`/`remember` light up in Cursor, Codex, Claude Desktop, Windsurf, and Gemini CLI.

## Context & principles

agentcairn is local-first, daemonless, no-telemetry; the vault (`~/agentcairn`, visible) is the source of truth, the DuckDB index (`~/.cache/agentcairn/index.duckdb`) is a rebuildable cache. The same single global vault is shared across hosts (so memory is cross-host, not per-tool). This sub-project touches only **config files the user owns** — it must never clobber unrelated content and must always be reversible (back up before write).

## Scope (v1)

Five hosts, clustered by config format (Zed deferred — bespoke `context_servers`):

| host id | label | config path | format |
|---|---|---|---|
| `cursor` | Cursor | `~/.cursor/mcp.json` | JSON `mcpServers` |
| `claude-desktop` | Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) | JSON `mcpServers` |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` | JSON `mcpServers` |
| `gemini` | Gemini CLI | `~/.gemini/settings.json` | JSON `mcpServers` |
| `codex` | Codex CLI | `~/.codex/config.toml` | TOML `[mcp_servers.agentcairn]` |

Claude Desktop's path is macOS-specific; the registry carries per-OS paths where they differ (Linux/Windows variants documented but only macOS is verified in v1 — others are best-effort + `--print`).

## CLI surface (`src/cairn/cli.py`)

- `cairn install <host>` — configure one host by id.
- `cairn install --all` — configure every **detected** host (config dir/file present) in one pass.
- `cairn install` (no arg) — **detect + preview**: list which of the five hosts are present and what each would receive. **Writes nothing** (safe default).
- `--vault PATH` — vault directory (default `~/agentcairn`); the command **`expanduser`s it to an absolute path** before writing, so no host has to deal with `~` (sidesteps the tilde-expansion issue entirely).
- `--index PATH` — index path (default `~/.cache/agentcairn/index.duckdb`), likewise absolute-ized.
- `--print` — emit the host-format snippet to stdout and write nothing (manual paste / review / unsupported hosts / CI).
- Exit non-zero only on a real error (bad host id, unwritable path after backup); a host whose config is absent is created, not an error.

## What gets written

The agentcairn MCP entry, absolute paths baked in. JSON hosts:

```jsonc
"agentcairn": {
  "command": "uvx",
  "args": ["agentcairn"],
  "env": {
    "CAIRN_VAULT": "/Users/<you>/agentcairn",
    "CAIRN_INDEX": "/Users/<you>/.cache/agentcairn/index.duckdb"
  }
}
```

Codex (TOML):

```toml
[mcp_servers.agentcairn]
command = "uvx"
args = ["agentcairn"]

[mcp_servers.agentcairn.env]
CAIRN_VAULT = "/Users/<you>/agentcairn"
CAIRN_INDEX = "/Users/<you>/.cache/agentcairn/index.duckdb"
```

## Merge semantics (the core invariant)

**Non-destructive + idempotent + reversible:**
- Read the existing config (if any). Add or **update only** the `agentcairn` server/table; preserve every other server and every unrelated top-level key (e.g. Gemini's `theme`/`model`, Codex's `[projects]`/`[tui]`/`[plugins]`).
- **Back up the file first** to `<path>.bak` (overwritten each run) before writing.
- **Idempotent:** a second `install` for the same host with the same paths is a no-op-equivalent (re-writes the same `agentcairn` entry; never duplicates).
- **Malformed existing config:** do not clobber — back up, print a clear error telling the user to fix or use `--print`, exit non-zero for that host (and continue others under `--all`).
- **JSON** via stdlib `json` (parse → set `mcpServers["agentcairn"]` → write with stable 2-space indent). Creates `mcpServers` if absent; creates the file + parent dirs if absent.
- **TOML** via **`tomlkit`** (new runtime dependency — small, pure-Python, round-trips comments/whitespace). Codex configs are hand-edited with comments, so a stdlib read-modify-rewrite would destroy them; tomlkit preserves them. Set the `[mcp_servers.agentcairn]` + `[mcp_servers.agentcairn.env]` tables, leave everything else byte-stable.

## Architecture

A small, focused module so each unit is independently testable:

- **`src/cairn/hosts/__init__.py`** — the **registry**: a list of `Host` definitions `{id, label, config_path(), format}` where `format` is `"mcpServers"` (the JSON `mcpServers` shape — Cursor/Claude Desktop/Windsurf/Gemini) or `"codex-toml"` (Codex). `detected_hosts()` returns those whose config path's parent exists. `get_host(id)` looks one up.
- **`src/cairn/hosts/writers.py`** — `write_json_mcp(path, entry, *, dry) -> str` and `write_codex_toml(path, entry, *, dry) -> str`. Each: backup → merge → write (or, when `dry`, return the rendered snippet without writing). Pure functions over a path + the entry dict; return a human summary string.
- **`src/cairn/hosts/entry.py`** — `mcp_entry(vault, index) -> dict` builds the canonical `{command, args, env}` (single source, shared by all writers + `--print`).
- **`cli.py`** — the `install` command: resolve `--vault`/`--index` to absolute paths, build the entry, dispatch on the registry (one host, `--all`, or no-arg preview), honor `--print`.

This keeps the host quirks (paths, formats) in the registry/writers and the CLI thin. Zed (deferred) = one registry entry + a `write_zed(...)` writer.

## Error handling

- Absent config dir/file → `mkdir -p` + create (not an error).
- Unwritable after backup, or malformed existing config → back up if possible, clear message, non-zero for that host; under `--all`, continue the rest and summarize failures at the end.
- `--print` never touches disk.
- Unknown host id → list valid ids, exit non-zero.

## Testing (all offline, temp paths via the registry's `config_path` indirection or `--print`)

- **`mcp_entry`** builds the expected `{command:"uvx", args:["agentcairn"], env:{CAIRN_VAULT, CAIRN_INDEX}}` with absolute paths.
- **JSON writer:** writes a correct entry into an empty/absent config; **preserves a pre-existing `other-server`** and unrelated top-level keys; **idempotent** (second run → exactly one `agentcairn`, no dup); creates parent dirs; backs up to `.bak`.
- **TOML writer (Codex):** adds the tables; **preserves other tables + comments** (seed a config with `[projects]` + a comment, assert they survive byte-wise); idempotent.
- **Malformed config:** writer backs up + raises/returns an error without clobbering the original bytes.
- **CLI:** `cairn install cursor --vault /tmp/v` writes the entry (temp HOME); `--print` emits the snippet and writes nothing; no-arg preview lists detected hosts and writes nothing; unknown host id exits non-zero; `--all` configures multiple temp hosts and reports a summary.
- **Path absolutization:** `--vault ~/x` (or relative) is written as an absolute path.

## Docs

- README: a **"Use it in any MCP host"** section — `cairn install <host>` / `--all`, the five supported hosts, and a note that the MCP server is the portable core.
- Mention that ambient capture (SessionStart digest / SessionEnd sweep) is Claude-Code-specific today and that per-host capture is tracked in #36 (sub-projects b/c).

## Out of scope (YAGNI / later sub-projects)

- **Zed** (`context_servers` format) — clean follow-up; one registry entry + writer.
- **Ambient capture** on other hosts (hooks / transcript ingestion) — sub-projects (b) and (c).
- **Uninstall/remove** (`cairn install --remove <host>`) — possible later; v1 is add/update only.
- Auto-running `cairn install` from the Claude Code plugin (Claude Code already has the plugin).
- Detecting/migrating an existing agentmemory entry — we only add `agentcairn`; the user removes agentmemory themselves.
