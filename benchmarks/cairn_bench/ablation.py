# SPDX-License-Identifier: Apache-2.0
"""Run one arm on one query and compute retrieval metrics at the requested k values,
at both turn granularity (parse turn id from heading_path) and session granularity."""

from __future__ import annotations

from cairn_bench.config import ArmConfig, RankedRow
from cairn_bench.models import Query
from cairn_bench.retrieval_metrics import ndcg_at_k, recall_all_at_k, recall_at_k, reciprocal_rank

# heading_path is stored as "{note_permalink} > {turn_id}  ({meta})" by the chunker.
# We split on " > " (the separator) and take the first whitespace-delimited token of the
# section portion to recover the bare turn id (e.g. "s_a_1" or "D1:2").


def _turn_id(heading_path: str) -> str:
    """Extract the turn id from a heading_path like 's_a > s_a_1  (user, 2024-01-05)'."""
    if not heading_path:
        return ""
    parts = heading_path.split(" > ", 1)
    if len(parts) < 2:
        # Fallback: no " > " separator — treat the whole string's first token as the id.
        return heading_path.split()[0]
    return parts[1].split()[0]


def score_query(rows: list[RankedRow], query: Query, ks: list[int]) -> dict:
    """Return {granularity: {metric@k: value}} for one (arm, query)."""
    turn_ranked = [_turn_id(r.heading_path) for r in rows]
    sess_ranked = [r.permalink for r in rows]
    out: dict = {"turn": {}, "session": {}}
    for gran, ranked, gold in (
        ("turn", turn_ranked, query.gold_turns),
        ("session", sess_ranked, query.gold_sessions),
    ):
        if not gold:
            continue
        for k in ks:
            out[gran][f"recall@{k}"] = recall_at_k(ranked, gold, k)
            out[gran][f"ndcg@{k}"] = ndcg_at_k(ranked, gold, k)
            out[gran][f"recall_all@{k}"] = recall_all_at_k(ranked, gold, k)
        out[gran]["mrr"] = reciprocal_rank(ranked, gold)
    return out


def run_arm(con, arm: ArmConfig, query: Query, embedder, *, ks: list[int], pool: int) -> dict:
    rows = arm.rank(con, query.question, embedder, pool, max(ks))
    return score_query(rows, query, ks)
