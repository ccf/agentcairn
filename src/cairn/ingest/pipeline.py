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
from cairn.ingest.importance import KEEP_THRESHOLD, score
from cairn.ingest.judge import Judge
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


def _judge_tier_name(judge: Judge | None) -> str:
    if judge is None:
        return "none"
    from cairn.ingest.judge import EmbeddingJudge, LLMJudge

    if isinstance(judge, LLMJudge):
        return "llm"
    if isinstance(judge, EmbeddingJudge):
        return "embedding"
    return type(judge).__name__.lower()


def ingest_transcripts(
    transcripts: list[Transcript],
    *,
    vault_root: Path,
    ledger: DedupLedger,
    threshold: float = KEEP_THRESHOLD,
    judge: Judge | None = None,
    distiller: Distiller | None = None,
    subdir: str = "memories",
    dry_run: bool = False,
) -> IngestReport:
    """Ingest a batch of transcripts with ONE judge call across all new candidates.
    Order per spec: redact -> dedup -> judge (batched) -> combined gate -> distill -> write."""
    distiller = distiller or ExtractiveDistiller()
    report = IngestReport()
    report.judge_tier = _judge_tier_name(judge)
    kind_totals: Counter = Counter()

    # Phase A: collect redacted, deduped candidates across all transcripts.
    pending: list[tuple[Candidate, str]] = []  # (candidate, content hash)
    seen_this_run: set[str] = set()
    for transcript in transcripts:
        kind_totals.update(
            transcript.kind_counts or Counter(e.kind.value for e in transcript.events)
        )
        candidates = select_candidates(transcript)
        report.authored += len(candidates)
        for cand in candidates:
            red = redact(cand.text)
            report.redactions += red.count
            cand = replace(cand, text=red.text)
            h = content_hash(cand.text)
            if ledger.seen(h) or h in seen_this_run:
                report.deduped += 1
                continue
            seen_this_run.add(h)
            pending.append((cand, h))
    report.event_kinds = dict(kind_totals)

    # Phase B: ONE batched judge call (never raises; LLM degrades internally).
    judgments = judge.judge([c.text for c, _ in pending]) if judge and pending else []
    if judge is not None and hasattr(judge, "degraded"):
        report.judge_degraded = judge.degraded

    # Phase C: combined gate -> distill -> write.
    for idx, (cand, h) in enumerate(pending):
        heuristic = score(cand.text)
        if judgments:
            j = judgments[idx]
            combined = max(0.0, min(1.0, 0.5 * heuristic + 0.5 * j.durability))
            cand = replace(cand, judgment=j, importance=combined)
        else:
            combined = heuristic
            cand = replace(cand, importance=combined)
        if combined < threshold:
            report.gated_out += 1
            continue
        report.candidates += 1
        note = distiller.distill(cand)
        if dry_run:
            continue
        path = write_derived_note(note, vault_root, subdir=subdir)
        ledger.add(h)
        report.written.append(path)
    return report


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
    """Single-transcript wrapper (kept for API compatibility; judge-less)."""
    return ingest_transcripts(
        [transcript],
        vault_root=vault_root,
        ledger=ledger,
        threshold=threshold,
        judge=None,
        distiller=distiller,
        subdir=subdir,
        dry_run=dry_run,
    )
