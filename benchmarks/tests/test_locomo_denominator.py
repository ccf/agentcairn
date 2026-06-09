# SPDX-License-Identifier: Apache-2.0
"""Zep-denominator invariant: LoCoMo cat-5 (adversarial) is present in the query list
as is_abstention=True with empty gold sets, but must NOT contribute to any retrieval
metric's numerator OR denominator.

Old model: cat-5 was silently dropped (excluded from query list entirely).
New model (Fix 1): cat-5 is emitted as a Query(is_abstention=True, gold_turns=set(),
gold_sessions=set()), so the abstention QA path can consume it, but score_query / the
retrieval loop must still exclude it (empty gold means no metric contribution).
"""

from __future__ import annotations

from cairn_bench.ablation import score_query
from cairn_bench.adapters import locomo
from cairn_bench.config import RankedRow


def test_cat5_present_as_abstention(locomo_samples):
    """cat-5 query is in the query list with is_abstention=True and empty gold."""
    _notes, queries = locomo.adapt(locomo_samples[0])
    cat5_queries = [q for q in queries if q.category == 5]
    assert len(cat5_queries) == 1, "exactly one cat-5 query expected"
    q = cat5_queries[0]
    assert q.is_abstention is True
    assert q.gold_turns == set()
    assert q.gold_sessions == set()


def test_cat5_empty_gold_yields_empty_score(locomo_samples):
    """score_query on a cat-5 abstention query returns empty turn/session dicts.

    Empty gold sets cause score_query to skip both granularities, so the cat-5
    query contributes to neither numerator nor denominator of any retrieval metric
    (the Zep-bug invariant: adding an unanswerable query must not inflate the
    denominator either).
    """
    _notes, queries = locomo.adapt(locomo_samples[0])
    cat5 = next(q for q in queries if q.category == 5)

    # Simulate some retrieval rows (content doesn't matter for this invariant).
    rows = [RankedRow("conv-synth-1_session_1", "conv-synth-1_session_1 > D1:1  (Alex)")]
    result = score_query(rows, cat5, [1, 3, 5])

    assert result.get("turn", {}) == {}, (
        f"cat-5 (empty gold) must yield empty turn metrics, got {result.get('turn')}"
    )
    assert result.get("session", {}) == {}, (
        f"cat-5 (empty gold) must yield empty session metrics, got {result.get('session')}"
    )


def test_retrieval_loop_skips_empty_gold_queries(locomo_samples):
    """The retrieval loop condition 'if not q.gold_turns and not q.gold_sessions: continue'
    correctly skips cat-5 abstention queries, so they never enter per_query accumulation."""
    _notes, queries = locomo.adapt(locomo_samples[0])
    # Simulate the retrieval loop filter from run.py
    retrieval_queries = [q for q in queries if q.gold_turns or q.gold_sessions]
    abstention_queries = [q for q in queries if not q.gold_turns and not q.gold_sessions]

    # The fixture has 4 retrieval queries and 1 abstention query
    assert len(retrieval_queries) == 4
    assert len(abstention_queries) == 1
    assert abstention_queries[0].category == 5
    assert abstention_queries[0].is_abstention is True
