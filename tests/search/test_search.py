# SPDX-License-Identifier: Apache-2.0
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
