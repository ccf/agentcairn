# Antigravity Ingest Adapter

**Status:** Approved (2026-06-14)
**Issue:** #36 (multi-harness ingest), next harness after Codex.
**Affects:** `src/cairn/ingest/harness/` (new `antigravity.py` + registry bootstrap), tests. No change to redaction, judge, consolidation, reindex, the CLI, or the Markdown contract.

## Problem

agentcairn ingests Claude Code and Codex transcripts (0.11.0). The next harness was going to be **Gemini CLI** — but Google is **sunsetting Gemini CLI** (consumer cutoff 2026-06-18) and replacing it with the **Antigravity CLI** (`agy`), which, like Claude Code and Codex, is both a desktop app and a CLI. So we skip Gemini CLI entirely and add **Antigravity** as the new harness.

The #36 adapter framework already exists: a `HarnessAdapter` protocol (`name` / `default_root` / `is_present` / `find` / `iter_raw` / `classify` / `to_event`), a `REGISTRY` with a `_bootstrap_registry`, `TranscriptRef`, and `ParseCtx(path, session_id, cwd, git_branch)`. Adding a harness = one adapter module + one registry line; `cairn sweep`/`ingest` auto-detect it.

## What the dogfood established (research)

Running `agy` (v1.0.8) headless (`-p`) and interactive (`-i`) and inspecting `~/.gemini/antigravity-cli/`:

- **Transcripts are plaintext JSONL** at `~/.gemini/antigravity-cli/brain/<conversation-uuid>/.system_generated/logs/transcript.jsonl` — written **per conversation, in both headless and interactive modes** (comprehensive coverage). A sibling `transcript_full.jsonl` adds verbose internal/tool steps; `transcript.jsonl` is the curated log and is sufficient for user-prompt capture.
- **Each line** is `{step_index, source, type, status, created_at, content}`:
  - `type == "USER_INPUT"` (with `source == "USER_EXPLICIT"`) → the genuine user prompt, wrapped:
    ```
    <USER_REQUEST>
    {genuine prompt}
    </USER_REQUEST>
    <ADDITIONAL_METADATA> …local time… </ADDITIONAL_METADATA>
    <USER_SETTINGS_CHANGE> …model change… </USER_SETTINGS_CHANGE>
    ```
    Only the `<USER_REQUEST>` block is authored; the sibling blocks are injected framing to discard.
  - `type == "PLANNER_RESPONSE"` → assistant turn (`content` is a plain string).
  - `type == "CONVERSATION_HISTORY"` and any other type → bookkeeping/internal → skip.
- **cwd/project** is **not** in the transcript. `~/.gemini/antigravity-cli/cache/last_conversations.json` maps `cwd → conversation-uuid` (last conversation per cwd only — lossy). Best-effort reverse map (uuid → cwd); `None` when absent.
- **Not the source:** `history.jsonl` logs only slash-commands (`{display, type:"slash_command", workspace}`), not user prose. The `conversations/<uuid>.db` is SQLite with **protobuf** step blobs (the internal trajectory) — comprehensive but undocumented/fragile, and adds nothing over `transcript.jsonl` for user-prompt capture. **Both are rejected.**
- **Roots are distinct:** Antigravity CLI = `~/.gemini/antigravity-cli/`; the desktop IDE = `~/.gemini/antigravity/`; Gemini CLI = `~/.gemini/tmp/`. The adapter keys off `~/.gemini/antigravity-cli/brain` only.
- Antigravity's MCP config is `~/.gemini/config/mcp_config.json` — the **same path the existing `cairn install antigravity` host already writes**, so the *output* side is already covered. Antigravity also has a plugin model (`agy plugin`); a first-class Antigravity *plugin* is a separate future cycle (like the Codex plugin), out of scope here.

## Goal / decisions (brainstorm)

