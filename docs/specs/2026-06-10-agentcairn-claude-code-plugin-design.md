# agentcairn Claude Code Plugin — Design Spec

**Date:** 2026-06-10
**Status:** Approved design; pending spec review → implementation plan
**Scope:** A Claude Code plugin, shipped from the agentcairn repo, that makes agentcairn's
memory *ambient and effective* inside Claude Code with a single install.

---

## 1. Goal

One install (`claude plugin install agentcairn@agentcairn`) that:
- auto-wires the agentcairn **MCP server** (no manual `claude mcp add`),
- **surfaces** relevant prior memory at the start of every session,
- **distills** each session into the vault when it ends,
- teaches Claude *when* to recall/remember via a **skill**, and gives quick **slash commands**.

Success: a user installs the plugin, accepts the default vault path, and from then on the coding
agent remembers across sessions with zero manual bookkeeping — the vault is **auto-created**
(Obsidian-ready) on the first session, so it works even for someone who's never used Obsidian.

## 2. Decisions (from brainstorm)

- **Automation level: ambient (B)** — MCP wiring + skills + commands **+ lifecycle hooks**
  (SessionStart surface, SessionEnd distill). *Not* per-prompt auto-recall.
- **Vault: one global personal vault (A)** — default `~/agentcairn`, overridable at install.
  Memories from all projects live in one vault, scoped by project metadata.
- **SessionStart: compact recent digest (A)** — inject a few recent, project-scoped memories.

## 3. Architecture & distribution

- The plugin lives **in the agentcairn repo** under `plugin/`, with
  `.claude-plugin/marketplace.json` at the repo root. The one repo is both the tool's home and
  its plugin marketplace.
- **No Python is vendored.** The plugin wires `uvx agentcairn` (the published PyPI package) for
  the MCP server, and `uvx --from 'agentcairn>=0.2' cairn …` for hook CLI calls. Tool updates
  ship via PyPI; integration updates via a plugin version bump.
- Install:
  ```bash
  claude plugin marketplace add ccf/agentcairn
  claude plugin install agentcairn@agentcairn
  ```

## 4. Prerequisites (tool-side): `cairn recent` + `cairn init` CLI commands

The plugin needs two CLI commands the `cairn` CLI doesn't have yet (it currently exposes only
`parse · reindex · index-status · recall · serve · sweep · doctor · ingest`). So this plan **first
extends agentcairn**, then ships the plugin against the new release:

**`cairn recent`** — for the SessionStart digest (currently `recent` is MCP-only):
- `cairn recent [--index PATH] [--project SUBSTR] [-n N] [--json]`
  → the N most-recent notes (permalink + title + one-line snippet), optionally filtered to a
  project substring, with `--json` for machine parsing by the hook.

**`cairn init`** — scaffolds an **Obsidian-ready vault** so getting started needs no prior Obsidian
setup and no hand-made vault:
- `cairn init [PATH]` (PATH defaults to `$CAIRN_VAULT` or `~/agentcairn`).
- Creates: the vault dir; a minimal `.obsidian/` so Obsidian opens it cleanly as a vault; and a
  `welcome.md` note (valid frontmatter) explaining the vault is agentcairn's memory — written by
  the agent, freely human-editable.
- **Idempotent + non-destructive:** safe to run repeatedly; never overwrites an existing
  `welcome.md` or any notes; only creates what's missing. (No `--force` in v1.)

**Release `agentcairn 0.2.0`** (bump `__version__`, tag `v0.2.0` → the existing Trusted-Publishing
workflow publishes) carrying both commands, **before** the plugin relies on them. Hooks pin
`uvx --from 'agentcairn>=0.2' cairn …`.

This prerequisite phase is part of this plan and must be sequenced first.

## 5. Repo / file layout

```
agentcairn/
├── .claude-plugin/
│   └── marketplace.json              # lists the agentcairn plugin
└── plugin/
    ├── .claude-plugin/
    │   └── plugin.json               # manifest (name, version, userConfig, pointers)
    ├── .mcp.json                     # registers the agentcairn MCP server (uvx agentcairn)
    ├── hooks/
    │   └── hooks.json                # SessionStart + SessionEnd
    ├── scripts/
    │   ├── session-start.sh          # recent-digest → additionalContext
    │   └── session-end.sh            # distill ending session (cairn sweep)
    ├── skills/
    │   └── using-agentcairn-memory/
    │       └── SKILL.md
    ├── commands/
    │   ├── recall.md                 # /agentcairn:recall <query>
    │   ├── remember.md               # /agentcairn:remember <fact>
    │   ├── memory.md                 # /agentcairn:memory  (status)
    │   └── ingest.md                 # /agentcairn:ingest  (manual sweep)
    └── README.md                     # what it installs, the userConfig, how to use
```

