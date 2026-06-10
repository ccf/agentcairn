# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from cairn.embed import FakeEmbedder
from cairn.search import hybrid_search, open_search
from tests.search.test_engine import build_index


def test_hybrid_search_returns_ranked_hits(tmp_path):
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    qvec = emb.embed_query("coffee brewing")
    hits = hybrid_search(con, "coffee brewing", qvec, dim=emb.dim, limit=5)
    assert hits, "no hybrid hits"
    h = hits[0]
    assert set(h.keys()) == {
        "chunk_id",
        "note_permalink",
        "heading_path",
        "snippet",
        "score",
        "valid_from",
        "valid_until",
        "superseded_by",
    }
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


def _build_large_index(tmp_path: Path, emb) -> str:
    """Build an index with >20 chunks to exercise the 20-cap rerank bug.

    Generates notes whose bodies span multiple chunks (chunk size ~1500 chars).
    Each note shares the query token 'coffee' so they all surface in BM25.
    """
    from cairn.index import build_fts, index_vault, open_index

    v = tmp_path / "large_vault"
    v.mkdir()
    phrase = "coffee brewing extraction techniques for optimal flavor. "
    # 5 notes × ~5 chunks each ≈ 25 chunks; phrase ~57 chars × 130 reps ≈ 7400 chars ≈ 5 chunks
    for i in range(5):
        body = (phrase * 130).strip() + f" note-variant-{i}."
        (v / f"note{i}.md").write_text(f"---\ntitle: Note{i}\npermalink: note{i}\n---\n{body}\n")
    idx = str(tmp_path / "large.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    build_fts(con)
    con.close()
    return idx


def test_graph_boost_toggle_changes_score(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    vault = tmp_path / "v"
    vault.mkdir()
    # note "tea" is a link target of "coffee" -> graph-boost applies to tea
    (vault / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\nbrewing tea methods. See [[tea]].\n"
    )
    (vault / "tea.md").write_text(
        "---\ntitle: Tea\npermalink: tea\n---\nbrewing tea steeping methods.\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(vault), emb)
    con0.close()

    con = open_search(str(idx))
    try:
        on = {
            h.permalink: h.score for h in search(con, "brewing tea", embedder=emb, graph_boost=True)
        }  # noqa: E501
        off = {
            h.permalink: h.score
            for h in search(con, "brewing tea", embedder=emb, graph_boost=False)
        }  # noqa: E501
        default = {h.permalink: h.score for h in search(con, "brewing tea", embedder=emb)}
    finally:
        con.close()
    # tea is a link target -> boosted when on, not when off
    assert on["tea"] > off["tea"]
    assert default["tea"] == on["tea"]  # default is graph_boost=True


def test_bm25_only_graph_boost_toggle(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    vault = tmp_path / "v"
    vault.mkdir()
    # note "tea" is a link target of "coffee" -> graph-boost applies to tea
    (vault / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\nbrewing tea methods. See [[tea]].\n"
    )
    (vault / "tea.md").write_text(
        "---\ntitle: Tea\npermalink: tea\n---\nbrewing tea steeping methods.\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(vault), emb)
    con0.close()

    con = open_search(str(idx))
    try:
        on = {
            h.permalink: h.score
            for h in search(con, "brewing tea", embedder=None, graph_boost=True)
        }
        off = {
            h.permalink: h.score
            for h in search(con, "brewing tea", embedder=None, graph_boost=False)
        }
        default = {h.permalink: h.score for h in search(con, "brewing tea", embedder=None)}
    finally:
        con.close()
    # tea is a link target -> boosted when on, not when off
    assert on["tea"] > off["tea"]
    assert default["tea"] == on["tea"]  # default is graph_boost=True


def test_validity_soft_demote(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    v = tmp_path / "v"
    v.mkdir()
    # two notes about the same topic; "old" is superseded by "new"
    (v / "old.md").write_text(
        "---\ntitle: Old\npermalink: old\nsuperseded_by: new\n---\nfavorite color is blue\n"
    )
    (v / "new.md").write_text("---\ntitle: New\npermalink: new\n---\nfavorite color is green\n")
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(v), emb)
    con0.close()
    con = open_search(str(idx))
    try:
        on = {
            h.permalink: h.score
            for h in search(con, "favorite color", embedder=emb, validity_aware=True)
        }
        off = {
            h.permalink: h.score
            for h in search(con, "favorite color", embedder=emb, validity_aware=False)
        }
        hits = search(con, "favorite color", embedder=emb)
    finally:
        con.close()
    # superseded "old" is demoted when validity_aware (default on)
    assert on["old"] < off["old"]
    # Hit carries validity fields regardless of the toggle
    h_old = next(h for h in hits if h.permalink == "old")
    assert h_old.superseded_by == "new"


def test_expired_note_demoted_vs_current(tmp_path):
    """An expired note (valid_until in the past) must be soft-demoted vs a current note.

    This exercises the corrected db_now() naive-UTC bind: if the SQL now-param were
    timezone-aware DuckDB would coerce it to local time, potentially breaking the
    comparison against the naive-UTC stored values.
    """
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    v = tmp_path / "v"
    v.mkdir()
    # "old" expired in 2020 — valid_until is definitively in the past
    (v / "old.md").write_text(
        "---\ntitle: Old\npermalink: old\nvalid_until: 2020-01-01\n---\nfavorite color is blue\n"
    )
    (v / "new.md").write_text("---\ntitle: New\npermalink: new\n---\nfavorite color is green\n")
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(v), emb)
    con0.close()

    con = open_search(str(idx))
    try:
        on = {
            h.permalink: h.score
            for h in search(con, "favorite color", embedder=emb, validity_aware=True)
        }
        off = {
            h.permalink: h.score
            for h in search(con, "favorite color", embedder=emb, validity_aware=False)
        }
    finally:
        con.close()
    # expired "old" is demoted when validity_aware=True vs off
    assert on["old"] < off["old"], (
        f"Expired note score on={on['old']:.6f} not < off={off['old']:.6f}; "
        "db_now() naive-UTC bind may be broken."
    )


def test_rerank_fetches_at_least_k_candidates_when_k_exceeds_20(tmp_path, monkeypatch):
    """When rerank=True and k>20, the engine must fetch max(20, k) candidates so the
    reranker sees all k candidates and the 20-cap no longer throttles a larger k.

    This is the Bugbot fix: `limit=(20 if rerank else k)` → `limit=(max(20, k) if rerank else k)`.
    """
    emb = FakeEmbedder(dim=8)
    # Build a large index (>20 chunks) so the 20-cap visibly throttles a k=30 request.
    idx = _build_large_index(tmp_path, emb)
    con = open_search(idx)

    # Verify the index has enough chunks for the test to be meaningful.
    total_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert total_chunks > 20, (
        f"fixture produced only {total_chunks} chunks; need >20 to exercise the rerank cap"
    )

    received_candidate_count: list[int] = []

    def counting_rerank(query, candidates, *, top_k):
        received_candidate_count.append(len(candidates))
        # Identity-style reranker: return first top_k candidates with a score.
        sliced = candidates[:top_k]
        return [{**c, "rerank_score": 1.0 - i * 0.1} for i, c in enumerate(sliced)]

    monkeypatch.setattr("cairn.search.engine.rerank_candidates", counting_rerank)

    # k=30 is larger than the old hard-coded 20 cap.  After the fix, the reranker
    # must receive all available chunks (bounded only by the pool, not a 20 cap).
    search(con, "coffee brewing", embedder=emb, k=30, rerank=True)
    assert received_candidate_count, "rerank stub was never called"
    # The stub must have received MORE than 20 candidates (old cap), proving the fix.
    assert received_candidate_count[0] > 20, (
        f"Expected >20 candidates passed to reranker for k=30, got {received_candidate_count[0]}. "
        "The 20-cap bug is still present."
    )
