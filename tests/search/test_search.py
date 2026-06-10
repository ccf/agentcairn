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


def test_rerank_validity_demote_superseded(tmp_path, monkeypatch):
    """After reranking, a superseded note must still be demoted vs a current note.

    Without the fix, rerank_candidates returns candidates purely by cross-encoder
    score, discarding the validity penalty — so a superseded note with a higher
    cross-encoder score would outrank the current one.

    The test forces that exact scenario: monkeypatched reranker gives superseded
    score=1.0, current score=0.9 so without the fix the superseded note wins.
    With validity_aware=True the adjusted scores must be 1.0×0.5=0.5 vs 0.9×1.0=0.9,
    so the current note must come first.  With validity_aware=False no adjustment
    is applied and the superseded note (score 1.0) wins.
    """
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    v = tmp_path / "v"
    v.mkdir()
    (v / "old.md").write_text(
        "---\ntitle: Old\npermalink: old\nsuperseded_by: new\n---\nfavorite color is blue\n"
    )
    (v / "new.md").write_text("---\ntitle: New\npermalink: new\n---\nfavorite color is green\n")
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(v), emb)
    con0.close()

    def fake_rerank_superseded_wins(query, candidates, *, top_k):
        """Give the superseded 'old' note a higher cross-encoder score than 'new'."""
        result = []
        for c in candidates:
            if c["note_permalink"] == "old":
                result.append({**c, "rerank_score": 1.0})
            else:
                result.append({**c, "rerank_score": 0.9})
        return sorted(result, key=lambda x: x["rerank_score"], reverse=True)[:top_k]

    monkeypatch.setattr("cairn.search.engine.rerank_candidates", fake_rerank_superseded_wins)

    con = open_search(str(idx))
    try:
        # validity_aware=True: superseded note must be demoted (0.9×1.0 > 1.0×0.5)
        hits_on = search(con, "favorite color", embedder=emb, k=5, rerank=True, validity_aware=True)
        perms_on = [h.permalink for h in hits_on]
        assert "new" in perms_on and "old" in perms_on, (
            f"Both notes must appear in results; got {perms_on}"
        )
        new_idx = perms_on.index("new")
        old_idx = perms_on.index("old")
        assert new_idx < old_idx, (
            f"Current note 'new' must rank above superseded 'old' when validity_aware=True; "
            f"got order {perms_on}"
        )

        # validity_aware=False: no adjustment; superseded wins with higher cross-encoder score
        hits_off = search(
            con, "favorite color", embedder=emb, k=5, rerank=True, validity_aware=False
        )
        perms_off = [h.permalink for h in hits_off]
        assert "old" in perms_off and "new" in perms_off, (
            f"Both notes must appear with validity_aware=False; got {perms_off}"
        )
        assert perms_off.index("old") < perms_off.index("new"), (
            "With validity_aware=False, superseded 'old' (score 1.0) must rank above 'new' (0.9)"
        )
    finally:
        con.close()


def test_rerank_inert_without_validity_fields(tmp_path, monkeypatch):
    """Notes without any validity frontmatter → factor 1.0 → reranked order unchanged.

    Ensures the fix doesn't perturb results for corpora that have no validity fields.
    """
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    v = tmp_path / "v"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nfavorite color alpha\n")
    (v / "b.md").write_text("---\ntitle: B\npermalink: b\n---\nfavorite color beta\n")
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(v), emb)
    con0.close()

    call_count = [0]

    def fake_rerank_deterministic(query, candidates, *, top_k):
        call_count[0] += 1
        # Return in stable order a→b with scores 0.9, 0.8
        ordered = sorted(candidates, key=lambda c: c["note_permalink"])
        return [{**c, "rerank_score": 0.9 - 0.1 * i} for i, c in enumerate(ordered[:top_k])]

    monkeypatch.setattr("cairn.search.engine.rerank_candidates", fake_rerank_deterministic)

    con = open_search(str(idx))
    try:
        hits = search(con, "favorite color", embedder=emb, k=5, rerank=True, validity_aware=True)
        perms = [h.permalink for h in hits]
        # No validity fields → factor 1.0 everywhere → reranked order is exactly a, b
        assert perms == sorted(perms), f"Inert corpus: order should be a→b, got {perms}"
        # Scores must be the raw reranker scores (×1.0), not modified
        assert hits[0].score == pytest.approx(0.9)
        assert call_count[0] == 1, "rerank stub must have been called exactly once"
    finally:
        con.close()
