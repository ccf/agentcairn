# src/cairn/ingest/harness/antigravity.py
# SPDX-License-Identifier: Apache-2.0
"""Antigravity adapter: ~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/
logs/transcript.jsonl (one JSON object per line). Antigravity CLI replaces Gemini
CLI (sunset 2026-06-18) and is both a desktop app and a CLI.

Positive-ID, fail-closed: only a USER_INPUT step's <USER_REQUEST> block is authored
user prose; injected <ADDITIONAL_METADATA>/<USER_SETTINGS_CHANGE> framing is dropped."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_AGY_ROOT = Path.home() / ".gemini" / "antigravity-cli" / "brain"

# Inner text of the first <USER_REQUEST>...</USER_REQUEST> block. Everything outside
# it (ADDITIONAL_METADATA, USER_SETTINGS_CHANGE, ...) is injected framing to drop.
_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _user_request(content: object) -> str:
    """The authored prose of a USER_INPUT step: inner text of the first
    <USER_REQUEST> block, sanitized. '' if absent/empty/non-string."""
    if not isinstance(content, str):
        return ""
    m = _USER_REQUEST_RE.search(content)
    if not m:
        return ""
    return sanitize_text(m.group(1)).strip()


def _conversation_cwd(brain_root: Path) -> dict[str, str]:
    """Reverse of cache/last_conversations.json ({cwd: uuid}) -> {uuid: cwd},
    best-effort. Empty dict on any error. cache/ is a sibling of brain/."""
    cache = brain_root.parent / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        uuid: cwd for cwd, uuid in data.items() if isinstance(cwd, str) and isinstance(uuid, str)
    }


def _uuid_of(path: Path) -> str:
    """Conversation uuid for brain/<uuid>/.system_generated/logs/transcript.jsonl."""
    return path.parent.parent.parent.name


def _brain_root(path: Path) -> Path:
    """The brain/ root for brain/<uuid>/.system_generated/logs/transcript.jsonl.
    Uses a .parent chain (never IndexErrors on a short path, unlike parents[3])."""
    return path.parent.parent.parent.parent


class AntigravityAdapter:
    name = "antigravity"

    def default_root(self) -> Path:
        return _AGY_ROOT

    def is_present(self) -> bool:
        return self.default_root().is_dir()

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        base = Path(root) if root is not None else self.default_root()
        if not base.is_dir():
            return []
        files = list(base.glob("*/.system_generated/logs/transcript.jsonl"))
        if project is not None:
            target = project.rstrip("/") or "/"
            uuid_cwd = _conversation_cwd(base)
            files = [f for f in files if (uuid_cwd.get(_uuid_of(f)) or "").rstrip("/") == target]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def iter_raw(self, path: Path) -> Iterator[dict]:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue  # partial/corrupt line
            if isinstance(obj, dict):
                yield obj

    def classify(self, raw: dict) -> EventKind:
        t = raw.get("type")
        if t == "USER_INPUT":
            if raw.get("source") != "USER_EXPLICIT":
                return EventKind.META_INJECTION  # system-injected user step
            text = _user_request(raw.get("content")).lstrip()
            if not text or text.startswith("/"):
                return EventKind.META_INJECTION  # empty or slash-command
            return EventKind.AUTHORED_USER
        if t == "PLANNER_RESPONSE":
            return EventKind.AUTHORED_ASSISTANT
        return EventKind.UNKNOWN

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        if ctx.session_id is None:
            # Resolve session + cwd once per file (uuid from the path; cwd best-effort).
            ctx.session_id = _uuid_of(ctx.path)
            ctx.cwd = _conversation_cwd(_brain_root(ctx.path)).get(ctx.session_id)
        t = raw.get("type")
        if t == "USER_INPUT":
            text = _user_request(raw.get("content"))
            role = "user"
        elif t == "PLANNER_RESPONSE":
            content = raw.get("content")
            text = sanitize_text(content).strip() if isinstance(content, str) else ""
            role = "assistant"
        else:
            return None
        if not text:
            return None
        return NormalizedEvent(
            kind=kind,
            role=role,
            text=text,
            timestamp=raw.get("created_at"),
            session_id=ctx.session_id,
            project=project_from_cwd(ctx.cwd),
            git_branch=None,  # Antigravity transcripts carry no git branch
            source_path=ctx.path,
            harness=self.name,
        )
