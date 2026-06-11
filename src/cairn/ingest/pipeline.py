# src/cairn/ingest/pipeline.py
# SPDX-License-Identifier: Apache-2.0
"""Ingest orchestrator. Enforces the mandatory pipeline order (spec §9):
redact -> dedup -> importance gate -> distill -> write. Redaction is FIRST so no
unredacted secret is ever hashed or written. Candidates are selected structurally:
only genuinely-authored user events (EventKind.AUTHORED_USER) qualify."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from pathlib import Path

from cairn.ingest.dedup import DedupLedger, content_hash
from cairn.ingest.distill import Distiller, ExtractiveDistiller, write_derived_note
from cairn.ingest.events import EventKind
from cairn.ingest.importance import KEEP_THRESHOLD, is_important
from cairn.ingest.models import Candidate, IngestReport, Transcript
from cairn.ingest.redact import redact


def select_candidates(transcript: Transcript) -> list[Candidate]:
    """One candidate per genuinely-authored user event. Everything else (tool
    results, meta injections, summaries, assistant turns) is excluded by kind."""
    return [
        Candidate(
            text=e.text,
            session_id=e.session_id or transcript.session_id,
            cwd=transcript.cwd,
            git_branch=e.git_branch,
            timestamp=e.timestamp,
            source_path=e.source_path,
            project=e.project,
        )
        for e in transcript.events
        if e.kind == EventKind.AUTHORED_USER
    ]


def ingest_transcript(
    transcript: Transcript,
    *,
    vault_root: Path,
    ledger: DedupLedger,
    threshold: float = KEEP_THRESHOLD,
    distiller: Distiller | None = None,
    subdir: str = "memories",
    dry_run: bool = False,
) -> IngestReport:
    distiller = distiller or ExtractiveDistiller()
    report = IngestReport()
    report.event_kinds = dict(Counter(e.kind.value for e in transcript.events))
    candidates = select_candidates(transcript)
    report.authored = len(candidates)
    for cand in candidates:
        # 1. REDACT FIRST — everything downstream sees only redacted text.
        red = redact(cand.text)
        report.redactions += red.count
        cand = replace(cand, text=red.text)

        # 2. DEDUP on the redacted content (spec §9: dedup before gate).
        h = content_hash(cand.text)
        if ledger.seen(h):
            report.deduped += 1
            continue

        # 3. IMPORTANCE GATE.
        if not is_important(cand.text, threshold=threshold):
            report.gated_out += 1
            continue

        report.candidates += 1

        # 4. DISTILL (non-lossy).
        note = distiller.distill(cand)

        # 5. WRITE (skipped on dry-run; ledger untouched on dry-run).
        if dry_run:
            continue
        path = write_derived_note(note, vault_root, subdir=subdir)
        ledger.add(h)
        report.written.append(path)
    return report
