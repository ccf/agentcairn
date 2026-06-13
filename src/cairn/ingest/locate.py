# src/cairn/ingest/locate.py
# SPDX-License-Identifier: Apache-2.0
"""Locate and parse harness transcripts out-of-band.

Dispatch-shaped: a HarnessAdapter (cairn.ingest.harness) owns each harness's
transcript location, container format, and structural classification. This
module is the stable public entry point — find_transcripts() returns
TranscriptRefs (path + harness) and parse_transcript() routes each to its
adapter. Transcripts are append-only; corrupt/partial lines are skipped."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from cairn.ingest.harness import (
    ParseCtx,
    TranscriptRef,
    get_adapter,
    present_harnesses,
)

# Re-exports for back-compat (tests and callers import these from locate).
from cairn.ingest.harness.claude_code import (  # noqa: F401
    classify_claude_code,
    encode_cwd,
)
from cairn.ingest.models import Transcript


def find_transcripts(
    *,
    harness: str | None = "claude-code",
    root: Path | None = None,
    project: str | None = None,
    harnesses: list[str] | None = None,
) -> list[TranscriptRef]:
    """Return transcript references newest-first.

    - harness=<name> (default "claude-code"): that single harness; `root`
      overrides its default location.
    - harness=None: auto-detect — union of every present harness (or those named
      in `harnesses`). `root` is ignored in auto-detect mode.
    A missing root yields no refs for that harness (graceful, never raises)."""
    if harness is not None:
        adapter = get_adapter(harness)  # ValueError on unknown name
        return [
            TranscriptRef(path=p, harness=adapter.name)
            for p in adapter.find(root=root, project=project)
        ]
    refs: list[TranscriptRef] = []
    for adapter in present_harnesses(harnesses):
        refs += [
            TranscriptRef(path=p, harness=adapter.name)
            for p in adapter.find(root=None, project=project)
        ]
    refs.sort(key=lambda r: r.path.stat().st_mtime, reverse=True)
    return refs


def parse_transcript(ref: TranscriptRef | Path, *, harness: str = "claude-code") -> Transcript:
    """Parse a transcript into a Transcript of NormalizedEvents via its adapter.
    Accepts a TranscriptRef (carries its harness) or a bare Path (back-compat;
    defaults to `harness`). Skips bookkeeping and malformed lines; each content
    row is classified structurally and sanitized; provenance is preserved."""
    if isinstance(ref, TranscriptRef):
        path, name = ref.path, ref.harness
    else:
        path, name = ref, harness
    adapter = get_adapter(name)
    ctx = ParseCtx(path=path)
    events = []
    kind_counts: Counter = Counter()
    for raw in adapter.iter_raw(path):
        kind = adapter.classify(raw)
        kind_counts[kind.value] += 1
        ev = adapter.to_event(raw, kind, ctx)
        if ev is not None:
            events.append(ev)
    return Transcript(
        session_id=ctx.session_id or path.stem,
        cwd=ctx.cwd,
        git_branch=ctx.git_branch,
        path=path,
        events=events,
        kind_counts=dict(kind_counts),
    )
