# SPDX-License-Identifier: Apache-2.0
import pytest
from tests.search.test_engine import build_index

from cairn.embed import FakeEmbedder
from cairn.search import hybrid_search, open_search


def test_hybrid_search_returns_ranked_hits(tmp_path):
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    qvec = emb.embed_query("coffee brewing")
    hits = hybrid_search(con, "coffee brewing", qvec, dim=emb.dim, limit=5)
    assert hits, "no hybrid hits"
    h = hits[0]
    assert set(h.keys()) == {"chunk_id", "note_permalink", "heading_path", "snippet", "score"}
    scores = [x["score"] for x in hits]
    assert scores == sorted(scores, reverse=True)
    # BM25 term 'coffee' should surface the coffee note near the top
    assert any(x["note_permalink"] == "coffee" for x in hits[:3])


def test_hybrid_search_survives_single_arm(tmp_path):
    # A query that matches NO BM25 term still returns vector hits (never silently dead)
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    qvec = emb.embed_query("zzzznomatch")
    hits = hybrid_search(con, "zzzznomatch", qvec, dim=emb.dim, limit=5)
    assert hits, "vector arm should still return results when BM25 matches nothing"


from cairn.search import Hit, search  # noqa: E402


def test_search_hybrid_with_embedder(tmp_path):
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    hits = search(con, "coffee brewing", embedder=emb, k=5)
    assert hits and isinstance(hits[0], Hit)
    assert hits[0].snippet and hits[0].permalink
    assert any(h.permalink == "coffee" for h in hits[:3])


def test_search_bm25_only_without_embedder(tmp_path):
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    hits = search(con, "tea steeping", embedder=None, k=5)  # no embedder -> BM25-only
    assert hits and any(h.permalink == "tea" for h in hits)


def test_rerank_hit_scores_reflect_reranker_order(tmp_path, monkeypatch):
    """When rerank=True, Hit.score must equal the cross-encoder score (not the RRF
    score), so the returned list is sorted descending by Hit.score."""
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)

    # Monkeypatch rerank_candidates so we control the output order and scores
    # without needing the fastembed model.  The patched function returns the
    # candidates in a deterministic new order, each augmented with a known
    # descending rerank_score.
    def fake_rerank(query, candidates, *, top_k):
        # Reverse the incoming order so the result differs from the RRF order,
        # then attach falling scores 0.9, 0.5, 0.1 … so we can assert exactly.
        reordered = list(reversed(candidates))[:top_k]
        scores = [0.9 - 0.4 * i for i in range(len(reordered))]
        return [{**c, "rerank_score": s} for c, s in zip(reordered, scores, strict=False)]

    monkeypatch.setattr("cairn.search.engine.rerank_candidates", fake_rerank)

    hits = search(con, "coffee brewing", embedder=emb, k=3, rerank=True)
    assert hits, "expected at least one hit"
    scores = [h.score for h in hits]
    # (a) each Hit.score must equal the monkeypatched rerank_score (not the RRF value)
    assert scores[0] == pytest.approx(0.9)
    # (b) the list is non-increasing
    assert scores == sorted(scores, reverse=True)
