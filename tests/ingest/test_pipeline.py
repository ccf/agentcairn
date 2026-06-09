# tests/ingest/test_pipeline.py
# SPDX-License-Identifier: Apache-2.0

import json

from cairn.ingest.dedup import DedupLedger
from cairn.ingest.models import IngestReport, Transcript, Turn
from cairn.ingest.pipeline import ingest_transcript

SECRET = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"


def _transcript(tmp_path) -> Transcript:
    return Transcript(
        session_id="sess-1",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "sess-1.jsonl",
        turns=[
            Turn("user", "thanks!", "t0"),  # trivial -> gated out
            Turn("user", f"We decided to always rotate the token; the old one was {SECRET}.", "t1"),
            Turn("assistant", "Understood, rotating now.", "t2"),  # not a user turn -> skipped
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
