# SPDX-License-Identifier: Apache-2.0
import json
import os
from pathlib import Path

import pytest

from cairn.embed import FakeEmbedder, get_embedder
from cairn.index import build_fts, index_vault, open_index
from cairn.ingest.dedup import DedupLedger
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.locate import find_transcripts, parse_transcript
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


def test_opencode_adapter_core_loop_offline(tmp_path, monkeypatch):
    """OpenCode adapter → locate → parse → ingest → index → recall, offline (fake embedder).
    Guards that the OpenCode storage layout is correctly traversed end-to-end."""
    # Build a minimal OpenCode storage fixture
    storage = tmp_path / "storage"
    msg_dir = storage / "message" / "sess1"
    msg_dir.mkdir(parents=True)
    (msg_dir / "msg1.json").write_text(
        json.dumps({"role": "user", "time": {"created": 1}}),
        encoding="utf-8",
    )
    part_dir = storage / "part" / "msg1"
    part_dir.mkdir(parents=True)
    (part_dir / "p1.json").write_text(
        json.dumps(
            {
                "type": "text",
                "text": (
                    "We decided to deploy only with make ship, never npm publish directly,"
                    " because the Makefile enforces the pre-flight checks we always need."
                ),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path))

    # Locate via harness="opencode" — must find our session
    refs = find_transcripts(harness="opencode")
    assert refs, "find_transcripts(harness='opencode') returned nothing"
    assert any(r.path.name == "sess1" for r in refs), "sess1 not found in refs"

    # Auto-detect (harness=None) must also include the opencode session
    auto_refs = find_transcripts(harness=None)
    assert any(r.harness == "opencode" and r.path.name == "sess1" for r in auto_refs), (
        "auto-detect did not include the opencode sess1 session"
    )

    # Parse the transcript
    ref = next(r for r in refs if r.path.name == "sess1")
    transcript = parse_transcript(ref, harness="opencode")
    assert transcript.events, "parsed transcript has no events"

    # Ingest → index → recall
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(transcript, vault_root=vault, ledger=ledger)
    assert report.written, "ingest wrote no notes"

    emb = FakeEmbedder(dim=8)
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(vault), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    hits = search(con, "how do we deploy", embedder=emb, k=10)
    assert hits, "recall returned nothing for an ingested fact"
    blob = " ".join(h.snippet.lower() for h in hits)
    assert "make ship" in blob, f"expected 'make ship' in recall output, got: {blob!r}"


_LABELS = [
    ("what is the scope of cairn link for the Obsidian graph", "cairn-link-scope"),
    ("how does the index avoid scratch-vault pollution", "vault-scoped-index"),
    ("when does capture run relative to compaction", "precompact-capture"),
]
_SUMMARY = "session-summary-2026-06-18"


@pytest.mark.skipif(
    not os.environ.get("CAIRN_E2E"),
    reason="set CAIRN_E2E=1 to run the real-embedder recall-quality eval",
)
def test_recall_quality(tmp_path):
    fixtures = Path(__file__).parent / "fixtures" / "recall_eval"
    vault = tmp_path / "vault"
    vault.mkdir()
    for md in fixtures.glob("*.md"):
        (vault / md.name).write_text(md.read_text())

    try:
        emb = get_embedder("fastembed")
    except Exception as exc:  # model unavailable offline -> skip, never fail
        pytest.skip(f"fastembed unavailable: {exc}")

    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(vault), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    k = 10
    hit_at_k = 0
    rr_total = 0.0
    failures = []
    for query, expected in _LABELS:
        hits = search(con, query, embedder=emb, k=k, rerank=True)
        permalinks = [h.permalink for h in hits]
        assert len(permalinks) == len(set(permalinks)), f"dup notes: {permalinks}"
        if expected in permalinks:
            hit_at_k += 1
            rr_total += 1.0 / (permalinks.index(expected) + 1)
        e_idx = permalinks.index(expected) if expected in permalinks else 10**6
        s_idx = permalinks.index(_SUMMARY) if _SUMMARY in permalinks else 10**6
        if not e_idx < s_idx:
            failures.append((query, expected, permalinks))

    recall_at_k = hit_at_k / len(_LABELS)
    mrr = rr_total / len(_LABELS)
    print(f"\n[recall-eval] recall@{k}={recall_at_k:.3f} MRR={mrr:.3f}")
    assert not failures, f"atomic note did not outrank the session summary: {failures}"
    assert recall_at_k == 1.0, f"recall@{k}={recall_at_k}"
