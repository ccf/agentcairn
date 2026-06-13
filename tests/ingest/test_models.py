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
