# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.embed import FakeEmbedder
from cairn.index import build_fts, index_vault, open_index
from cairn.ingest.dedup import DedupLedger
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.models import Transcript
from cairn.ingest.pipeline import ingest_transcript
from cairn.search import open_search, search


def _ev(kind: EventKind, text: str) -> NormalizedEvent:
    return NormalizedEvent(
        kind=kind,
        role="user" if kind == EventKind.AUTHORED_USER else "assistant",
        text=text,
        timestamp="t0",
        session_id="e2e-1",
        project="proj",
        git_branch="main",
        source_path=Path("/tmp/e2e-1.jsonl"),
        harness="claude-code",
    )


def test_core_loop_offline(tmp_path):
    """Capture -> index -> recall, end to end, offline (fake embedder, no judge).
    Guards the loop the SessionEnd/PreCompact sweep runs."""
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    transcript = Transcript(
        session_id="e2e-1",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "e2e-1.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "We decided to pin the DuckDB version to 1.1 because"
                " 1.2 broke array_cosine_similarity.",
            ),
        ],
    )
    report = ingest_transcript(transcript, vault_root=vault, ledger=ledger)
    assert report.written, "ingest wrote no notes"

    emb = FakeEmbedder(dim=8)
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(vault), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    hits = search(con, "why is the DuckDB version pinned", embedder=emb, k=10)
    assert hits, "recall returned nothing for an ingested fact"
    blob = " ".join(h.snippet.lower() for h in hits)
    assert "duckdb" in blob
