# SPDX-License-Identifier: Apache-2.0
"""Manual benchmark entrypoint: `python -m cairn_bench.run --dataset longmemeval-s`.

Loads a real dataset (downloaded + SHA-pinned via manifest.toml), runs the ablation
matrix over a configurable sample of instances, and prints the retrieval report.

The QA layer is opt-in via --qa (needs ANTHROPIC_API_KEY and the bench dep group).

Usage examples:
    uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --limit 50
    uv run --group bench python -m cairn_bench.run --dataset locomo

Pipeline: adapt -> build scoped index -> ablation arms -> aggregate -> report.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from cairn.embed import get_embedder
from cairn_bench import download
from cairn_bench.ablation import run_arm
from cairn_bench.adapters import locomo, longmemeval
from cairn_bench.build import build_scoped_index
from cairn_bench.config import ARMS
from cairn_bench.report import aggregate, to_markdown

KS = [1, 3, 5, 10, 20]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run the agentcairn retrieval benchmark over a real dataset."
    )
    ap.add_argument(
        "--dataset",
        choices=["longmemeval-s", "locomo"],
        required=True,
        help="Which dataset to benchmark (must be fetchable via manifest.toml).",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Max instances/conversations to process (default: 50).",
    )
    ap.add_argument(
        "--embedder",
        default="fastembed",
        help="Embedder name passed to cairn.embed.get_embedder (default: fastembed).",
    )
    args = ap.parse_args()

    emb = get_embedder(args.embedder)
    per_query: list[dict] = []

    if args.dataset == "longmemeval-s":
        data = json.loads(download.fetch("longmemeval_s").read_text())
        records = [longmemeval.adapt(inst) for inst in data[: args.limit]]
    else:
        data = json.loads(download.fetch("locomo").read_text())
        records = [locomo.adapt(s) for s in data[: args.limit]]

    for notes, queries in records:
        with tempfile.TemporaryDirectory() as d:
            con, chunks = build_scoped_index(notes, Path(d), emb)
            try:
                for q in queries:
                    if not q.gold_turns and not q.gold_sessions:
                        continue
                    for arm in ARMS:
                        res = run_arm(con, arm, q, emb, ks=KS, pool=max(200, chunks))
                        per_query.append({"arm": arm.name, "category": q.category, **res})
            finally:
                con.close()

    agg = aggregate(per_query, ks=KS)
    print(to_markdown(agg, granularity="turn"))


if __name__ == "__main__":
    main()