## 6. Plugin manifest — `plugin/.claude-plugin/plugin.json`

```json
{
  "name": "agentcairn",
  "displayName": "agentcairn",
  "description": "Local-first agent memory for Claude Code — recall, remember, and ambient capture into a Markdown vault you own.",
  "version": "0.1.0",
  "author": { "name": "Charles C. Figueiredo", "email": "ccf@ccf.io" },
  "homepage": "https://agentcairn.dev",
  "repository": "https://github.com/ccf/agentcairn",
  "license": "Apache-2.0",
  "keywords": ["memory", "mcp", "obsidian", "agent", "local-first"],
  "userConfig": {
    "vault_path": {
      "type": "directory",
      "title": "agentcairn vault",
      "description": "Folder for your Markdown memory vault (created if missing).",
      "default": "~/agentcairn"
    },
    "index_path": {
      "type": "string",
      "title": "Index path",
      "description": "DuckDB index location (rebuildable cache).",
      "default": "~/.cache/agentcairn/index.duckdb"
    }
  },
  "mcpServers": "./.mcp.json",
  "hooks": "./hooks/hooks.json"
}
```
(`skills/` and `commands/` are auto-discovered at the plugin root; no explicit pointer needed.)

## 7. MCP wiring — `plugin/.mcp.json`

```json
{
  "mcpServers": {
    "agentcairn": {
      "command": "uvx",
      "args": ["agentcairn"],
      "env": {
        "CAIRN_VAULT": "${user_config.vault_path}",
        "CAIRN_INDEX": "${user_config.index_path}"
      }
    }
  }
}
```
- `uvx agentcairn` runs the `agentcairn` console script = `cairn.mcp.server:main` (the MCP server).
  **No `mcp` subcommand** — the entry point *is* the server.
- Exposes the five tools: `recall · search · build_context · recent · remember`.
- `CAIRN_EMBEDDER`/`CAIRN_RERANK` are left at agentcairn's defaults (fastembed nomic, reranker on).

## 8. Path resolution (the `~` gotcha)

`CAIRN_VAULT`/`CAIRN_INDEX` reach the server as raw env strings; agentcairn does not expand `~`.
The plan must guarantee **absolute** paths:
- Preferred: `userConfig` `directory`/path values resolve to absolute on selection — verify at
  plan time.
- Fallback (authoritative): the hook scripts and any path use expand `~`/`$HOME` themselves
  before passing to `cairn`/the server, and the install README instructs an absolute vault path.
The plan will verify the actual interpolation behavior and implement whichever guarantees an
absolute path (no silent `./~/agentcairn` directory).

## 9. Hooks — `plugin/hooks/hooks.json` + scripts

```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "*", "hooks": [
        { "type": "command",
          "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-start.sh",
                   "${user_config.vault_path}", "${user_config.index_path}"],
          "timeout": 20 } ] }
    ],
    "SessionEnd": [
      { "matcher": "*", "hooks": [
        { "type": "command",
          "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-end.sh",
                   "${user_config.vault_path}", "${user_config.index_path}"],
          "timeout": 120 } ] }
    ]
  }
}
```

**`session-start.sh`** (vault, index as `$1`, `$2`; stdin = hook JSON with `cwd`):
- Expand `~` in the paths; derive a project key from `cwd` (basename).
- **Ensure the vault exists (zero-step onboarding):** if the vault dir is missing, run
  `uvx --from 'agentcairn>=0.2' cairn init "$VAULT"` (idempotent) so the first session auto-creates
  an Obsidian-ready vault — no manual step even for first-time users.
- `uvx --from 'agentcairn>=0.2' cairn recent --index "$INDEX" --project "$PROJECT" -n 5 --json`
- If it returns memories, format a compact digest (each: `- <title> — <one-line>`) and emit:
  ```json
  {"hookSpecificOutput":{"hookEventName":"SessionStart",
    "additionalContext":"## agentcairn — recent memory for <project>\n- …"}}
  ```
- **Always non-fatal:** empty result, missing index, or any error → exit 0 with no context.
  Never block or delay the session start beyond the timeout.

