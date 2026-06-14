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
# json_valid(value) MUST precede json_extract: SQLite evaluates WHERE terms
# left-to-right on a table scan, so it short-circuits malformed values before
# json_extract can raise (one corrupt bubble would otherwise abort all ingestion).
_USER_BUBBLE_SQL = (
    "SELECT key, value FROM cursorDiskKV "
    "WHERE key LIKE 'bubbleId:%' "
    "AND json_valid(value) "
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
        ts = raw.get("createdAt")
        return NormalizedEvent(
            kind=kind,
            role="user",
            text=text,
            timestamp=ts if isinstance(ts, str) else None,  # non-str createdAt → None (safe sort)
            session_id=raw.get("_composer_id") or ctx.path.stem,
            project=project_from_cwd(raw.get("workspaceProjectDir")),
            git_branch=None,  # Cursor bubbles carry no git branch
            source_path=ctx.path,
            harness=self.name,
        )
