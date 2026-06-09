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
