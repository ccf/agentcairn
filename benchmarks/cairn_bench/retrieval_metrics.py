# SPDX-License-Identifier: Apache-2.0
"""Retrieval metrics over a ranked list of ids and a gold set. Deterministic, no LLM.
`recall_at_k`/`ndcg_at_k`/`reciprocal_rank` are textbook (fractional). `recall_all_at_k`
/`ndcg_any_at_k` replicate the LongMemEval official 'strict' definitions for line-up."""

from __future__ import annotations

import math


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    topk = set(ranked[:k])
    return len(topk & gold) / len(gold)


def recall_all_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    """LongMemEval-style: 1.0 iff ALL gold ids appear in the top-k, else 0.0."""
    if not gold:
        return 0.0
    topk = set(ranked[:k])
    return 1.0 if gold <= topk else 0.0


def reciprocal_rank(ranked: list[str], gold: set[str]) -> float:
    for i, rid in enumerate(ranked):
        if rid in gold:
            return 1.0 / (i + 1)
    return 0.0


def _dcg(rels: list[float]) -> float:
    return (
        rels[0] + sum(r / math.log2(i + 2) for i, r in enumerate(rels[1:], start=1))
        if rels
        else 0.0
    )


def ndcg_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    seen: set[str] = set()
    dedup: list[str] = []
    for rid in ranked:
        if rid not in seen:
            seen.add(rid)
            dedup.append(rid)
    rels = [1.0 if rid in gold else 0.0 for rid in dedup[:k]]
    ideal = [1.0] * min(len(gold), k)
    idcg = _dcg(ideal)
    return _dcg(rels) / idcg if idcg else 0.0


# ndcg_any uses binary relevance too; kept as a distinct name to match the paper's label.
ndcg_any_at_k = ndcg_at_k
