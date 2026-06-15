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

from cairn.ingest.consolidate import ConsolidationVerdict, Consolidator, NeighborIndex
from cairn.ingest.dedup import DedupLedger, content_hash
from cairn.ingest.distill import Distiller, ExtractiveDistiller, mark_superseded, write_derived_note
from cairn.ingest.events import EventKind
from cairn.ingest.importance import KEEP_THRESHOLD, score
from cairn.ingest.judge import Judge, JudgedCache, Judgment, tier_at_least
from cairn.ingest.models import Candidate, IngestReport, Transcript
from cairn.ingest.redact import redact

_ANTECEDENT_CHARS = 2000  # HEAD-truncate the assistant antecedent: the proposal's
# option list is near the top, and this caps the extra judge-input tokens per turn.


def select_candidates(transcript: Transcript) -> list[Candidate]:
    """One candidate per genuinely-authored user event. Everything else (tool
    results, meta injections, summaries, assistant turns) is excluded by kind.
    Each user candidate also carries its `antecedent`: the nearest preceding
    AUTHORED_ASSISTANT turn in the SAME session (tracked per session, so
    interleaved sessions don't steal each other's context), used downstream only
    as resolution context for the LLM judge — never stored in the note."""
    out: list[Candidate] = []
    last_assistant: dict[str, str] = {}  # session_id -> nearest preceding assistant text
    for e in transcript.events:
        sid = e.session_id or transcript.session_id
        if e.kind == EventKind.AUTHORED_ASSISTANT:
            last_assistant[sid] = e.text
            continue
        if e.kind != EventKind.AUTHORED_USER:
            continue  # tool results / meta / etc. do not clear the antecedent
        antecedent = last_assistant.get(sid)
        out.append(
            Candidate(
                text=e.text,
                session_id=sid,
                cwd=transcript.cwd,
                git_branch=e.git_branch,
                timestamp=e.timestamp,
                source_path=e.source_path,
                project=e.project,
                harness=e.harness,
                antecedent=antecedent,
            )
        )
    return out


def _judge_cache_key(cand: Candidate) -> str:
    """Key for a cached judge verdict: depends on the user turn AND its (redacted)
    antecedent, because the LLM distillation resolves against the antecedent — the
    same turn after a different proposal is a different verdict (Bugbot #64). The
    dedup ledger stays keyed on the turn alone, so re-runs never duplicate an
    already-written note."""
    return content_hash(f"{cand.text}\x00{cand.antecedent or ''}")


def _judge_tier_name(judge: Judge | None) -> str:
    if judge is None:
        return "none"
    from cairn.ingest.judge import EmbeddingJudge, LLMJudge

    if isinstance(judge, LLMJudge):
        return "llm"
    if isinstance(judge, EmbeddingJudge):
        return "embedding"
    return type(judge).__name__.lower()


def _memory_text(cand: Candidate) -> str:
    """Text used to embed/compare a memory: the LLM distillation if present, else
    the candidate's redacted text."""
    j = cand.judgment
    return j.distilled if (j and j.distilled) else cand.text


def _consolidate(cand: Candidate, consolidator: Consolidator, neighbor_index: NeighborIndex):
    """Return (verdict, neighbor). Fail-safe: any error -> (DISTINCT, None)."""
    try:
        hit = neighbor_index.nearest(_memory_text(cand))
        if hit is None:
            return ConsolidationVerdict.DISTINCT, None
        neighbor, _cos = hit
        verdict = consolidator.classify(
            new_text=_memory_text(cand), new_ts=cand.timestamp, neighbor=neighbor
        )
        return verdict, neighbor
    except Exception:
        return ConsolidationVerdict.DISTINCT, None


