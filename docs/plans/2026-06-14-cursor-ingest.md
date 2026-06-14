# Cursor Ingest Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cursor` HarnessAdapter that ingests genuine user prompts from Cursor's global `state.vscdb` SQLite store, so `cairn sweep` auto-detects and captures Cursor sessions.

**Architecture:** One new adapter module mirroring the Codex/Antigravity adapters, registered in `_bootstrap_registry`. The novelty is a **SQLite `iter_raw`**: open the global `state.vscdb` read-only (`immutable=1`) and select user bubbles (`cursorDiskKV` rows, `type==1`) via `json_extract`, yielding parsed JSON bubble dicts. Positive-ID/fail-closed: only a `type==1` bubble with non-empty `text` is AUTHORED_USER. Pipeline/CLI unchanged.

**Tech Stack:** Python 3.12+, stdlib `sqlite3` (json1), `uv` (`uv run pytest`/`uv run ruff`). Tests: `tests/ingest/test_harness.py`.

**Spec:** `docs/specs/2026-06-14-cursor-ingest-design.md`. **Branch:** `feat/cursor-ingest` (spec committed).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cairn/ingest/harness/cursor.py` | **new** — `CursorAdapter` (platform root, SQLite iter_raw, type-1 classify, to_event) |
| `src/cairn/ingest/harness/__init__.py` | register `CursorAdapter` in `_bootstrap_registry` |
| `tests/ingest/test_harness.py` | classify branches, fixture-SQLite end-to-end parse, find present/absent, missing-table robustness, registry |
| `README.md`, `CLAUDE.md`, `website/src/lib/content.ts` | Cursor is now an ingested harness |

---

## Task 1: CursorAdapter

**Files:**
- Create: `src/cairn/ingest/harness/cursor.py`
- Modify: `src/cairn/ingest/harness/__init__.py`
- Test: `tests/ingest/test_harness.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_harness.py`:

```python
def _make_cursor_db(path, rows):
    """Build a fixture Cursor state.vscdb: a cursorDiskKV(key, value) table whose
    values are JSON bubbles. `rows` is a list of (key, dict)."""
    import json as _j
    import sqlite3

    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        [(k, _j.dumps(v)) for k, v in rows],
    )
    con.commit()
    con.close()


def test_cursor_classify():
    from cairn.ingest.harness.cursor import CursorAdapter

    a = CursorAdapter()
    assert a.name == "cursor"
    assert a.classify({"type": 1, "text": "combine the repos"}) == EventKind.AUTHORED_USER
    # a genuine prompt that starts with "/" (a path) must NOT be dropped
    assert a.classify({"type": 1, "text": "/Users/x/f.py please review"}) == EventKind.AUTHORED_USER
    assert a.classify({"type": 1, "text": "   "}) == EventKind.UNKNOWN  # whitespace
    assert a.classify({"type": 1}) == EventKind.UNKNOWN  # no text
    assert a.classify({"type": 2, "text": "assistant reply"}) == EventKind.UNKNOWN


def test_cursor_parses_user_bubbles(tmp_path):
    from cairn.ingest.locate import parse_transcript

    db = tmp_path / "state.vscdb"
    _make_cursor_db(
        db,
        [
            ("bubbleId:comp-1:b1", {"type": 1, "text": "Combine the repos into one",
                                    "workspaceProjectDir": "/Users/x/proj",
                                    "createdAt": "2025-12-20T23:29:17.798Z"}),
            ("bubbleId:comp-1:b2", {"type": 2, "text": "Here is a plan"}),  # assistant → excluded
            ("bubbleId:comp-1:b3", {"type": 1, "text": "/Users/x/f.py please review",
                                    "workspaceProjectDir": "/Users/x/proj"}),  # leading-/ path
            ("bubbleId:comp-1:b4", {"type": 1, "text": "   ",
                                    "workspaceProjectDir": "/Users/x/proj"}),  # whitespace → excluded
        ],
    )
    tr = parse_transcript(TranscriptRef(path=db, harness="cursor"))
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == [
        "Combine the repos into one",
        "/Users/x/f.py please review",
    ]
    assert all(e.harness == "cursor" for e in authored)
    assert authored[0].session_id == "comp-1"  # composerId from the key
    assert authored[0].project == "proj"  # from workspaceProjectDir
    assert authored[0].timestamp == "2025-12-20T23:29:17.798Z"