- Add an **`antigravity` ingest adapter** sourced from `brain/<uuid>/.system_generated/logs/transcript.jsonl`.
- **Drop Gemini CLI** — do not build a Gemini adapter (sunsetting).
- Extract **only** the `<USER_REQUEST>` block from `USER_INPUT` content — positive-ID by construction (injected metadata/settings blocks can't leak in).
- **Ingest-only scope.** Output is already an MCP host; an Antigravity plugin is a future cycle. Pipeline (redact → judge → consolidate → reindex) unchanged; sweep auto-detects.

## Architecture

### `src/cairn/ingest/harness/antigravity.py` (new) — `AntigravityAdapter`

Mirrors `CodexAdapter`'s structure.

```python
# SPDX-License-Identifier: Apache-2.0
"""Antigravity adapter: ~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/
transcript.jsonl (one JSON object per line). Antigravity CLI replaces Gemini CLI
(sunset 2026-06-18) and is both a desktop app and a CLI.

Positive-ID, fail-closed: only a USER_INPUT step's <USER_REQUEST> block is authored
user prose; injected <ADDITIONAL_METADATA>/<USER_SETTINGS_CHANGE> framing is dropped."""
```

- `name = "antigravity"`.
- `default_root()` → `~/.gemini/antigravity-cli/brain`.
- `is_present()` → `default_root().is_dir()`.
- `find(*, root, project)`:
  - base = `root or default_root()`; if not a dir → `[]`.
  - `files = list(base.glob("*/.system_generated/logs/transcript.jsonl"))`.
  - if `project` given → keep files whose conversation-uuid maps to that cwd via the reverse of `cache/last_conversations.json` (resolved relative to `base.parent`, i.e. `~/.gemini/antigravity-cli/cache/last_conversations.json`); `project` and stored cwd compared `rstrip("/")`. Files with no known cwd are excluded under a project filter.
  - mtime-sorted newest-first.
- `iter_raw(path)` → standard JSONL: read lines, strip, skip blank, `json.loads`, skip on `JSONDecodeError`/non-dict, `yield obj`. (Same robustness as Codex/Claude.)
- `classify(raw)`:
  - `t = raw.get("type")`.
  - `t == "USER_INPUT"` and `raw.get("source") == "USER_EXPLICIT"`: extract `<USER_REQUEST>` text (see `_user_request`); sanitize+lstrip; if it starts with `"/"` (slash-command) → `META_INJECTION`; if empty → `META_INJECTION` (nothing authored); else `AUTHORED_USER`.
  - `t == "PLANNER_RESPONSE"` → `AUTHORED_ASSISTANT`.
  - anything else (`CONVERSATION_HISTORY`, tool/internal, missing) → `UNKNOWN` (fail-closed).
- `to_event(raw, kind, ctx)`:
  - session/ctx: `ctx.session_id` is seeded once from the conversation uuid = `ctx.path.parent.parent.parent.name` (the `<uuid>` in `brain/<uuid>/.system_generated/logs/transcript.jsonl`); `ctx.cwd` from the best-effort reverse map (resolved once per file).
  - `USER_INPUT` → text = the `<USER_REQUEST>` inner block, sanitized; if empty → `None`.
  - `PLANNER_RESPONSE` → text = `content` (str), sanitized; if empty → `None`.
  - other → `None`.
  - build `NormalizedEvent(kind=kind, role=("user" if USER_INPUT else "assistant"), text=text, timestamp=raw.get("created_at"), session_id=ctx.session_id or ctx.path...uuid, project=project_from_cwd(ctx.cwd), git_branch=None, source_path=ctx.path, harness="antigravity")`.

Helpers:
- `_user_request(content: str) -> str`: return the text inside the first `<USER_REQUEST> … </USER_REQUEST>` block (regex, DOTALL), stripped; `""` if absent. This is the positive-ID extraction — everything outside the block (metadata/settings/other injected tags) is dropped.
- A module-level helper to load + reverse `cache/last_conversations.json` into `{uuid: cwd}` (best-effort; returns `{}` on any error). Called per-`find`/per-parse with the cache path derived from the brain root's parent.

### Registry

`_bootstrap_registry()` in `harness/__init__.py` gains:
```python
from cairn.ingest.harness.antigravity import AntigravityAdapter
_register(AntigravityAdapter())
```

## Data flow

```
present_harnesses() includes antigravity when ~/.gemini/antigravity-cli/brain exists
  → find() globs brain/*/.system_generated/logs/transcript.jsonl
  → parse_transcript(ref) → iter_raw (JSONL) → classify → to_event
      → USER_INPUT/<USER_REQUEST> → AUTHORED_USER; PLANNER_RESPONSE → AUTHORED_ASSISTANT
  → ingest_transcripts(...)  # UNCHANGED redact → select → judge → consolidate → write
  → reindex                  # UNCHANGED
```

## Error handling

- Missing `~/.gemini/antigravity-cli/brain` → adapter contributes `[]`; auto-detect skips it.
- Corrupt/partial JSONL line → skipped.
- Missing/unreadable `last_conversations.json` → reverse map `{}`; project `None` (graceful; project filter then matches nothing for antigravity, which is correct — we can't confirm cwd).
- `USER_INPUT` with no `<USER_REQUEST>` block, or empty inner text → not a candidate (`None`).
- Unknown `type`/`source` → `UNKNOWN`, never a candidate (fail-closed).

## Testing / verification

- **Classify:** `USER_INPUT`+`USER_EXPLICIT` genuine prose → `AUTHORED_USER`; `<USER_REQUEST>/help</USER_REQUEST>` (slash) → `META_INJECTION`; `USER_INPUT` with non-explicit source → not authored; `PLANNER_RESPONSE` → `AUTHORED_ASSISTANT`; `CONVERSATION_HISTORY` and unknown types → `UNKNOWN`.
- **`_user_request` extraction:** pulls only the `<USER_REQUEST>` block, dropping `<ADDITIONAL_METADATA>`/`<USER_SETTINGS_CHANGE>` (assert the metadata text is absent from the result — a leakage guard).
- **Parse (end-to-end via `parse_transcript`):** a fixture `brain/<uuid>/.system_generated/logs/transcript.jsonl` with USER_INPUT + PLANNER_RESPONSE + CONVERSATION_HISTORY → only the genuine user prose is `AUTHORED_USER`; `harness == "antigravity"`; `session_id` == the uuid; `project` resolved from a fixture `cache/last_conversations.json`.
- **find:** globs the nested transcript path; mtime-sorted; project filter via `last_conversations.json` reverse map; missing root → `[]`.
- **Registry/auto-detect:** `get_adapter("antigravity")` resolves; with the brain root present, `find_transcripts(harness=None)` includes antigravity refs.
- `uv run pytest` green; `uv run ruff check`/`format` clean.
- **Dogfood:** real `agy` session → `cairn sweep --harness antigravity` (scratch vault) → the genuine user turn is written; the `<ADDITIONAL_METADATA>`/`<USER_SETTINGS_CHANGE>` framing does **not** appear in the vault.

## File-by-file

| File | Change |
|---|---|
| `src/cairn/ingest/harness/antigravity.py` | **new** — `AntigravityAdapter` + `_user_request` + last_conversations reverse-map helper |
| `src/cairn/ingest/harness/__init__.py` | register `AntigravityAdapter` in `_bootstrap_registry` |
| `tests/ingest/test_harness.py` | classify branches, `_user_request` extraction/leakage guard, end-to-end parse, find + project filter, registry |

## Non-goals

- **No Gemini CLI adapter** (sunsetting). If ever needed for enterprise users, it's a separate adapter; not now.
- **No protobuf/SQLite `.db` ingestion** — `transcript.jsonl` is sufficient; the `.db` remains a future fallback only if Antigravity ever stops writing the JSONL.
- **No Antigravity plugin / output work** — already an MCP host; a plugin (`agy plugin`) is a separate future cycle.
- No change to redaction, judge, consolidation, reindex, the CLI, or the Markdown contract.

## Open questions

None.
