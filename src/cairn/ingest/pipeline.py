# src/cairn/ingest/pipeline.py
# SPDX-License-Identifier: Apache-2.0
"""Ingest orchestrator. Enforces the mandatory pipeline order (spec §9):
redact -> dedup -> importance gate -> distill -> write. Redaction is FIRST so no
unredacted secret is ever hashed or written."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cairn.ingest.dedup import DedupLedger, content_hash
from cairn.ingest.distill import Distiller, ExtractiveDistiller, write_derived_note
from cairn.ingest.importance import KEEP_THRESHOLD, is_important
from cairn.ingest.models import Candidate, IngestReport, Transcript
from cairn.ingest.redact import redact
from cairn.ingest.sanitize import is_framing_noise


def _candidates(transcript: Transcript) -> list[Candidate]:
    """v1 segmentation: one candidate per real user turn. Harness-injected framing
    (slash-command output, tool dumps, compaction summaries) is dropped — it's not
    user prose and must not become a memory."""
    return [
        Candidate(
            text=t.text,
            session_id=transcript.session_id,
            cwd=transcript.cwd,
            git_branch=transcript.git_branch,
            timestamp=t.timestamp,
            source_path=transcript.path,
        )
        for t in transcript.turns
        if t.role == "user" and not is_framing_noise(t.text)
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
    for cand in _candidates(transcript):
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
