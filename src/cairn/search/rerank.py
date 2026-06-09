# SPDX-License-Identifier: Apache-2.0
"""Cross-encoder reranker (fastembed, ONNX, torch-free). ON by default since v1.1:
the LoCoMo benchmark showed it is the largest retrieval lever (+0.11 recall@5 over
hybrid). Validated on conversational/prose data; ms-marco is tuned for short passages,
so code-heavy vaults are unvalidated — disable with CAIRN_RERANK=0 if it underperforms.
Lazy singleton; only constructed when a rerank actually runs."""

from __future__ import annotations

_RERANKER = None
_RERANKER_NAME = "Xenova/ms-marco-MiniLM-L-6-v2"


def _get_reranker():
    global _RERANKER
    if _RERANKER is None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        _RERANKER = TextCrossEncoder(model_name=_RERANKER_NAME)
    return _RERANKER


def rerank_candidates(query: str, candidates: list[dict], *, top_k: int = 8) -> list[dict]:
    """Rerank `candidates` (each a dict with a 'text' key) by cross-encoder relevance
    to `query`. Bound the input to ~20 before calling. Scores are NOT normalized;
    sort by them descending and return the top_k."""
    if not candidates:
        return []
    scores = list(_get_reranker().rerank(query, [c["text"] for c in candidates]))
    ranked = sorted(
        ({**c, "rerank_score": float(s)} for c, s in zip(candidates, scores, strict=True)),
        key=lambda c: c["rerank_score"],
        reverse=True,
    )
    return ranked[:top_k]
