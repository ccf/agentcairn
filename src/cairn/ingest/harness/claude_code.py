# src/cairn/ingest/harness/claude_code.py
# SPDX-License-Identifier: Apache-2.0
"""Claude Code adapter: ~/.claude/projects/<encoded-cwd>/<session>.jsonl.

Classification is positive-identification and fail-closed: a user turn is
AUTHORED_USER only when it carries NONE of the harness's injection markers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_CLAUDE_ROOT = Path.home() / ".claude" / "projects"
_CONTENT_TYPES = {"user", "assistant"}

# Backstop for legacy transcripts (Claude Code <=2.1.150): injected slash-command
# and tool rows carried NO structural flags, so they are structurally identical to
# authored prose. Structure stays primary; this prefix list is ONLY for rows with
# no markers, and lists the harness's own injection tags — never user vocabulary.
_LEGACY_TAG_PREFIXES = (
    "<command-",
    "<local-command",
    "<bash-input",
    "<bash-stdout",
    "<bash-stderr",
    "<task-notification",
    "<system-reminder",
    "<user-prompt-submit-hook",
)


def encode_cwd(cwd: str) -> str:
    """Claude Code encodes a project dir by replacing every '/' with '-'.
    e.g. '/Users/ccf/git/agentcairn' -> '-Users-ccf-git-agentcairn'. Trailing
    slashes are stripped first, so '/Users/x/proj/' maps to the same dir as
    '/Users/x/proj'."""
    normalized = cwd.rstrip("/") or "/"
    return normalized.replace("/", "-")


def _extract_text(content: object) -> str:
    """User content is a str; assistant content is a list of blocks. Keep only
    plain text (drop thinking/tool_use/tool_result). Terminal escape sequences
    and stray control bytes are stripped so they never reach the vault."""
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
    """Positive-ID, fail-closed classification of a raw Claude Code JSONL entry.
    Order matters: compact-summary first, then tool results, then meta/injected.
    A tag-prefix backstop covers legacy transcripts whose injected rows predate
    the structural flags."""
    t = obj.get("type")
    if t == "user":
        if obj.get("isCompactSummary"):
            return EventKind.COMPACT_SUMMARY
        if "toolUseResult" in obj:
            return EventKind.TOOL_RESULT
        if obj.get("isMeta") or obj.get("isVisibleInTranscriptOnly") or obj.get("origin"):
            return EventKind.META_INJECTION
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and sanitize_text(content).lstrip().startswith(
            _LEGACY_TAG_PREFIXES
        ):
            return EventKind.META_INJECTION
        return EventKind.AUTHORED_USER
    if t == "assistant":
        return EventKind.AUTHORED_ASSISTANT
    if t == "system":
        return EventKind.SYSTEM
    return EventKind.UNKNOWN


class ClaudeCodeAdapter:
    name = "claude-code"

    def default_root(self) -> Path:
        return _CLAUDE_ROOT

    def is_present(self) -> bool:
        return self.default_root().is_dir()

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        base = Path(root) if root is not None else self.default_root()
        if not base.is_dir():
            return []
        if project is not None:
            dirs = [base / encode_cwd(project)]
        else:
            dirs = [d for d in base.iterdir() if d.is_dir()]
        files = [f for d in dirs if d.is_dir() for f in d.glob("*.jsonl")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def iter_raw(self, path: Path) -> Iterator[dict]:
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
                continue  # only user/assistant rows carry conversational content
            yield obj

    def classify(self, raw: dict) -> EventKind:
        return classify_claude_code(raw)

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        msg = raw.get("message")
        if not isinstance(msg, dict):
            return None
        text = _extract_text(msg.get("content"))
        if not text:
            return None  # a skipped row must not set provenance
        if ctx.session_id is None:
            ctx.session_id = raw.get("sessionId")
        line_cwd = raw.get("cwd")
        if ctx.cwd is None:
            ctx.cwd = line_cwd
        if ctx.git_branch is None:
            ctx.git_branch = raw.get("gitBranch")
        return NormalizedEvent(
            kind=kind,
            role=msg.get("role", raw["type"]),
            text=text,
            timestamp=raw.get("timestamp"),
            session_id=raw.get("sessionId") or ctx.session_id or ctx.path.stem,
            project=project_from_cwd(line_cwd or ctx.cwd),
            git_branch=raw.get("gitBranch") or ctx.git_branch,
            source_path=ctx.path,
            harness=self.name,
        )
