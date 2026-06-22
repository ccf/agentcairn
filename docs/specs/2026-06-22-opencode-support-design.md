# First-Class OpenCode Support — Design

**Status:** approved 2026-06-22
**Goal:** Make [OpenCode](https://opencode.ai) (`sst/opencode`, open-source terminal AI agent) a **first-class agentcairn host** — one-command install wiring (a) the MCP server, (b) recall/remember slash commands, and (c) an ambient TS plugin (recall-at-start + capture-at-end) — plus a `HarnessAdapter` so `cairn sweep` auto-ingests OpenCode sessions. Prompted by an organic user request + the repo's first star.

## Background

OpenCode's integration surfaces (researched):
- **MCP host:** `opencode.json` `mcp` block — `{"type":"local","command":[…],"enabled":true}` for stdio. **Not** the standard `mcpServers` block, so it needs an OpenCode-specific writer.
- **Transcripts:** `~/.local/share/opencode/storage/` (XDG; `OPENCODE_DATA_DIR` override, comma-separated allowed), JSON: `session/<projectID>/<sessionID>.json`, `message/<sessionID>/<messageID>.json`, `part/<messageID>/<partID>.json` (text/tool parts).
- **Plugins/hooks:** a JS/TS plugin system (`@opencode-ai/plugin`) auto-loaded from the plugin dir; rich lifecycle events incl. `session.created`, `session.idle` (agent finished), `session.compacted`, and `system.transform` (mutate the model context — the recall-injection point).
- **Slash commands:** markdown files in `~/.config/opencode/commands/*.md`.

The competitor `rohitg00/agentmemory` ships a dedicated MCP `mcp`-block writer, a **22-hook** TS plugin (full Claude-Code parity), and recall/remember commands. We take its *structure* but deliberately stay lean — the memory value comes from ~3 hooks, not 22.

agentcairn's existing tiers: plugin hosts (Claude Code/Codex/Antigravity), MCP hosts (Cursor/VS Code/Claude Desktop), and ingest adapters (`cairn.ingest.harness.*`). OpenCode becomes the first host to combine **all three** — MCP + ingest + a true ambient plugin.

## Architecture

Two layers, both **reusing existing agentcairn machinery** — the TS plugin adds no memory logic:
- **A (Python):** an `OpenCodeAdapter` (ingest) + `cairn install opencode` (MCP `mcp`-block writer + slash commands).
- **B (TS):** a lean `@opencode-ai/plugin` that is a **thin orchestrator shelling out to the `cairn` CLI** — `system.transform` → `cairn recall` (inject), `session.idle`/`session.compacted` → `cairn sweep` (capture, reusing the A adapter). All recall/distill logic stays in Python.

## Components

### A1 — `OpenCodeAdapter` (`src/cairn/ingest/harness/opencode.py`)
Implements the `HarnessAdapter` protocol (mirror `cursor.py`), registered via `_register`:
- `name = "opencode"`.
- `default_root()` → `$OPENCODE_DATA_DIR or ~/.local/share/opencode` + `/storage` (support the comma-separated `OPENCODE_DATA_DIR` by searching each).
- `is_present()` → the storage dir exists with at least one `message/` session.
- `find(root, project)` → one `Path` per session (the `message/<sessionID>/` dir), optionally filtered by `project` via `session/<projectID>` → project mapping.
- `iter_raw(path)` → yield each message dict for that session, **joined with its parts** (read `message/<sid>/<mid>.json` + the text parts from `part/<mid>/*.json`), in message order.
- `classify(raw)` → **positive-ID, fail-closed**: only an OpenCode *user* message's authored text parts → `AUTHORED_USER`; assistant → `AUTHORED_ASSISTANT`; everything else (tool, system, non-text parts) → `SYSTEM`/`UNKNOWN` (never a candidate). Confirm the role field + text-part `type` from a real OpenCode message JSON sample before finalizing.
- `to_event(raw, kind, ctx)` → `NormalizedEvent` (text via `sanitize_text`, `harness="opencode"`, project via `project_from_cwd`, session_id from the dir). Returns `None` for empty text.

`cairn sweep` then auto-detects + distills OpenCode sessions through the existing pipeline (redact → gate → distill → reindex), deduped by the ledger — no pipeline changes.

### A2 — `cairn install opencode` (`src/cairn/hosts/`)
- **MCP writer** (`hosts/writers.py`): a dedicated OpenCode writer that merges into `~/.config/opencode/opencode.json`'s **`mcp`** block (NOT `mcpServers`): `{"agentcairn": {"type":"local","command":[<uvx agentcairn / cairn serve>],"enabled":true}}` — non-destructive (preserve other servers), idempotent, backup the original to `<config>.bak` (match the existing writers' behavior).
- **Slash commands** (`hosts/` + bundled markdown): install `recall.md` + `remember.md` to `~/.config/opencode/commands/` (markdown command files that invoke the recall/remember flow). Bundle the command markdown under the repo (e.g. `integrations/opencode/commands/`).
- Register `opencode` in the host registry (`hosts/__init__.py`) + the `cairn install` dispatch (`cli.py:489`, the help string), as an MCP-host-style install that ALSO drops the B plugin (below).