def test_cursor_find_present_and_absent(tmp_path):
    from cairn.ingest.harness.cursor import CursorAdapter

    a = CursorAdapter()
    assert a.find(root=tmp_path, project=None) == []  # no globalStorage/state.vscdb
    gs = tmp_path / "globalStorage"
    gs.mkdir()
    db = gs / "state.vscdb"
    _make_cursor_db(db, [("bubbleId:c:b", {"type": 1, "text": "hi"})])
    assert a.find(root=tmp_path, project=None) == [db]
    # project filter is N/A for the single global DB — still returns it
    assert a.find(root=tmp_path, project="/Users/x/anything") == [db]


def test_cursor_iter_raw_missing_table_is_graceful(tmp_path):
    import sqlite3

    from cairn.ingest.harness.cursor import CursorAdapter

    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE other (k TEXT)")  # no cursorDiskKV
    con.commit()
    con.close()
    a = CursorAdapter()
    assert list(a.iter_raw(db)) == []  # missing table → no rows, no crash


def test_cursor_registered():
    assert get_adapter("cursor").name == "cursor"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_harness.py -k cursor -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.harness.cursor'`.

- [ ] **Step 3: Create the adapter**

Create `src/cairn/ingest/harness/cursor.py` with EXACTLY:

```python
# src/cairn/ingest/harness/cursor.py
# SPDX-License-Identifier: Apache-2.0
"""Cursor adapter: <CursorUser>/globalStorage/state.vscdb (SQLite, table cursorDiskKV).
Chat messages are JSON "bubbles" keyed bubbleId:<composerId>:<bubbleId>; type 1 = user,
2 = assistant. Only the user bubble's `text` is authored prose (attached files/rules/
context live in separate fields). Positive-ID, fail-closed: only type-1 non-empty text."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

# Select only user bubbles (type==1) with non-empty text, pushing the filter into
# SQL via json_extract so the large assistant/tool blobs are never materialized.
_USER_BUBBLE_SQL = (
    "SELECT key, value FROM cursorDiskKV "
    "WHERE key LIKE 'bubbleId:%' "
    "AND json_extract(value, '$.type') = 1 "
    "AND length(json_extract(value, '$.text')) > 0"
)


def _cursor_user_root() -> Path:
    """The Cursor `User` config dir for the current platform."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Cursor" / "User"
    if sys.platform.startswith("win"):
        return home / "AppData" / "Roaming" / "Cursor" / "User"
    return home / ".config" / "Cursor" / "User"


class CursorAdapter:
    name = "cursor"

    def default_root(self) -> Path:
        return _cursor_user_root()

    def is_present(self) -> bool:
        return (self.default_root() / "globalStorage" / "state.vscdb").is_file()

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        # Single global DB; `project` cannot be honored at find time (provenance is
        # per-bubble via workspaceProjectDir). Returns the DB if it exists.
        base = Path(root) if root is not None else self.default_root()
        db = base / "globalStorage" / "state.vscdb"
        return [db] if db.is_file() else []

    def iter_raw(self, path: Path) -> Iterator[dict]:
        try:
            con = sqlite3.connect(f"file:{path}?immutable=1", uri=True)  # read-only, no lock
        except sqlite3.Error:
            return  # unreadable DB → no rows
        try:
            try:
                cur = con.execute(_USER_BUBBLE_SQL)
            except sqlite3.Error:
                return  # missing cursorDiskKV table / old schema → no rows
            for key, value in cur:
                try:
                    bubble = json.loads(value)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue  # malformed value → skip
                if not isinstance(bubble, dict):
                    continue
                parts = key.split(":")
                bubble["_composer_id"] = parts[1] if len(parts) >= 2 else ""
                yield bubble
        finally:
            con.close()

    def classify(self, raw: dict) -> EventKind:
        if raw.get("type") == 1 and sanitize_text(raw.get("text") or "").strip():
            return EventKind.AUTHORED_USER
        return EventKind.UNKNOWN

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        if raw.get("type") != 1:
            return None
        text = sanitize_text(raw.get("text") or "").strip()
        if not text:
            return None
        return NormalizedEvent(
            kind=kind,
            role="user",
            text=text,
            timestamp=raw.get("createdAt"),
            session_id=raw.get("_composer_id") or ctx.path.stem,
            project=project_from_cwd(raw.get("workspaceProjectDir")),
            git_branch=None,  # Cursor bubbles carry no git branch
            source_path=ctx.path,
            harness=self.name,
        )
```

- [ ] **Step 4: Register the adapter**

In `src/cairn/ingest/harness/__init__.py`, extend `_bootstrap_registry`:

```python
def _bootstrap_registry() -> None:
    from cairn.ingest.harness.antigravity import AntigravityAdapter
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter
    from cairn.ingest.harness.codex import CodexAdapter
    from cairn.ingest.harness.cursor import CursorAdapter

    _register(ClaudeCodeAdapter())
    _register(CodexAdapter())
    _register(AntigravityAdapter())
    _register(CursorAdapter())
```

