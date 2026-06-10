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
from cairn_bench import download, token_savings
from cairn_bench.ablation import run_arm
from cairn_bench.adapters import locomo, longmemeval
from cairn_bench.build import build_scoped_index
from cairn_bench.config import ARMS
from cairn_bench.report import (
    aggregate,
    aggregate_by_category,
    aggregate_qa,
    qa_to_markdown,
    to_markdown,
)

KS = [1, 3, 5, 10, 20]

_QA_ARM_NAME = "hybrid+graph-boost"


def _print_category_retrieval(cat_agg: dict) -> None:
    """Print a per-category retrieval table from aggregate_by_category output."""
    print("\n### Per-category retrieval — turn-level (macro-avg)\n")
    print("| arm | category | recall@5 | recall@10 | ndcg@10 | mrr |")
    print("|---|---|---|---|---|---|")
    for arm, cats in sorted(cat_agg.items()):
        for cat, grans in sorted(cats.items(), key=lambda x: str(x[0])):
            m = grans.get("turn", {})
            print(
                f"| {arm} | {cat} | {m.get('recall@5', 0):.3f} | "
                f"{m.get('recall@10', 0):.3f} | {m.get('ndcg@10', 0):.3f} | "
                f"{m.get('mrr', 0):.3f} |"
            )


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
            "Run QA accuracy on the hybrid+graph-boost arm for answerable AND abstention "
            "queries. Requires ANTHROPIC_API_KEY. Answerable and abstention accuracy are "
            "reported separately."
        ),
    )
    ap.add_argument(
        "--qa-model",
        default="claude-sonnet-4-6",
        dest="qa_model",
        help="Anthropic model used for QA generate+judge (default: claude-sonnet-4-6).",
    )
    ap.add_argument(
        "--token-savings",
        action="store_true",
        dest="token_savings",
        help=(
            "Measure context-token savings (recalled top-k vs. full haystack) instead of the "
            "ablation matrix. Retrieval-only; no API key needed."
        ),
    )
    ap.add_argument(
        "--ts-k",
        type=int,
        default=10,
        dest="ts_k",
        help="Top-k recalled chunks for --token-savings (default: 10).",
    )
    ap.add_argument(
        "--with-token-savings",
        action="store_true",
        dest="with_token_savings",
        help=(
            "Also measure context-token savings during the ablation run (one extra recall per "
            "answerable query), reusing each built index — printed alongside the retrieval tables. "
            "Use this to get both the ablation and the savings from a single (expensive) pass."
        ),
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

    # Lean token-savings path: measure recalled vs. full-haystack tokens, then exit.
    if args.token_savings:
        ts_rows: list[dict] = []
        for notes, queries in records:
            with tempfile.TemporaryDirectory() as d:
                con, chunks = build_scoped_index(notes, Path(d), emb)
                try:
                    full = token_savings.full_haystack_tokens(con)
                    for q in queries:
                        rec = token_savings.recalled_tokens(
                            con, q.question, emb, k=args.ts_k, pool=max(200, chunks)
                        )
                        ts_rows.append({"full": full, "recalled": rec})
                finally:
                    con.close()
        print(token_savings.to_markdown(ts_rows, k=args.ts_k))
        return

    # Locate the QA arm once (used only when --qa is set).
    qa_arm = next((a for a in ARMS if a.name == _QA_ARM_NAME), None)

    # QA accumulators: rows list for aggregate_qa, plus simple per-type counters.
    qa_rows: list[dict] = []
    ts_rows: list[dict] = []  # context-token savings, when --with-token-savings
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
                # Per-record full-haystack token count (reused for every query in the record).
                full_tokens = (
                    token_savings.full_haystack_tokens(con) if args.with_token_savings else 0
                )
                for q in queries:
                    # Retrieval pass: skip abstention/empty-gold queries (Zep invariant).
                    if not q.gold_turns and not q.gold_sessions:
                        pass  # fall through to QA abstention path below
                    else:
                        for arm in ARMS:
                            res = run_arm(con, arm, q, emb, ks=KS, pool=max(200, chunks))
                            per_query.append({"arm": arm.name, "category": q.category, **res})
                        if args.with_token_savings:
                            ts_rows.append(
                                {
                                    "full": full_tokens,
                                    "recalled": token_savings.recalled_tokens(
                                        con, q.question, emb, k=args.ts_k, pool=max(200, chunks)
                                    ),
                                }
                            )

                    # QA pass — answerable and abstention queries, one arm each.
                    if args.qa and provider is not None and qa_arm is not None:
                        run_qa = (not q.is_abstention and bool(q.answer)) or q.is_abstention
                        if run_qa:
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
                            qa_rows.append(
                                {
                                    "category": q.category,
                                    "is_abstention": q.is_abstention,
                                    "correct": correct,
                                }
                            )
            finally:
                con.close()

    agg = aggregate(per_query)
    print(to_markdown(agg, granularity="turn"))
    # Session-level too — the granularity comparable to LongMemEval's published recall@k.
    if any(grans.get("session") for grans in agg.values()):
        print()
        print(to_markdown(agg, granularity="session"))

    if args.with_token_savings and ts_rows:
        print()
        print(token_savings.to_markdown(ts_rows, k=args.ts_k))

    if args.qa:
        if provider is None:
            print("QA skipped: ANTHROPIC_API_KEY not available.")
        elif qa_rows:
            qa_agg = aggregate_qa(qa_rows)
            print(qa_to_markdown(qa_agg, judge_model=args.qa_model))
            # Also print per-category retrieval breakdown.
            if per_query:
                cat_agg = aggregate_by_category(per_query)
                _print_category_retrieval(cat_agg)
        else:
            print("QA skipped: no qualifying queries processed.")


if __name__ == "__main__":
    main()
