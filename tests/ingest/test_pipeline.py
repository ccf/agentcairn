# tests/ingest/test_pipeline.py
# SPDX-License-Identifier: Apache-2.0

import json

from cairn.ingest.dedup import DedupLedger
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.models import IngestReport, Transcript
from cairn.ingest.pipeline import ingest_transcript

SECRET = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"


def _ev(kind, text, ts="t0"):
    from pathlib import Path

    return NormalizedEvent(
        kind=kind,
        role="user",
        text=text,
        timestamp=ts,
        session_id="sess-1",
        project="proj",
        git_branch="main",
        source_path=Path("/tmp/sess-1.jsonl"),
    )


def _transcript(tmp_path) -> Transcript:
    return Transcript(
        session_id="sess-1",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "sess-1.jsonl",
        events=[
            _ev(EventKind.AUTHORED_USER, "thanks!"),  # authored but trivial -> gated out
            _ev(
                EventKind.AUTHORED_USER,
                f"We decided to always rotate the token; the old one was {SECRET}.",
            ),
            _ev(EventKind.AUTHORED_ASSISTANT, "Understood, rotating now."),  # not a candidate
        ],
    )


def test_pipeline_redacts_before_write_and_gates(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)

    assert report.candidates == 1  # only the one substantive user turn
    assert report.gated_out == 1  # "thanks!"
    assert len(report.written) == 1
    assert report.redactions >= 1

    # INVARIANT: the secret never reaches disk
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert SECRET not in blob
    assert "[REDACTED" in blob


def test_pipeline_ingests_only_authored_user_events(tmp_path):
    """Tool results, meta injections, summaries, and assistant turns are excluded
    by KIND — no text patterns involved. The per-kind tally is reported."""
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.TOOL_RESULT, "Context Usage 49.8k/1m tokens; system prompt 6.7k"),
            _ev(EventKind.META_INJECTION, "<task-notification> background task done"),
            _ev(
                EventKind.COMPACT_SUMMARY,
                "This session is being continued from a previous conversation.",
            ),
            _ev(
                EventKind.AUTHORED_USER, "We decided to always rebase-merge and delete the branch."
            ),
        ],
    )
    report = ingest_transcript(tr, vault_root=vault, ledger=ledger)
    assert report.authored == 1
    assert report.candidates == 1
    assert report.event_kinds == {
        "tool_result": 1,
        "meta_injection": 1,
        "compact_summary": 1,
        "authored_user": 1,
    }
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "rebase-merge" in blob
    assert "task-notification" not in blob and "Context Usage" not in blob


def test_pipeline_dedup_skips_on_second_run(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    report2 = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert report2.deduped == 1
    assert report2.written == []


def test_pipeline_dry_run_writes_nothing(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger, dry_run=True)
    assert report.written == []
    assert list(vault.rglob("*.md")) == []
    # dry-run must not poison the ledger
    assert report.deduped == 0

    # dry-run left the ledger clean: a real run now actually writes.
    real = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert len(real.written) == 1


# ---------------------------------------------------------------------------
# M3 — IngestReport.to_dict() must produce a JSON-serializable dict
# ---------------------------------------------------------------------------


def test_ingest_report_to_dict_is_json_serializable(tmp_path):
    """M3: IngestReport.to_dict() must be JSON-serializable (Paths -> str)."""
    from pathlib import Path

    report = IngestReport(
        candidates=3,
        redactions=1,
        deduped=1,
        gated_out=1,
        written=[Path("/vault/memories/note-abc.md")],
    )
    d = report.to_dict()
    serialized = json.dumps(d)  # must not raise
    parsed = json.loads(serialized)
    assert parsed["candidates"] == 3
    assert parsed["redactions"] == 1
    assert parsed["deduped"] == 1
    assert parsed["gated_out"] == 1
    assert parsed["written"] == ["/vault/memories/note-abc.md"]
    assert parsed["authored"] == 0
    assert parsed["event_kinds"] == {}


# ---------------------------------------------------------------------------
# M2 — Plan-5 seams must be exported from cairn.ingest
# ---------------------------------------------------------------------------


def test_ingest_package_exports_plan5_seams():
    """M2: cairn.ingest must export redact, RedactionResult, DedupLedger,
    content_hash, Distiller, ExtractiveDistiller, write_derived_note."""
    import cairn.ingest as pkg

    expected = [
        "redact",
        "RedactionResult",
        "DedupLedger",
        "content_hash",
        "Distiller",
        "ExtractiveDistiller",
        "write_derived_note",
    ]
    for name in expected:
        assert hasattr(pkg, name), f"cairn.ingest missing export: {name!r}"
        assert name in pkg.__all__, f"{name!r} not in cairn.ingest.__all__"


# ---------------------------------------------------------------------------
# Layer B — batched ingest with one judge call per run
# ---------------------------------------------------------------------------


def test_ingest_transcripts_judges_once_and_gates_by_combined_score(tmp_path):
    """One judge call per run across transcripts; combined = 0.5*heuristic+0.5*durability."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    calls = []

    class SpyJudge:
        def judge(self, texts):
            calls.append(list(texts))
            # first candidate durable, second ephemeral
            return [
                Judgment(durability=1.0, title="Durable decision", distilled="The decision.")
                if "decided" in t
                else Judgment(durability=0.0)
                for t in texts
            ]

    t1 = Transcript(
        session_id="s1",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s1.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "We decided to always rebase-merge approved PRs because it is important.",
            )
        ],
    )
    t2 = Transcript(
        session_id="s2",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s2.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "Check the CI status on PR #76 and merge it if everything is green "
                "because we should ship.",
            )
        ],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts([t1, t2], vault_root=vault, ledger=ledger, judge=SpyJudge())

    assert len(calls) == 1 and len(calls[0]) == 2  # ONE batched call for both transcripts
    # durable: 0.5*h + 0.5*1.0 >= 0.5 -> written; ephemeral: 0.5*h + 0.5*0 < 0.5 -> gated
    assert len(report.written) == 1
    assert report.gated_out >= 1
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "- [context] The decision. #ingested" in blob
    assert "- [verbatim] We decided" in blob
    assert "CI status" not in blob


def test_ingest_transcripts_without_judge_matches_legacy_behavior(tmp_path):
    from cairn.ingest.pipeline import ingest_transcripts

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)], vault_root=vault, ledger=ledger, judge=None
    )
    assert report.judge_tier == "none"
    assert len(report.written) == 1  # same as today's singular behavior


def test_ingest_transcript_singular_still_works(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert len(report.written) == 1  # unchanged public API


def test_report_judge_tier_recorded(tmp_path):
    from cairn.ingest.judge import EmbeddingJudge
    from cairn.ingest.pipeline import ingest_transcripts
    from tests.ingest.test_judge import StubEmbedder

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)],
        vault_root=vault,
        ledger=ledger,
        judge=EmbeddingJudge(StubEmbedder()),
    )
    assert report.judge_tier == "embedding"