- [ ] **Step 5: Run the cursor tests**

Run: `uv run pytest tests/ingest/test_harness.py -k cursor -q`
Expected: PASS (5 tests).

- [ ] **Step 6: Run the full ingest suite (no regressions)**

Run: `uv run pytest tests/ingest/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cairn/ingest/harness/cursor.py src/cairn/ingest/harness/__init__.py tests/ingest/test_harness.py
git commit -m "feat(ingest): Cursor adapter — SQLite cursorDiskKV user bubbles (#36)"
```

(If pre-commit reformats/aborts, `git add -A` and re-run the commit.)

---

## Task 2: Docs + full verification

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `website/src/lib/content.ts`

- [ ] **Step 1: Update README.md**

In the capture/ingest description, add **Cursor** to the list of harnesses whose sessions are captured (Claude Code, Codex, Antigravity, **Cursor**). In the "Agents supported" table, the **Cursor** row is currently an MCP-server row — change its support cell to note ingest too (e.g. "MCP server + ingest") and add a footnote (mirror the Antigravity ingest footnote) naming the source: Cursor's global `state.vscdb` (`cursorDiskKV` user bubbles), read out-of-band by `cairn sweep`. Keep tone/format; don't claim a plugin (Cursor output stays the MCP host).

- [ ] **Step 2: Update CLAUDE.md**

In the "Capture pipeline (ingest)" sentence that names the harnesses, add **Cursor** (`<CursorUser>/globalStorage/state.vscdb`, SQLite `cursorDiskKV`). One clause; match voice; don't restructure.

- [ ] **Step 3: Update the website hosts table**

In `website/src/lib/content.ts`, the `agents.rows` **Cursor** row is `{ host: "Cursor", support: "MCP server", setup: "cairn install cursor", ambient: "none" }`. Change `support` to `"MCP server + ingest"` and `ambient` to `"partial"` (capture-via-sweep, like Antigravity's pre-plugin row). Update the `note`/`body` if they imply Cursor has no capture. Then `cd website && npm run build` to confirm it builds.

- [ ] **Step 4: Full suite + linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green. (If ruff-format would rewrite, run `uv run ruff format .` and re-stage.)

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md website/src/lib/content.ts
git commit -m "docs: Cursor is an ingested harness (SQLite cursorDiskKV)"
```

---

## Self-Review

**1. Spec coverage:**
- Adapter from global `state.vscdb` `cursorDiskKV`, user bubbles via json_extract → Task 1 `iter_raw` + `_USER_BUBBLE_SQL`. ✓
- Read-only `immutable=1` → Task 1 `iter_raw`. ✓
- Platform root + `is_present` on `globalStorage/state.vscdb` → Task 1 `_cursor_user_root`/`is_present`. ✓
- `find` returns the global DB; `--project` ignored → Task 1 `find` + test. ✓
- classify `type==1`+non-empty → AUTHORED_USER; no slash backstop (leading-`/` path stays authored); empty/type-2 → UNKNOWN → Task 1 `classify` + tests. ✓
- to_event: text/session_id(composerId)/project(workspaceProjectDir)/timestamp(createdAt)/harness="cursor" → Task 1 `to_event` + parse test. ✓
- Robustness: missing table / unreadable / malformed → graceful no-rows → Task 1 `iter_raw` + `test_cursor_iter_raw_missing_table_is_graceful`. ✓
- Registered + auto-detected → bootstrap + `test_cursor_registered`. ✓
- Pipeline/CLI unchanged; ingest-only; no per-workspace DBs/assistant stream → no other modules touched; docs (Task 2). ✓

**2. Placeholder scan:** No TBD/TODO; complete code in every code step; commands have expected output. Task 2 doc steps describe prose edits (appropriate). ✓

**3. Type consistency:** `_cursor_user_root()`, `_USER_BUBBLE_SQL`, `CursorAdapter` methods match the protocol (`name`/`default_root`/`is_present`/`find(*,root,project)`/`iter_raw`/`classify`/`to_event(raw,kind,ctx)`) used by `parse_transcript`. `bubble["_composer_id"]` set in `iter_raw` and read in `to_event`. ✓

**Note for the executor:** Task 1 is the judgment-heavy piece (the SQLite `iter_raw` + read-only/immutable handling + classification) — give it the full two-stage spec-then-quality review. Task 2 is docs — verify the diff. After Task 2, dogfood: `cairn sweep --harness cursor` over the real `~/Library/Application Support/Cursor/User/globalStorage/state.vscdb` into a scratch vault → genuine user prompts written, and no assistant/tool text or attached-file/context-field content leaks. Release (0.15.0) is a separate follow-up.
