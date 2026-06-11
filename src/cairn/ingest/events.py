# src/cairn/ingest/events.py
# SPDX-License-Identifier: Apache-2.0
"""Harness-agnostic normalized transcript events.

Each harness adapter classifies its native entries into one of these kinds; the
pipeline distills only AUTHORED_USER. Classification is positive-identification
and fail-closed: anything not affirmatively recognized as authored prose is some
other kind (ultimately UNKNOWN) and never becomes a candidate."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class EventKind(StrEnum):
    AUTHORED_USER = "authored_user"  # the ONLY candidate source (Layer A)
    AUTHORED_ASSISTANT = "authored_assistant"  # retained in stream, not a candidate
    TOOL_RESULT = "tool_result"
    META_INJECTION = (
        "meta_injection"  # slash-command markers, skill bodies, hooks, task-notifications
    )
    COMPACT_SUMMARY = "compact_summary"
    SYSTEM = "system"
    UNKNOWN = "unknown"  # fail-closed bucket -> never a candidate


@dataclass(frozen=True)
class NormalizedEvent:
    kind: EventKind
    role: str
    text: str  # sanitized at parse
    timestamp: str | None
    # provenance (plumbing for #28; carried, not yet written to frontmatter)
    session_id: str | None
    project: str | None  # origin project identity, derived from cwd
    git_branch: str | None
    source_path: Path


def project_from_cwd(cwd: str | None) -> str | None:
    """Origin 'project' identity for provenance: the final path segment of cwd
    (the repo / working-dir name). None for missing/empty/root cwd."""
    if not cwd:
        return None
    return Path(cwd).name or None
