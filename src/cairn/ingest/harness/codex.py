# src/cairn/ingest/harness/codex.py
# SPDX-License-Identifier: Apache-2.0
"""Codex adapter: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.

Each line is {type, payload, timestamp}. Conversational content lives in
`response_item` rows; `session_meta`/`turn_context` seed session/cwd; `event_msg`
is UI noise. Classification is positive-ID and fail-closed: a role=user message
is AUTHORED_USER only when it does NOT start with an injected harness block
(# AGENTS.md, <INSTRUCTIONS>, ...) — Codex laces real user turns with those."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_CODEX_ROOT = Path.home() / ".codex" / "sessions"

# response_item payload.type values that are tool I/O (role is None).
_TOOL_TYPES = {
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "web_search_call",
}

# Top-level types that only seed context / are UI bookkeeping (never candidates).
_BOOKKEEPING_TYPES = {"session_meta", "turn_context", "event_msg"}

# Tag-backstop: blocks Codex injects into role=user messages. Positive-ID prose
# only — anything starting with one of these is harness-injected, not authored.
_CODEX_TAG_PREFIXES = (
    "# AGENTS.md",
    "<INSTRUCTIONS>",
    "<turn_aborted",
    "<user_instructions",
    "<environment_context",
)


def _payload(raw: dict) -> dict:
    p = raw.get("payload")
    return p if isinstance(p, dict) else {}


def _payload_cwd(payload: dict) -> str | None:
    """The payload's cwd only when it is a string. A non-string (or missing) cwd
    becomes None so it can't crash `.rstrip()` / `Path()` downstream — defensive,
    since a transcript's schema is not trusted to describe its older rows."""
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) else None


def _extract_codex_text(payload: dict) -> str:
    """Join the input_text/output_text blocks of a Codex message payload,
    sanitized. Other block types are dropped."""
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        return ""
    parts = [
        b["text"]
        for b in blocks
        if isinstance(b, dict)
        and b.get("type") in ("input_text", "output_text", "text")
        and isinstance(b.get("text"), str)
    ]
    return sanitize_text("\n".join(parts)).strip()


class CodexAdapter:
    name = "codex"

    def default_root(self) -> Path:
        return _CODEX_ROOT

    def is_present(self) -> bool:
        return self.default_root().is_dir()

    def _session_cwd(self, path: Path) -> str | None:
        """Read the session_meta cwd from a transcript's header. Streams line by
        line and stops at the first session_meta row (it is the first line of a
        rollout), so it never loads the whole transcript. None if absent/unreadable."""
        try:
            with path.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(obj, dict) and obj.get("type") == "session_meta":
                        return _payload_cwd(_payload(obj))
        except OSError:
            return None
        return None

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        base = Path(root) if root is not None else self.default_root()
        if not base.is_dir():
            return []
        files = list(base.rglob("rollout-*.jsonl"))
        if project is not None:
            target = project.rstrip("/") or "/"
            files = [f for f in files if (self._session_cwd(f) or "").rstrip("/") == target]
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
                continue  # partial/corrupt line — transcripts are append-only
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            if t == "event_msg":
                continue  # UI/turn bookkeeping — carries no ctx and no candidate
            if t == "response_item" or t in ("session_meta", "turn_context", "compacted"):
                yield obj

    def classify(self, raw: dict) -> EventKind:
        t = raw.get("type")
        if t == "compacted":
            return EventKind.COMPACT_SUMMARY
        if t in _BOOKKEEPING_TYPES:
            return EventKind.SYSTEM
        if t == "response_item":
            p = _payload(raw)
            pt = p.get("type")
            if pt in _TOOL_TYPES:
                return EventKind.TOOL_RESULT
            if pt == "reasoning":
                return EventKind.AUTHORED_ASSISTANT
            if pt == "message":
                role = p.get("role")
                if role == "assistant":
                    return EventKind.AUTHORED_ASSISTANT
                if role == "developer":
                    return EventKind.META_INJECTION
                if role == "user":
                    text = _extract_codex_text(p).lstrip()
                    if text.startswith(_CODEX_TAG_PREFIXES):
                        return EventKind.META_INJECTION
                    return EventKind.AUTHORED_USER
            return EventKind.UNKNOWN
        return EventKind.UNKNOWN

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        t = raw.get("type")
        if t == "session_meta":
            p = _payload(raw)
            if ctx.session_id is None:
                ctx.session_id = p.get("id")
            if ctx.cwd is None:
                ctx.cwd = _payload_cwd(p)
            return None
        if t == "turn_context":
            if ctx.cwd is None:
                ctx.cwd = _payload_cwd(_payload(raw))
            return None
        if t != "response_item":
            return None  # compacted: counted in kind_counts, not a candidate
        p = _payload(raw)
        if p.get("type") != "message":
            return None  # reasoning/tool I/O retained in counts, no candidate text
        text = _extract_codex_text(p)
        if not text:
            return None
        return NormalizedEvent(
            kind=kind,
            role=p.get("role") or "user",
            text=text,
            timestamp=raw.get("timestamp"),
            session_id=ctx.session_id or ctx.path.stem,
            project=project_from_cwd(ctx.cwd),
            git_branch=None,  # Codex transcripts carry no git branch
            source_path=ctx.path,
            harness=self.name,
        )
