# src/cairn/ingest/from_messages.py
# SPDX-License-Identifier: Apache-2.0
"""Build an agentcairn Transcript from an in-memory message list (e.g. a Hermes
Agent conversation), so it can flow through the normal ingest/distill pipeline."""

from __future__ import annotations

from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.models import Transcript

_ROLE_KIND = {
    "user": EventKind.AUTHORED_USER,
    "assistant": EventKind.AUTHORED_ASSISTANT,
}


def _text_of(content) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict):
                parts.append(str(p.get("text") or p.get("content") or ""))
            else:
                parts.append(str(p))
        return "".join(parts).strip()
    return str(content or "").strip()


def transcript_from_messages(
    messages: list[dict],
    *,
    session_id: str,
    cwd: str | None = None,
    source_path: Path | None = None,
    harness: str = "hermes",
) -> Transcript:
    src = source_path or Path(f"hermes:{session_id}")
    events: list[NormalizedEvent] = []
    counts: dict[str, int] = {}
    for m in messages:
        role = str(m.get("role", "")).lower()
        kind = _ROLE_KIND.get(role, EventKind.SYSTEM)
        text = _text_of(m.get("content"))
        if not text:
            continue
        counts[kind.value] = counts.get(kind.value, 0) + 1
        events.append(
            NormalizedEvent(
                kind=kind,
                role=role or "user",
                text=text,
                timestamp=m.get("timestamp"),
                session_id=session_id,
                project=project_from_cwd(cwd),
                git_branch=None,
                source_path=src,
                harness=harness,
            )
        )
    return Transcript(
        session_id=session_id,
        cwd=cwd,
        git_branch=None,
        path=src,
        events=events,
        kind_counts=counts,
    )