### B — ambient TS plugin (`integrations/opencode/`)
A lean `@opencode-ai/plugin` (TypeScript; OpenCode loads `.ts` directly from its plugin dir — no build step). It is a **thin shell over the `cairn` CLI**:
- `system.transform` → run `cairn recall --json "<latest user prompt>"` and inject the returned memories into the model context (recall-at-start). (Confirm the CLI's JSON recall flag/shape.)
- `session.idle` (and `session.compacted`) → spawn `cairn sweep` (non-blocking) to capture the just-ended session via the A1 adapter (immediate capture instead of waiting for the scheduled sweep).
- That's it — ~3 hooks, not 22. No tool-lifecycle/permission/task hooks. Plus the 2 slash commands from A2.
- **Install:** `cairn install opencode` copies the plugin into OpenCode's plugin dir (`~/.config/opencode/plugin/`), so one command wires MCP + commands + plugin.

## Data flow

`cairn install opencode` → writes `mcp` block + commands + plugin. Per session: the plugin's `system.transform` injects `cairn recall` results; on `session.idle` it fires `cairn sweep`, which the `OpenCodeAdapter` feeds into the normal distill pipeline → the shared `~/agentcairn` vault. `cairn sweep` (scheduled or manual) also catches any sessions independently. Recall tools are additionally available via the MCP server for explicit use.

## Error handling

- **Adapter:** positive-ID + fail-closed (a row not affirmatively authored-user prose is never a candidate); a malformed/partial message JSON is skipped, never aborts the session/sweep (mirror Cursor's `json_valid`-first robustness).
- **Plugin:** every shell-out is wrapped — a `cairn` failure (not installed, error) is logged to OpenCode's plugin log and swallowed; it must **never break the OpenCode session**. `session.idle` capture runs detached/non-blocking.
- **Redaction** is inherited (capture goes through the existing `sweep`/ingest redact-before-write path).
- **Install** is non-destructive + backup-first; missing `~/.config/opencode/` is created.

## Testing

- **A1 adapter (Python, unit):** fixture `storage/` tree (a couple of `message/`+`part/` JSON files) → `find`/`iter_raw`/`classify`/`to_event`: user text → `AUTHORED_USER`, assistant → `AUTHORED_ASSISTANT`, tool/non-text → not a candidate; malformed part skipped (fail-closed); a planted secret is redacted downstream. Mirror `tests/.../test_cursor*`.
- **A2 install (Python, unit):** writing into a temp `opencode.json` adds the `mcp.agentcairn` entry, preserves existing `mcp` servers, is idempotent, backs up; commands land in a temp commands dir.
- **B plugin (TS):** lean — a small unit test of the pure bits (building the `cairn recall`/`cairn sweep` command + parsing recall JSON for injection); the hook wiring itself is **manual-QA in a real OpenCode session** (per the repo convention for non-unit-testable host integration). Keep the testable logic in a pure function.

## Out of scope (follow-ups)

- The competitor's 22-hook maximalism (tool/permission/task tracking) — only recall+capture for v1.
- npm-package distribution of the plugin (ship in-repo + copy-install for v1).
- OpenCode `type:remote` MCP (we wire the local stdio server).
- Project-scoped `opencode.json` (write the global `~/.config/opencode/opencode.json`).
- **Project attribution for OpenCode memories** (deferred): the adapter does not yet map an OpenCode session → its `projectID` (from `session/<projectID>/<sid>.json`), so ingested OpenCode memories carry `project=None` and are not project-boosted/filtered by recall. Wire it once the `session.json` `projectID`/cwd schema is confirmed against a real install (manual-QA item).

## Definition of done

- `cairn install opencode` writes the agentcairn MCP server into `opencode.json`'s `mcp` block, installs `recall`/`remember` slash commands, and drops the ambient plugin — non-destructive, idempotent, backup-first.
- `OpenCodeAdapter` makes `cairn sweep` auto-detect + distill OpenCode sessions (positive-ID, fail-closed), through the existing pipeline + ledger, into the shared vault.
- The TS plugin does ambient recall (`system.transform` → `cairn recall`) + capture (`session.idle` → `cairn sweep`), fail-safe (never breaks OpenCode), as a thin CLI orchestrator.
- README + website host table updated (OpenCode row, first-class). Tests: adapter + install unit-tested in Python; the TS plugin's pure logic unit-tested, hooks manual-QA'd.
