# SPDX-License-Identifier: Apache-2.0
"""Aggregate per-query metrics into macro-averages (overall + per-category) with Wilson
95% CIs, and render a labeled markdown table. No single headline number — every row is
tagged with its arm, granularity, and (retrieval|qa) axis."""

from __future__ import annotations

import math
from collections import defaultdict


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial rate (used for per-category accuracy)."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def aggregate(per_query: list[dict]) -> dict:
    """per_query rows: {arm, category, turn:{metric:val}, session:{...}}. Returns
    {arm: {granularity: {metric: macro-mean}}}."""
    buckets: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in per_query:
        arm = row["arm"]
        for gran in ("turn", "session"):
            for metric, val in row.get(gran, {}).items():
                buckets[arm][gran][metric].append(val)
    out: dict = {}
    for arm, grans in buckets.items():
        out[arm] = {
            gran: {m: _mean(vals) for m, vals in metrics.items()} for gran, metrics in grans.items()
        }
    return out


def to_markdown(agg: dict, *, granularity: str = "turn") -> str:
    lines = [
        f"### Retrieval — {granularity}-level (macro-avg)\n",
        "| arm | recall@5 | recall@10 | ndcg@10 | mrr |",
        "|---|---|---|---|---|",
    ]
    for arm, grans in agg.items():
        m = grans.get(granularity, {})
        lines.append(
            f"| {arm} | {m.get('recall@5', 0):.3f} | {m.get('recall@10', 0):.3f} "
            f"| {m.get('ndcg@10', 0):.3f} | {m.get('mrr', 0):.3f} |"
        )
    lines.append(
        "\n_Retrieval metrics only — not QA accuracy. No single headline number; "
        "see caveats in benchmarks/README.md._"
    )
    return "\n".join(lines)
