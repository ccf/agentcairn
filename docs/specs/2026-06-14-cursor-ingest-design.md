# Cursor Ingest Adapter

**Status:** Approved (2026-06-14)
**Issue:** #36 (multi-harness ingest) — the final harness; the deferred SQLite spike.
**Affects:** `src/cairn/ingest/harness/` (new `cursor.py` + registry bootstrap), tests. No change to redaction, judge, consolidation, reindex, the CLI, or the Markdown contract.

## Problem

agentcairn ingests Claude Code, Codex, and Antigravity transcripts. Cursor — the remaining major harness — stores its chat in **SQLite**, which was flagged early as the hard/fragile case. On-disk inspection shows it's actually tractable: clean JSON message records (no protobuf), with a `type` discriminator and a `text` field. This adds a Cursor ingest adapter behind the existing `HarnessAdapter` seam.

## Research — the real Cursor schema (reverse-engineered from the live DB)

- **Store:** `<CursorUser>/globalStorage/state.vscdb` — a SQLite DB (table `cursorDiskKV`). Per-workspace `workspaceStorage/<hash>/state.vscdb` files exist but all chat bubbles live in the **global** DB; we read global only.
- **Rows:** `cursorDiskKV` key `bubbleId:<composerId>:<bubbleId>`, value = a **JSON object** per message ("bubble"):
  - `type`: **1 = user, 2 = assistant** (verified: a `type:1` bubble's `text` is the genuine user prompt; `type:2` is the assistant turn).
  - `text`: the message text. For a user bubble this is the **clean typed prompt** — attached files, rules, codebase context, @-mentions live in *separate* fields (`attachedFiles…`, `cursorRules`, `context`, `contextPieces`, …), so `text` does not carry injected framing.
  - `workspaceProjectDir`: the cwd (provenance). `createdAt`: ISO-8601 timestamp. The **composerId** (conversation/session id) is embedded in the row key.
  - `composerData:<composerId>` rows hold conversation metadata (not needed for user-prompt capture).
- **Access:** Python's stdlib `sqlite3` (SQLite 3.51, json1 built in) opens the live 421 MB DB **read-only without locking** via `file:{path}?immutable=1` — no interference with a running Cursor. `json_extract` pushes the user-filter into SQL so we never materialize the large assistant/tool blobs:
  ```sql
  SELECT key, value FROM cursorDiskKV
  WHERE key LIKE 'bubbleId:%'
    AND json_extract(value, '$.type') = 1
    AND length(json_extract(value, '$.text')) > 0
  ```
- **Output side (separate cycle):** `~/.cursor/plugins/local` exists and Cursor is already an MCP host (`cairn install cursor` → `~/.cursor/mcp.json`). Whether Cursor natively loads a Claude plugin (recall/remember skill) is a deferred follow-up — out of scope here.

## Goal / decisions (brainstorm)

- Add a **`cursor` ingest adapter** sourced from the global `state.vscdb` (`cursorDiskKV`).
- **User bubbles only** (`type==1`, via the SQL filter) — no per-workspace DBs, no assistant-bubble stream (so no antecedent resolution for Cursor, same as the Gemini-logs case).
- **`--project` is not honored** for Cursor (a single global DB can't be path-filtered at `find` time; sweep defaults to all-projects, and per-bubble `workspaceProjectDir` still provides provenance).
- **Ingest-only.** Output/plugin story is a separate follow-up. Pipeline unchanged; sweep auto-detects.

## Architecture

### `src/cairn/ingest/harness/cursor.py` (new) — `CursorAdapter`

Mirrors the existing adapters; the only novelty is a **SQLite `iter_raw`** instead of a line/array reader.

```python
# SPDX-License-Identifier: Apache-2.0
"""Cursor adapter: <CursorUser>/globalStorage/state.vscdb (SQLite, table cursorDiskKV).
Chat messages are JSON "bubbles" keyed bubbleId:<composerId>:<bubbleId>; type 1 = user,
2 = assistant. Only the user bubble's `text` is authored prose (attached files/rules/
context live in separate fields). Positive-ID, fail-closed: only type-1 non-empty text."""
```

- `name = "cursor"`.
- `default_root()` — platform-branched (mirror `cairn.hosts._claude_desktop_path`):
  - macOS: `~/Library/Application Support/Cursor/User`
  - Windows: `~/AppData/Roaming/Cursor/User`
  - else (Linux): `~/.config/Cursor/User`
- `is_present()` → `(default_root() / "globalStorage" / "state.vscdb").is_file()`.
- `find(*, root, project)` → `base = root or default_root()`; `db = base / "globalStorage" / "state.vscdb"`; return `[db]` if it `is_file()` else `[]`. `project` is ignored (documented). (Newest-first sorting is moot for a single file.)
- `iter_raw(path)` — open read-only/immutable, query user bubbles, yield enriched dicts:
  ```python
  def iter_raw(self, path: Path) -> Iterator[dict]:
      uri = f"file:{path}?immutable=1"
      try:
          con = sqlite3.connect(uri, uri=True)
      except sqlite3.Error:
          return  # unreadable DB → no rows (graceful)
      try:
          try:
              cur = con.execute(
                  "SELECT key, value FROM cursorDiskKV "
                  "WHERE key LIKE 'bubbleId:%' "
                  "AND json_extract(value, '$.type') = 1 "
                  "AND length(json_extract(value, '$.text')) > 0"
              )
          except sqlite3.Error:
              return  # missing table / old schema → no rows
          for key, value in cur:
              try:
                  bubble = json.loads(value)
              except (json.JSONDecodeError, ValueError, TypeError):
                  continue
              if not isinstance(bubble, dict):
                  continue
              parts = key.split(":")
              bubble["_composer_id"] = parts[1] if len(parts) >= 2 else ""
              yield bubble
      finally:
          con.close()
  ```
- `classify(raw)` — defense-in-depth (the SQL already filtered, but never trust it):
  - `raw.get("type") == 1` and `sanitize_text(raw.get("text") or "").strip()` is non-empty → `AUTHORED_USER`.
  - else → `UNKNOWN`.
  - **No slash/tag backstop.** Cursor does not inject framing into a user bubble's `text` (attached files, rules, and context live in separate fields), so `type==1` + non-empty text is itself the positive ID. A bare `/`-prefix backstop is deliberately avoided — it would wrongly drop genuine prompts that begin with a path (e.g. `/Users/...`); low-value command-like turns are handled by the durability judge, not by structural dropping. (Cursor bubbles carry `isNudge`/`isQuickSearchQuery`/`isAgentic` flags that could refine this later, but their semantics are unconfirmed, so v1 does not gate on them.)
- `to_event(raw, kind, ctx)`:
  - if `raw.get("type") != 1`: return `None`.
  - `text = sanitize_text(raw.get("text") or "").strip()`; if empty → `None`.
  - return `NormalizedEvent(kind=kind, role="user", text=text, timestamp=raw.get("createdAt"), session_id=raw.get("_composer_id") or ctx.path.stem, project=project_from_cwd(raw.get("workspaceProjectDir")), git_branch=None, source_path=ctx.path, harness="cursor")`.
  - (Each bubble carries its own composerId/cwd, so `ctx` need not be threaded; `ctx.session_id` is left unset and the per-event `_composer_id` is authoritative. The `Transcript.session_id` falls back to `path.stem` = "state".)

### Registry

`_bootstrap_registry()` in `harness/__init__.py` gains:
```python
from cairn.ingest.harness.cursor import CursorAdapter
_register(CursorAdapter())
```

## Data flow

```
present_harnesses() includes cursor when <CursorUser>/globalStorage/state.vscdb exists
  → find() → [state.vscdb]
  → parse_transcript(ref) → iter_raw (SQLite, read-only/immutable, json_extract user filter)
      → classify (type 1 + non-slash → AUTHORED_USER) → to_event
  → ingest_transcripts(...)  # UNCHANGED redact → select → judge → consolidate → write
  → reindex                  # UNCHANGED
```

## Error handling

- Missing `state.vscdb` → adapter contributes `[]`; auto-detect skips cursor.
- Unreadable/locked DB, or missing `cursorDiskKV` table (older Cursor) → `iter_raw` yields nothing (graceful, never raises). Connection always closed.
- Bad-JSON or non-dict bubble value → skipped.
- A bubble with a missing/odd key → `_composer_id` falls back to `""` (then `to_event` falls back to `path.stem`); never crashes.
- `--project` for cursor → ignored (returns the global DB regardless); not an error.

## Testing / verification

- **Fixture SQLite built in-test:** create a temp `state.vscdb` with a `cursorDiskKV(key TEXT, value TEXT)` table and rows — a type-1 user bubble (genuine prose + `workspaceProjectDir`/`createdAt`), a type-2 assistant bubble, and a type-1 empty-text bubble — then drive `parse_transcript(TranscriptRef(path=db, harness="cursor"))`: only the genuine user prose is `AUTHORED_USER`; the assistant and empty bubbles are excluded; `harness=="cursor"`; `session_id` == the composerId from the key; `project` == the `workspaceProjectDir`'s final segment. (A user prompt that happens to start with `/` — e.g. a path — is also asserted to be `AUTHORED_USER`, guarding against an over-eager slash backstop.)
- **classify branches:** type-1 prose → AUTHORED_USER; type-1 leading-`/` path → AUTHORED_USER; type-1 empty/whitespace → UNKNOWN; type-2 → UNKNOWN.
- **find:** present (`state.vscdb` exists) → one path; absent → `[]`.
- **Robustness:** a DB with no `cursorDiskKV` table → `iter_raw` yields nothing (no crash); a malformed value row → skipped.
- **Read-only:** the fixture asserts `iter_raw` opens via `immutable=1` (constructing the same URI), and that the source DB is never written (no `-wal`/`-journal` mutation).
- `uv run pytest` green; `uv run ruff check`/`format` clean.
- **Dogfood:** `cairn sweep --harness cursor` over the real `state.vscdb` (scratch vault) → genuine user prompts written, no assistant/tool text and no attached-file/context-field content leaked into the vault.

## File-by-file

| File | Change |
|---|---|
| `src/cairn/ingest/harness/cursor.py` | **new** — `CursorAdapter` (platform root, SQLite `iter_raw`, type-1 classify, to_event) |
| `src/cairn/ingest/harness/__init__.py` | register `CursorAdapter` in `_bootstrap_registry` |
| `tests/ingest/test_harness.py` | classify branches, end-to-end fixture-SQLite parse, find present/absent, missing-table/malformed robustness |

## Non-goals

- **No output/plugin work** — Cursor stays an MCP host (`cairn install cursor`); native-Claude-plugin support is a separate follow-up.
- **No per-workspace `state.vscdb` ingestion** and **no assistant-bubble stream** (so no antecedent resolution for Cursor).
- **No `--project` filtering** for Cursor (single global DB).
- No change to redaction, judge, consolidation, reindex, the CLI, or the Markdown contract.

## Open questions

None.
