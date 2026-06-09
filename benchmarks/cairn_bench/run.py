# SPDX-License-Identifier: Apache-2.0
"""Manual benchmark entrypoint: `python -m cairn_bench.run --dataset longmemeval-s`.

Loads a real dataset (downloaded + SHA-pinned via manifest.toml), runs the ablation
matrix over a configurable sample of instances, and prints the retrieval report.

The QA layer is opt-in via --qa (needs ANTHROPIC_API_KEY and the bench dep group).

Usage examples:
    uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --limit 50
    uv run --group bench python -m cairn_bench.run --dataset locomo
    uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --qa

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

_QA_ARM_NAME = "hybrid+graph-boost"


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
    ap.add_argument(
        "--qa",
        action="store_true",
        help=(
            "Run QA accuracy on the hybrid+graph-boost arm for answerable queries. "
            "Requires ANTHROPIC_API_KEY."
        ),
    )
    ap.add_argument(
        "--qa-model",
        default="claude-sonnet-4-6",
        dest="qa_model",
        help="Anthropic model used for QA generate+judge (default: claude-sonnet-4-6).",
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

    # Locate the QA arm once (used only when --qa is set).
    qa_arm = next((a for a in ARMS if a.name == _QA_ARM_NAME), None)

    # QA accumulator: (correct_count, total_count).
    qa_correct = 0
    qa_total = 0
    provider = None

    if args.qa:
        # Lazy import so the retrieval path has no anthropic dependency.
        from cairn_bench.qa.provider import AnthropicProvider

        try:
            provider = AnthropicProvider(model=args.qa_model)
        except (KeyError, Exception) as exc:  # noqa: BLE001
            print(f"--qa requires ANTHROPIC_API_KEY; skipping QA. ({exc})")
            provider = None

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

                    # QA pass — one arm, answerable queries only.
                    if args.qa and provider is not None and qa_arm is not None:
                        if not q.is_abstention and q.answer:
                            from cairn.search import search as cairn_search
                            from cairn_bench.qa import generate as qa_generate
                            from cairn_bench.qa import judge as qa_judge

                            hits = cairn_search(
                                con,
                                q.question,
                                embedder=emb,
                                k=10,
                                pool=max(200, chunks),
                                graph_boost=True,
                            )
                            ans = qa_generate.generate_answer(
                                con, q.question, hits, provider=provider
                            )
                            correct = qa_judge.judge(
                                q.question,
                                gold=q.answer,
                                response=ans,
                                question_type=q.category,
                                is_abstention=q.is_abstention,
                                provider=provider,
                            )
                            if correct:
                                qa_correct += 1
                            qa_total += 1
            finally:
                con.close()

    agg = aggregate(per_query)
    print(to_markdown(agg, granularity="turn"))

    if args.qa:
        if provider is None:
            print("QA skipped: ANTHROPIC_API_KEY not available.")
        else:
            pct = 100.0 * qa_correct / qa_total if qa_total else 0.0
            print(
                f"\nQA accuracy (judge={args.qa_model}, NOT comparable to published "
                f"leaderboards): {qa_correct}/{qa_total} = {pct:.1f}%"
            )


if __name__ == "__main__":
    main()
