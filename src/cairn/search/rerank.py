# SPDX-License-Identifier: Apache-2.0
"""Optional cross-encoder reranker (fastembed, ONNX, torch-free). OFF by default:
ms-marco cross-encoders can underperform on markdown/code (domain shift), so
enable per corpus only after validating. Lazy singleton — never on the hot path."""

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
