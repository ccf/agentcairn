def test_candidate_antecedent_defaults_none_and_accepts_value():
    from pathlib import Path

    from cairn.ingest.models import Candidate

    base = dict(
        text="lock A",
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        timestamp="t0",
        source_path=Path("/tmp/s.jsonl"),
    )
    assert Candidate(**base).antecedent is None  # defaulted, existing constructors unaffected
    assert Candidate(**base, antecedent="Approach A: the orderbook rep").antecedent == (
        "Approach A: the orderbook rep"
    )


def test_ingest_report_consolidation_counters():
    from cairn.ingest.models import IngestReport

    r = IngestReport()
    assert r.semantic_deduped == 0 and r.superseded == 0
    r.semantic_deduped += 1
    r.superseded += 2
    d = r.to_dict()
    assert d["semantic_deduped"] == 1 and d["superseded"] == 2