def ingest_transcripts(
    transcripts: list[Transcript],
    *,
    vault_root: Path,
    ledger: DedupLedger,
    threshold: float = KEEP_THRESHOLD,
    judge: Judge | None = None,
    judged_cache: JudgedCache | None = None,
    distiller: Distiller | None = None,
    subdir: str = "memories",
    dry_run: bool = False,
    consolidator: Consolidator | None = None,
    neighbor_index: NeighborIndex | None = None,
) -> IngestReport:
    """Ingest a batch of transcripts with ONE judge call across all new candidates.
    Order per spec: redact -> dedup -> judge (batched) -> combined gate -> distill -> write.
    `judged_cache` answers for candidates judged on earlier runs but gated out,
    so they never re-hit the (possibly LLM) judge."""
    distiller = distiller or ExtractiveDistiller()
    report = IngestReport()
    report.judge_tier = _judge_tier_name(judge)
    kind_totals: Counter = Counter()

    # Phase A: collect redacted, deduped candidates across all transcripts.
    # Cache hits get their Judgment attached immediately and skip Phase B.
    pending: list[tuple[Candidate, str]] = []  # (candidate, content hash)
    judged: dict[int, Judgment] = {}  # pending index -> judgment (cached or fresh)
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
            if cand.antecedent is not None:
                # Redact the FULL antecedent BEFORE truncating: truncating first
                # could split a boundary-straddling secret into a fragment the
                # named-pattern redactors no longer match, leaking it to the judge.
                ared = redact(cand.antecedent)
                report.redactions += ared.count
                cand = replace(cand, antecedent=ared.text[:_ANTECEDENT_CHARS])
            h = content_hash(cand.text)
            if ledger.seen(h) or h in seen_this_run:
                report.deduped += 1
                continue
            seen_this_run.add(h)
            if judge is not None and judged_cache is not None:
                cached = judged_cache.get(_judge_cache_key(cand))
                # Only reuse a cached verdict if its tier is at least the current
                # run's tier — an embedding-tier entry must not suppress the LLM.
                if cached is not None and tier_at_least(cached[1], report.judge_tier):
                    judged[len(pending)] = cached[0]  # full Judgment, distillation included
            pending.append((cand, h))
    report.event_kinds = dict(kind_totals)

    # Phase B: ONE batched judge call over the un-cached candidates. This phase
    # must NEVER raise: any judge failure degrades those candidates to
    # heuristic-only gating (LLM chunk failures also degrade internally).
    to_judge = [i for i in range(len(pending)) if i not in judged]
    if judge is not None and to_judge:
        try:
            results = judge.judge(
                [pending[i][0].text for i in to_judge],
                contexts=[pending[i][0].antecedent for i in to_judge],
            )
            judged.update(zip(to_judge, results, strict=True))
        except Exception:
            report.judge_degraded += len(to_judge)
    if judge is not None and hasattr(judge, "degraded"):
        report.judge_degraded += judge.degraded

    # Phase C: gate. Kept candidates are collected (not written yet) so the write
    # pass can run in timestamp order for correct supersession.
    # Consolidation requires an explicit LLM-tier run (report.judge_tier == "llm").
    # EmbeddingJudge and heuristic-only runs are NOT consolidated — the cosine
    # pre-gate in the NeighborIndex already assumes LLM-quality distillations.
    consolidating = (
        consolidator is not None
        and neighbor_index is not None
        and report.judge_tier == "llm"
        and not dry_run
    )
    kept: list[tuple[Candidate, str]] = []
    for idx, (cand, h) in enumerate(pending):
        heuristic = score(cand.text)
        j = judged.get(idx)
        # A degraded judgment is a fallback verdict wearing the LLM run's tier — it
        # must gate by the fallback (blend) rule, not the LLM keep rule.
        llm_verdict = j is not None and report.judge_tier == "llm" and not j.degraded
        if llm_verdict:
            # The LLM's decision to DISTILL is the keep signal. Its durability float
            # is noisy (clusters 0.3-0.5), but distilled-vs-null is a clean
            # durable/ephemeral call: keep iff the LLM distilled it. (A durability
            # threshold swept in hundreds of short junk turns rated ~0.5 — dogfood.)
            keep = j.distilled is not None
            combined = j.durability  # frontmatter importance only
            cand = replace(cand, judgment=j, importance=combined)
        elif j is not None:
            # Weaker (embedding) judge OR a degraded LLM chunk: blend durability
            # with the heuristic, exactly as the embedding tier would.
            combined = max(0.0, min(1.0, 0.5 * heuristic + 0.5 * j.durability))
            keep = combined >= threshold
            cand = replace(cand, judgment=j, importance=combined)
        else:
            combined = heuristic
            keep = combined >= threshold
            cand = replace(cand, importance=combined)
        if not keep:
            report.gated_out += 1
            # Cache the gated verdict so the LLM never re-judges it — but NEVER a
            # degraded verdict (a transient chunk failure fell back a tier; a real
            # LLM verdict must replace it next run, else one API blip drops the turn
            # forever), and NOT on dry runs (tier deliberately downgraded — caching
            # would make later real runs cache-hit and skip the LLM).
            if (
                judge is not None
                and judged_cache is not None
                and j is not None
                and not j.degraded
                and not dry_run
            ):
                judged_cache.put(_judge_cache_key(cand), j, report.judge_tier)
            continue
        kept.append((cand, h))

    # Consolidation needs timestamp order so a later memory supersedes an earlier
    # one (None timestamps sort first via `or ""`). Off the consolidating path we
    # keep the original collection order so write/ledger order is unchanged.
    ordered = sorted(kept, key=lambda ch: ch[0].timestamp or "") if consolidating else kept
    for cand, h in ordered:
        report.candidates += 1
        note = distiller.distill(cand)
        if dry_run:
            continue
        if consolidating:
            verdict, neighbor = _consolidate(cand, consolidator, neighbor_index)
            if verdict is ConsolidationVerdict.DUPLICATE:
                report.semantic_deduped += 1
                ledger.add(h)
                continue
            if verdict is ConsolidationVerdict.SUPERSEDES and neighbor is not None:
                if neighbor.path:
                    p = Path(neighbor.path)
                    old_path = p if p.is_absolute() else vault_root / p
                else:
                    old_path = vault_root / subdir / f"{neighbor.permalink}.md"
                if old_path.exists():
                    try:
                        mark_superseded(old_path, note.permalink)
                        report.superseded += 1
                        neighbor_index.note_superseded(neighbor.permalink)
                    except Exception:
                        # A malformed pre-existing note must not abort the sweep:
                        # skip the supersede mark and keep both notes (safe).
                        pass
        path = write_derived_note(note, vault_root, subdir=subdir)
        ledger.add(h)
        report.written.append(path)
        if consolidating:
            neighbor_index.add(note.permalink, _memory_text(cand), cand.timestamp, str(path))
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
