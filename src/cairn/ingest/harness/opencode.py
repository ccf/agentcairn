# SPDX-License-Identifier: Apache-2.0
"""OpenCode adapter: reads sessions from a WAL-mode SQLite DB at
$OPENCODE_DATA_DIR/opencode.db (or ~/.local/share/opencode/opencode.db).

Schema (OpenCode 1.17.5+):
  session(id, project_id, directory)
  message(id, session_id, time_created, time_updated, data TEXT)
  part(id, message_id, session_id, time_created, data TEXT)

message.data and part.data are JSON blobs. Text parts have type=="text".
Positive-ID, fail-closed: only a user message with non-empty text parts
is AUTHORED_USER."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from urllib.request import pathname2url

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_MSG_SESSION_SQL = """
    SELECT m.id, m.session_id, m.data, s.directory
    FROM message m
    LEFT JOIN session s ON m.session_id = s.id
    ORDER BY m.session_id, m.time_created
"""

_PART_SQL = "SELECT data FROM part WHERE message_id=? ORDER BY time_created"


def _roots() -> list[Path]:
    raw = os.environ.get("OPENCODE_DATA_DIR")
    if raw:
        return [Path(p) for p in raw.split(",")]
    return [Path.home() / ".local" / "share" / "opencode"]


def _db_for_base(base: Path) -> Path:
    return base / "opencode.db"


class OpenCodeAdapter:
    name = "opencode"

    def default_root(self) -> Path:
        return _roots()[0]

    def is_present(self) -> bool:
        return any(_db_for_base(b).is_file() for b in _roots())

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        # `project` is intentionally ignored (like CursorAdapter): per-session
        # project is only known post-parse (from session.directory in to_event),
        # so it cannot be used to filter at find time.
        if root is not None:
            bases = [Path(root)]
        else:
            bases = _roots()
        out: list[Path] = []
        for base in bases:
            db = _db_for_base(base)
            if db.is_file():
                out.append(db)
        return out

    def iter_raw(self, path: Path) -> Iterator[dict]:
        # WAL-mode requires mode=ro (NOT immutable=1 — immutable ignores the WAL)
        try:
            con = sqlite3.connect(f"file:{pathname2url(str(path))}?mode=ro", uri=True)
        except sqlite3.Error:
            return  # unreadable DB → no rows
        try:
            try:
                cur = con.execute(_MSG_SESSION_SQL)
            except sqlite3.Error:
                return  # missing table / old schema → no rows
            for msg_id, session_id, data_json, directory in cur:
                # Parse message JSON — skip malformed rows
                try:
                    msg_data = json.loads(data_json)
                except (json.JSONDecodeError, ValueError, TypeError):
                    continue
                if not isinstance(msg_data, dict):
                    continue

                # Fetch and join text parts
                text = _collect_text(con, msg_id)

                row = dict(msg_data)
                row["_text"] = text
                row["_session_id"] = session_id
                row["_cwd"] = directory  # may be None if LEFT JOIN found nothing
                yield row
        finally:
            con.close()

    def classify(self, raw: dict) -> EventKind:
        role = raw.get("role")
        if role == "user" and raw.get("_text"):
            return EventKind.AUTHORED_USER
        if role == "assistant":
            return EventKind.AUTHORED_ASSISTANT
        return EventKind.UNKNOWN

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        text = raw.get("_text") or ""
        if kind == EventKind.AUTHORED_USER and not text:
            return None
        time_obj = raw.get("time")
        ts = time_obj.get("created") if isinstance(time_obj, dict) else None
        cwd = raw.get("_cwd")
        return NormalizedEvent(
            kind=kind,
            role=raw.get("role") or "user",
            text=text,
            timestamp=str(ts) if ts is not None else None,
            session_id=raw.get("_session_id") or ctx.path.stem,
            project=project_from_cwd(cwd if isinstance(cwd, str) else None),
            git_branch=None,
            source_path=ctx.path,
            harness=self.name,
        )


def _collect_text(con: sqlite3.Connection, msg_id: str) -> str:
    """Fetch all text parts for a message and return sanitized concatenation.

    Uses its own cursor over the connection shared with iter_raw's outer cursor;
    SQLite supports multiple independent cursors on a single connection.
    """
    try:
        cur = con.execute(_PART_SQL, (msg_id,))
    except sqlite3.Error:
        return ""
    chunks: list[str] = []
    for (data_json,) in cur:
        try:
            part = json.loads(data_json)
        except (json.JSONDecodeError, ValueError, TypeError):
            continue  # malformed part → skip
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return sanitize_text("".join(chunks)).strip()
