# src/cairn/ingest/harness/__init__.py
# SPDX-License-Identifier: Apache-2.0
"""Harness adapter seam. One adapter per agent harness; everything
harness-specific (transcript location, container format, structural
classification) lives behind a HarnessAdapter. The ingest pipeline downstream
consumes NormalizedEvents identically regardless of origin.

Classification stays positive-identification and fail-closed per harness: a row
not affirmatively recognized as authored user prose never becomes a candidate."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from cairn.ingest.events import EventKind, NormalizedEvent


@dataclass(frozen=True)
class TranscriptRef:
    """A transcript path tagged with the harness that produced it, so a
    cross-harness (auto-detect) sweep can route each path back to its adapter."""

    path: Path
    harness: str


@dataclass
class ParseCtx:
    """Mutable per-file context an adapter fills in as it scans a transcript:
    session id / cwd / git branch discovered from a header row or per-row fields.
    `path` is the transcript path (for NormalizedEvent.source_path)."""

    path: Path
    session_id: str | None = None
    cwd: str | None = None
    git_branch: str | None = None


@runtime_checkable
class HarnessAdapter(Protocol):
    name: str

    def default_root(self) -> Path: ...
    def is_present(self) -> bool: ...
    def find(self, *, root: Path | None, project: str | None) -> list[Path]: ...
    def iter_raw(self, path: Path) -> Iterator[dict]: ...
    def classify(self, raw: dict) -> EventKind: ...
    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None: ...


# Populated by adapter modules at import time (see _register below).
REGISTRY: dict[str, HarnessAdapter] = {}


def _register(adapter: HarnessAdapter) -> None:
    REGISTRY[adapter.name] = adapter


def get_adapter(name: str) -> HarnessAdapter:
    """Resolve a harness name to its adapter; ValueError lists known names."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(f"unsupported harness: {name!r} (have: {sorted(REGISTRY)})") from None


def present_harnesses(selected: list[str] | None = None) -> list[HarnessAdapter]:
    """Adapters whose root currently exists. `selected` (from --harness /
    CAIRN_HARNESSES) narrows and validates names; None means 'all registered'.
    Unknown names raise ValueError (via get_adapter)."""
    names = selected if selected is not None else list(REGISTRY)
    return [a for a in (get_adapter(n) for n in names) if a.is_present()]
