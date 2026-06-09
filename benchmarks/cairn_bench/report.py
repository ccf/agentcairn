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


def aggregate_by_category(per_query: list[dict]) -> dict:
    """Produce per-category macro-means for retrieval metrics.

    Returns {arm: {category: {granularity: {metric: mean}}}}. Mirrors `aggregate`
    but further splits by `category`. Fractional retrieval values → macro-mean; no
    Wilson CI (retrieval is not binomial).
    """
    buckets: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))
    for row in per_query:
        arm = row["arm"]
        cat = row.get("category")
        for gran in ("turn", "session"):
            for metric, val in row.get(gran, {}).items():
                buckets[arm][cat][gran][metric].append(val)
    out: dict = {}
    for arm, cats in buckets.items():
        out[arm] = {}
        for cat, grans in cats.items():
            out[arm][cat] = {
                gran: {m: _mean(vals) for m, vals in metrics.items()}
                for gran, metrics in grans.items()
            }
    return out


def aggregate_qa(qa_rows: list[dict]) -> dict:
    """Aggregate QA result rows into accuracy stats with Wilson CIs.

    qa_rows: list of {"category": ..., "is_abstention": bool, "correct": bool}

    Returns:
        {
            "overall": {"acc": float, "n": int, "ci": (lo, hi)},
            "by_category": {cat: {"acc": float, "n": int, "ci": (lo, hi)}},
            "abstention": {"acc": float, "n": int, "ci": (lo, hi)},
        }

    Answerable queries (is_abstention=False) feed "overall" and "by_category".
    Abstention queries (is_abstention=True) feed "abstention" only.
    """
    answerable = [r for r in qa_rows if not r["is_abstention"]]
    abstentions = [r for r in qa_rows if r["is_abstention"]]

    def _acc_stats(rows: list[dict]) -> dict:
        n = len(rows)
        successes = sum(1 for r in rows if r["correct"])
        acc = successes / n if n else 0.0
        return {"acc": acc, "n": n, "ci": wilson_ci(successes, n)}

    by_cat: dict = {}
    cat_buckets: dict = defaultdict(list)
    for r in answerable:
        cat_buckets[r["category"]].append(r)
    for cat, rows in cat_buckets.items():
        by_cat[cat] = _acc_stats(rows)

    return {
        "overall": _acc_stats(answerable),
        "by_category": by_cat,
        "abstention": _acc_stats(abstentions),
    }


def qa_to_markdown(qa_agg: dict, *, judge_model: str = "unknown") -> str:
    """Render QA aggregate as a labeled markdown table with Wilson 95% CIs.

    Answerable and abstention sections are labeled separately. Includes the
    mandatory caveat that these numbers are NOT comparable to published leaderboards.
    """
    lines = [
        f"\n### QA Accuracy (judge={judge_model})\n",
        "> **NOT comparable to published leaderboards** (uses Anthropic judge, not GPT-4o).",
        "> Valid for relative ablation signal only.\n",
    ]

    # Answerable queries — overall + per-category
    ov = qa_agg.get("overall", {})
    n_ov = ov.get("n", 0)
    acc_ov = ov.get("acc", 0.0)
    lo_ov, hi_ov = ov.get("ci", (0.0, 0.0))
    lines.append("#### Answerable queries\n")
    lines.append("| scope | accuracy | 95% CI | n |")
    lines.append("|---|---|---|---|")
    lines.append(f"| **overall** | {acc_ov:.3f} | [{lo_ov:.3f}, {hi_ov:.3f}] | {n_ov} |")
    for cat, stats in sorted(qa_agg.get("by_category", {}).items(), key=lambda x: str(x[0])):
        acc = stats.get("acc", 0.0)
        lo, hi = stats.get("ci", (0.0, 0.0))
        n = stats.get("n", 0)
        lines.append(f"| cat {cat} | {acc:.3f} | [{lo:.3f}, {hi:.3f}] | {n} |")

    # Abstention queries
    ab = qa_agg.get("abstention", {})
    n_ab = ab.get("n", 0)
    acc_ab = ab.get("acc", 0.0)
    lo_ab, hi_ab = ab.get("ci", (0.0, 0.0))
    lines.append("\n#### Abstention / adversarial queries\n")
    lines.append("| scope | accuracy | 95% CI | n |")
    lines.append("|---|---|---|---|")
    lines.append(f"| **abstention** | {acc_ab:.3f} | [{lo_ab:.3f}, {hi_ab:.3f}] | {n_ab} |")

    return "\n".join(lines)


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
