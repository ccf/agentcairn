# src/cairn/ingest/locate.py
# SPDX-License-Identifier: Apache-2.0
"""Locate and parse harness transcripts out-of-band. v1 supports the Claude Code
layout only, but the API is dispatch-shaped for future harnesses (Codex/Cursor/Gemini).

Transcripts are append-only jsonl; we read without locking and skip any malformed
line (the last line may be partially written)."""

from __future__ import annotations

import json
from pathlib import Path

from cairn.ingest.events import EventKind
from cairn.ingest.models import Transcript, Turn
from cairn.ingest.sanitize import sanitize_text

_CLAUDE_ROOT = Path.home() / ".claude" / "projects"
_CONTENT_TYPES = {"user", "assistant"}


def encode_cwd(cwd: str) -> str:
    """Claude Code encodes a project dir by replacing every '/' with '-'.
    e.g. '/Users/ccf/git/agentcairn' -> '-Users-ccf-git-agentcairn'. Trailing
    slashes are stripped first (Claude Code's cwd never has one), so a `--project`
    given as '/Users/x/proj/' maps to the same directory as '/Users/x/proj'."""
    normalized = cwd.rstrip("/") or "/"
    return normalized.replace("/", "-")


def find_transcripts(
    *, harness: str = "claude-code", root: Path | None = None, project: str | None = None
) -> list[Path]:
    """Return jsonl transcript paths for a harness, newest first. Graceful: a
    missing root yields []. `project` (an absolute cwd) restricts to that project's
    encoded directory."""
    if harness != "claude-code":
        raise ValueError(f"unsupported harness: {harness!r} (v1 supports 'claude-code')")
    base = Path(root) if root is not None else _CLAUDE_ROOT
    if not base.is_dir():
        return []
    if project is not None:
        dirs = [base / encode_cwd(project)]
    else:
        dirs = [d for d in base.iterdir() if d.is_dir()]
    files = [f for d in dirs if d.is_dir() for f in d.glob("*.jsonl")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return files


def _extract_text(content: object) -> str:
    """User content is a str; assistant content is a list of blocks. Keep only
    plain text (drop thinking/tool_use/tool_result). Terminal escape sequences and
    stray control bytes are stripped so they never reach the vault."""
    if isinstance(content, str):
        return sanitize_text(content).strip()
    if isinstance(content, list):
        parts = [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return sanitize_text("\n".join(parts)).strip()
    return ""


def classify_claude_code(obj: dict) -> EventKind:
    """Positive-identification, fail-closed classification of a raw Claude Code
    JSONL entry. A user turn is AUTHORED_USER only when it carries NONE of the
    harness's injection markers. Order matters: compact-summary first (it also
    sets isVisibleInTranscriptOnly), then tool results, then meta/injected."""
    t = obj.get("type")
    if t == "user":
        if obj.get("isCompactSummary"):
            return EventKind.COMPACT_SUMMARY
        if "toolUseResult" in obj:
            return EventKind.TOOL_RESULT
        if obj.get("isMeta") or obj.get("isVisibleInTranscriptOnly") or obj.get("origin"):
            return EventKind.META_INJECTION
        return EventKind.AUTHORED_USER
    if t == "assistant":
        return EventKind.AUTHORED_ASSISTANT
    if t == "system":
        return EventKind.SYSTEM
    return EventKind.UNKNOWN


def parse_transcript(path: Path) -> Transcript:
    """Parse a jsonl transcript into a Transcript. Skips metadata lines and any
    line that fails to parse. Session-level provenance is taken from the first
    content line that carries it."""
    session_id = path.stem
    cwd: str | None = None
    git_branch: str | None = None
    turns: list[Turn] = []
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue  # partial/corrupt line — transcripts are append-only
        if not isinstance(obj, dict):
            continue
        if obj.get("type") not in _CONTENT_TYPES:
            continue
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        text = _extract_text(msg.get("content"))
        if not text:
            continue
        # Provenance comes from the first ACCEPTED content turn, not from a
        # content-typed row we end up skipping (no message / empty text).
        if session_id == path.stem:
            session_id = obj.get("sessionId") or session_id
        if cwd is None:
            cwd = obj.get("cwd")
        if git_branch is None:
            git_branch = obj.get("gitBranch")
        turns.append(
            Turn(role=msg.get("role", obj["type"]), text=text, timestamp=obj.get("timestamp"))
        )
    return Transcript(session_id=session_id, cwd=cwd, git_branch=git_branch, path=path, turns=turns)