**`session-end.sh`** (vault, index as `$1`,`$2`; stdin = hook JSON with `cwd`):
- Expand paths; `PROJECT=<cwd>`.
- `uvx --from 'agentcairn>=0.2' cairn sweep --vault "$VAULT" --index "$INDEX" --project "$PROJECT"`
  → ingests the just-ended session's transcript (redact → dedup → importance-gate → distill,
  non-lossy) and incrementally reindexes. The dedup ledger means only *new* sessions distill.
- Local + cheap; **no API key** (heuristic distiller). Errors non-fatal (exit 0).
- *Plan-time refinement:* if teardown latency is noticeable, detach (`… &`); default is synchronous
  within the 120s timeout.

Hook I/O contract: each script reads the hook JSON on stdin (fields incl. `cwd`, `session_id`,
`transcript_path`); SessionStart's stdout JSON carries `additionalContext`.

## 10. Skill — `plugin/skills/using-agentcairn-memory/SKILL.md`

Frontmatter `name: using-agentcairn-memory`, `description:` (when Claude should reach for memory).
Body teaches:
- **Recall before non-trivial work** — search memory ("have we solved/decided this before?") with
  `recall`/`search` before designing, debugging, or re-deriving; expand a hit with `build_context`.
- **Remember durable facts** — use `remember` for decisions, fixes, gotchas, conventions, and user
  preferences worth carrying forward (curated, high-value); the SessionEnd hook handles bulk
  capture, so `remember` is for the things worth pinning deliberately.
- Note the memory is a Markdown vault the user owns and edits; cite recalled notes by permalink.

## 11. Slash commands — `plugin/commands/*.md`

Each is a prompt-style command (frontmatter `description`, body uses `$ARGUMENTS`):
- **`recall.md`** → `/agentcairn:recall <query>`: call the `recall` tool with the query; present
  the cited results compactly.
- **`remember.md`** → `/agentcairn:remember <fact>`: call the `remember` tool with the fact.
- **`memory.md`** → `/agentcairn:memory`: run `uvx --from agentcairn cairn doctor` (+ index-status)
  and report vault/index health + counts.
- **`ingest.md`** → `/agentcairn:ingest`: run `cairn sweep` now (manual distill of recent sessions).

## 12. Testing

- **Tool-side CLI (agentcairn pytest, offline):** unit-test `cairn init` (creates the dir +
  `.obsidian/` + `welcome.md` with valid frontmatter in a tmp vault; idempotent — a second run
  doesn't clobber an edited `welcome.md` or existing notes) and `cairn recent` (returns the
  expected recent notes from a FakeEmbedder-built index; `--project` filter; `--json` shape). These
  live in `tests/test_cli.py`, no network.
- **Manifest/structure validation** (CI + local script): assert `plugin.json`,
  `marketplace.json`, `hooks.json` parse as valid JSON; `.mcp.json` references `uvx` + `agentcairn`;
  every `skills/*/SKILL.md` and `commands/*.md` has valid frontmatter (`name`/`description`).
- **Hook-script smoke (offline):** run `session-start.sh` and `session-end.sh` against a **stubbed
  `cairn`** (a PATH shim returning canned JSON / exit 0) with synthetic stdin, asserting: exit 0
  always; SessionStart emits valid JSON with `additionalContext` when memories exist and nothing
  when empty; scripts never error out. No network / no real `uvx` in tests.
- `shellcheck` the hook scripts.
- Wire into CI as a `plugin` job (path-filtered to `plugin/**`), mirroring the site's test-only
  workflow pattern.

## 13. Out of scope (v1)

- Per-prompt auto-recall (UserPromptSubmit) — deliberately omitted (latency/noise/cost).
- Custom agents, output styles, monitors.
- Telemetry of any kind.
- Bundling/vendoring the Python package (we depend on PyPI via `uvx`).

## 14. Open items to confirm at plan time

1. `userConfig` `~` expansion to absolute (§8) — verify behavior; implement the guaranteed-absolute
   path in the hook scripts regardless.
2. SessionEnd sync vs detached (§9) — default sync; detach if teardown lag shows.
3. Whether `recent --project` should match the project *substring* in note provenance vs an exact
   key — pick substring (lenient) and document.
4. Marketplace/plugin versioning: start plugin at `0.1.0`; the `cairn recent` prerequisite ships in
   agentcairn `0.2.0`.

No blocking unknowns — the design is complete enough to plan, given the `cairn recent` + 0.2.0
release prerequisite is sequenced first.
