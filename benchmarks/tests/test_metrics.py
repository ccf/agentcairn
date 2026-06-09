# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import math

from cairn_bench.retrieval_metrics import (
    ndcg_any_at_k,
    ndcg_at_k,
    recall_all_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k_fractional():
    ranked = ["a", "x", "b", "y"]
    gold = {"a", "b", "c"}  # 2 of 3 gold in top-3
    assert recall_at_k(ranked, gold, 3) == 2 / 3
    assert recall_at_k(ranked, gold, 1) == 1 / 3
    assert recall_at_k([], gold, 5) == 0.0


def test_recall_all_at_k_strict():
    ranked = ["a", "b", "x"]
    assert recall_all_at_k(ranked, {"a", "b"}, 3) == 1.0  # all gold present
    assert recall_all_at_k(ranked, {"a", "b"}, 1) == 0.0  # not all in top-1


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "a", "b"], {"a"}) == 0.5  # first gold at rank 2
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_ndcg_monotonic():
    gold = {"a"}
    # gold at rank 1 scores higher than gold at rank 3
    assert ndcg_at_k(["a", "x", "y"], gold, 3) > ndcg_at_k(["x", "y", "a"], gold, 3)
    assert math.isclose(ndcg_at_k(["a"], gold, 3), 1.0)


def test_ndcg_any_binary():
    # ndcg_any uses binary relevance; multiple gold contribute
    assert ndcg_any_at_k(["a", "b"], {"a", "b"}, 2) > 0.0


def test_ndcg_dedup_no_exceed_one():
    """nDCG must stay in [0, 1] even when the ranked list contains duplicate ids.

    A chunk-granular ranker can return the same gold id multiple times (e.g. once per
    chunk from that turn), which previously inflated DCG above IDCG → nDCG > 1.0.
    After deduplication, nDCG must equal 1.0 when the single gold item sits at rank 1.
    """
    # Three copies of the same gold turn id — without dedup this produces nDCG ≈ 2.13.
    assert ndcg_at_k(["t", "t", "t"], {"t"}, 20) == 1.0
    # Any ranked list should stay ≤ 1.0.
    assert ndcg_at_k(["a", "b", "a", "c", "a"], {"a", "b"}, 5) <= 1.0
    # The original monotonic property must still hold (gold at rank 1 > gold at rank 3).
    gold = {"a"}
    assert ndcg_at_k(["a", "x", "y"], gold, 3) > ndcg_at_k(["x", "y", "a"], gold, 3)


def test_score_query_dedup_shared_window():
    """recall@k and ndcg@k must share the same deduped top-k window.

    When duplicate turn ids fill the raw ranked list, recall_at_k (which slices
    the raw list) would miss gold items that ndcg_at_k (which dedups first) finds.
    After the fix, score_query dedups before passing to any metric, so both agree.
    """
    from cairn_bench.ablation import score_query
    from cairn_bench.config import RankedRow
    from cairn_bench.models import Query

    # Two rows with the same turn id (t1) before the gold turn (t2).
    # heading_path format: "{permalink} > {turn_id}  (meta)"
    rows = [
        RankedRow("p", "p > t1  (x)"),
        RankedRow("p", "p > t1  (x)"),
        RankedRow("p2", "p2 > t2  (x)"),
    ]
    q = Query(qid="q", question="?", answer="", gold_turns={"t2"})
    res = score_query(rows, q, [2])

    # After dedup, top-2 unique turn ids are ["t1", "t2"], so t2 IS in top-2.
    assert res["turn"]["recall@2"] == 1.0, (
        f"recall@2 should be 1.0 (t2 in deduped top-2), got {res['turn']['recall@2']}"
    )
    assert res["turn"]["ndcg@2"] > 0, (
        f"ndcg@2 should be > 0 (t2 in deduped top-2), got {res['turn']['ndcg@2']}"
    )


def test_run_arm_overfetches_for_unique_turns():
    """run_arm must request more chunks than max(ks) so dedup yields ≥ max(ks) unique turns."""
    from cairn_bench.ablation import run_arm
    from cairn_bench.config import ArmConfig
    from cairn_bench.models import Query

    seen = {}

    def recording_rank(con, q, e, pool, k):
        seen["k"] = k
        return []

    arm = ArmConfig("rec", recording_rank)
    run_arm(
        None,
        arm,
        Query(qid="q", question="?", answer="", gold_turns={"t"}),
        None,
        ks=[5],
        pool=200,
    )
    assert seen["k"] > 5, f"expected k > 5 (overfetch), got {seen['k']}"
    assert seen["k"] <= 200, f"expected k ≤ pool=200, got {seen['k']}"


def test_aggregate_macro_average():
    from cairn_bench.report import aggregate, wilson_ci

    per_query = [
        {"arm": "hybrid-rrf", "category": "multi-session", "turn": {"recall@5": 1.0, "mrr": 1.0}},
        {"arm": "hybrid-rrf", "category": "multi-session", "turn": {"recall@5": 0.0, "mrr": 0.0}},
    ]
    agg = aggregate(per_query)
    assert agg["hybrid-rrf"]["turn"]["recall@5"] == 0.5
    lo, hi = wilson_ci(1, 2)
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
