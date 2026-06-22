# SPDX-License-Identifier: Apache-2.0
"""OpenCode adapter: $OPENCODE_DATA_DIR (or ~/.local/share/opencode)/storage.
A session = message/<sessionID>/; each message/<mid>.json joins its text parts
from part/<mid>/*.json. Positive-ID, fail-closed: only a user message's text
parts are authored prose."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text


def _roots() -> list[Path]:
    raw = os.environ.get("OPENCODE_DATA_DIR")
    if raw:
        bases = [Path(p) for p in raw.split(",")]
    else:
        bases = [Path.home() / ".local" / "share" / "opencode"]
    return [b / "storage" for b in bases]


def _load(p: Path) -> dict | None:
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def _message_text(storage: Path, mid: str) -> str:
    pdir = storage / "part" / mid
    if not pdir.is_dir():
        return ""
    chunks: list[str] = []
    for pf in sorted(pdir.glob("*.json")):
        part = _load(pf)
        if part and part.get("type") == "text" and isinstance(part.get("text"), str):
            chunks.append(part["text"])
    return sanitize_text("".join(chunks)).strip()


class OpenCodeAdapter:
    name = "opencode"

    def default_root(self) -> Path:
        return _roots()[0]

    def is_present(self) -> bool:
        return any((r / "message").is_dir() for r in _roots())

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        roots = [Path(root)] if root is not None else _roots()
        out: list[Path] = []
        for storage in roots:
            mdir = storage / "message"
            if mdir.is_dir():
                out.extend(d for d in sorted(mdir.iterdir()) if d.is_dir())
        return out

    def iter_raw(self, path: Path) -> Iterator[dict]:
        storage = path.parent.parent  # storage/message/<sid> -> storage
        for mf in sorted(path.glob("*.json")):
            msg = _load(mf)
            if msg is None:
                continue
            msg["_text"] = _message_text(storage, mf.stem)
            msg["_session_id"] = path.name
            yield msg

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
        return NormalizedEvent(
            kind=kind,
            role=raw.get("role") or "user",
            text=text,
            timestamp=str(ts) if ts is not None else None,
            session_id=raw.get("_session_id") or ctx.path.name,
            project=project_from_cwd(None),
            git_branch=None,
            source_path=ctx.path,
            harness=self.name,
        )
