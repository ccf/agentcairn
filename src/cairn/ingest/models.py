# src/cairn/ingest/models.py
# SPDX-License-Identifier: Apache-2.0
"""Shared value types for the ingest pipeline. Keep these signatures stable —
the pipeline, CLI, and (later) MCP all consume them."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from cairn.ingest.events import NormalizedEvent


@dataclass
class Transcript:
    session_id: str
    cwd: str | None
    git_branch: str | None
    path: Path
    events: list[NormalizedEvent] = field(default_factory=list)


@dataclass
class Candidate:
    """One unit considered for distillation, with provenance back to its origin."""

    text: str
    session_id: str
    cwd: str | None
    git_branch: str | None
    timestamp: str | None
    source_path: Path
    project: str | None = None  # origin project identity (provenance plumbing for #28)


@dataclass
class RedactionResult:
    text: str  # redacted text (safe to hash/write)
    count: int  # number of redactions applied
    kinds: list[str] = field(default_factory=list)  # which detectors fired


@dataclass
class IngestReport:
    candidates: int = 0
    redactions: int = 0
    deduped: int = 0  # skipped as already-seen
    gated_out: int = 0  # below importance threshold
    authored: int = 0  # AUTHORED_USER events selected before redact/dedup/gate
    event_kinds: dict[str, int] = field(default_factory=dict)  # all event kinds seen
    written: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation (Paths stringified)."""
        return {
            "candidates": self.candidates,
            "redactions": self.redactions,
            "deduped": self.deduped,
            "gated_out": self.gated_out,
            "authored": self.authored,
            "event_kinds": self.event_kinds,
            "written": [str(p) for p in self.written],
        }
