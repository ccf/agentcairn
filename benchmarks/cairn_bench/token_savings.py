# SPDX-License-Identifier: Apache-2.0
"""Context-token savings: how much smaller is the recalled context vs. the full haystack.

An HONEST, transparent model — not a vendored headline number:
- "full"     = every chunk of the indexed haystack (what you'd paste if you dumped the vault).
- "recalled" = the top-k chunks agentcairn actually returns for a query (default config:
               hybrid fusion + cross-encoder reranker).
- reduction  = full / recalled, per query; we report mean/median plus the raw token sizes.

Tokens are ESTIMATED with a documented ~4-chars/token heuristic (zero-dep, model-agnostic).
This is a model of context size, NOT a measured dollar cost, and the report says so.
"""

from __future__ import annotations

import statistics

from cairn.search import get_chunks, search
from cairn.usage import estimate_tokens  # shared estimator (identical to the package)

__all__ = ["estimate_tokens", "full_haystack_tokens", "recalled_tokens", "summarize", "to_markdown"]


def full_haystack_tokens(con) -> int:
    """Estimated tokens for the entire indexed haystack (all chunks)."""
    rows = con.execute("SELECT text FROM chunks").fetchall()
    return sum(estimate_tokens(r[0]) for r in rows)


def recalled_tokens(con, query_text: str, embedder, *, k: int, pool: int) -> int:
    """Estimated tokens of the top-k chunks agentcairn returns for a query, using the
    default retrieval config (hybrid + reranker). Full chunk text (not the snippet)."""
    hits = search(con, query_text, embedder=embedder, k=k, pool=pool, rerank=True)
    ids = [h.chunk_id for h in hits]
    by_id = {c["chunk_id"]: c for c in get_chunks(con, ids)}
    return sum(estimate_tokens(by_id[i]["text"]) for i in ids if i in by_id)


def summarize(rows: list[dict]) -> dict:
    """Aggregate per-query {full, recalled} rows into summary stats.

    Reduction factors are computed only for rows with recalled > 0.
    """
    fulls = [r["full"] for r in rows]
    recs = [r["recalled"] for r in rows]
    factors = [r["full"] / r["recalled"] for r in rows if r["recalled"] > 0]
    return {
        "queries": len(rows),
        "mean_full": (sum(fulls) / len(fulls)) if fulls else 0.0,
        "mean_recalled": (sum(recs) / len(recs)) if recs else 0.0,
        "mean_factor": statistics.mean(factors) if factors else 0.0,
        "median_factor": statistics.median(factors) if factors else 0.0,
    }


def to_markdown(rows: list[dict], *, k: int) -> str:
    """Render the token-savings summary as a Markdown block."""
    if not rows:
        return "### Context-token savings\n\n_No queries measured._"
    s = summarize(rows)
    saved = s["mean_full"] - s["mean_recalled"]
    return (
        "### Context-token savings (estimated, ~4 chars/token)\n\n"
        f"- queries: {s['queries']} · recall k = {k} · default config (hybrid + reranker)\n"
        f"- mean haystack: {s['mean_full']:,.0f} tokens · "
        f"mean recalled context: {s['mean_recalled']:,.0f} tokens\n"
        f"- **context reduction: {s['mean_factor']:.1f}× mean / "
        f"{s['median_factor']:.1f}× median**\n"
        f"- mean tokens saved per query: {saved:,.0f}\n\n"
        "_Estimate, not a measured cost. 'full' = the whole indexed haystack you'd otherwise "
        "carry; 'recalled' = the top-k chunks agentcairn returns. Tokens via a 4-chars/token "
        "heuristic._"
    )
