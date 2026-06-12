#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Layer-B validation harness: score a labeled corpus (durable/ephemeral) with the
EmbeddingJudge and the importance heuristic; report AUC + precision/recall at the
0.5 gate. Offline, no keys. The REAL labeled corpus lives locally (privacy):
  ~/.cache/agentcairn/judge_labels.jsonl   # {"text": ..., "label": "durable"|"ephemeral"}
Usage:
  uv run python scripts/eval_judge.py [--labels PATH] [--embedder fastembed|fake]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def auc(labels: list[int], scores: list[float]) -> float:
    """Rank-based AUC (probability a positive outranks a negative); ties count half."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def pr_at(labels: list[int], scores: list[float], threshold: float) -> tuple[float, float]:
    tp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y == 1)
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y == 0)
    fn = sum(1 for s, y in zip(scores, labels, strict=True) if s < threshold and y == 1)
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    return precision, recall


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(Path.home() / ".cache/agentcairn/judge_labels.jsonl"))
    ap.add_argument("--embedder", default="fastembed")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    rows = [json.loads(ln) for ln in Path(args.labels).read_text().splitlines() if ln.strip()]
    texts = [r["text"] for r in rows]
    labels = [1 if r["label"] == "durable" else 0 for r in rows]

    from cairn.embed import get_embedder
    from cairn.ingest.importance import score as heuristic_score
    from cairn.ingest.judge import EmbeddingJudge

    judge = EmbeddingJudge(get_embedder(args.embedder))
    durability = [j.durability for j in judge.judge(texts)]
    heuristic = [heuristic_score(t) for t in texts]
    combined = [
        max(0.0, min(1.0, 0.5 * h + 0.5 * d)) for h, d in zip(heuristic, durability, strict=True)
    ]

    for name, scores in [
        ("heuristic", heuristic),
        ("embedding", durability),
        ("combined", combined),
    ]:
        p, r = pr_at(labels, scores, args.threshold)
        thr = args.threshold
        print(f"{name:10s} AUC={auc(labels, scores):.3f}  P@{thr}={p:.3f}  R@{thr}={r:.3f}")


if __name__ == "__main__":
    main()
