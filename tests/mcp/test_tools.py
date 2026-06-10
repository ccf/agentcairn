# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from cairn.embed import FakeEmbedder
from cairn.index import open_index, reconcile
from cairn.mcp.tools import build_context_tool, recall_tool, recent_tool, remember_tool, search_tool
from cairn.vault import parse_note


def _build_index(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\n"
        "Pour over coffee brewing.\n\nSee also [[tea]].\n"
    )
    (vault / "tea.md").write_text(
        "---\ntitle: Tea\npermalink: tea\n---\nGreen tea steeping is calming.\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    return idx


def test_search_tool_returns_compact_hits(tmp_path):
    idx = _build_index(tmp_path)
    out = search_tool(str(idx), "coffee brewing", embedder="fake", k=5)
    assert out["query"] == "coffee brewing"
    assert isinstance(out["hits"], list) and out["hits"]
    h = out["hits"][0]
    assert set(h) >= {"permalink", "heading_path", "snippet", "score"}
    # compact: no full note body in a search hit
    assert "text" not in h


def test_search_tool_k_zero_returns_empty(tmp_path):
    # k=0 must return no hits, not collect every distinct note.
    idx = _build_index(tmp_path)
    assert search_tool(str(idx), "coffee", embedder="fake", k=0)["hits"] == []


def test_recall_tool_k_zero_returns_empty(tmp_path):
    idx = _build_index(tmp_path)
    assert recall_tool(str(idx), "coffee", embedder="fake", k=0)["notes"] == []


def test_recall_tool_hydrates_full_notes(tmp_path):
    idx = _build_index(tmp_path)
    out = recall_tool(str(idx), "coffee brewing", embedder="fake", k=2)
    assert out["notes"]
    top = out["notes"][0]
    assert "permalink" in top and "text" in top  # full text hydrated
    assert "Pour over coffee" in top["text"] or "coffee" in top["text"].lower()


def test_build_context_returns_note_and_neighbors(tmp_path):
    idx = _build_index(tmp_path)
    out = build_context_tool(str(idx), "coffee")
    assert out["root"]["permalink"] == "coffee"
    # coffee links to [[tea]] -> tea resolves as an outgoing neighbor
    outgoing = {n["permalink"] for n in out["outgoing"] if n.get("permalink")}
    assert "tea" in outgoing


def test_build_context_missing_permalink(tmp_path):
    idx = _build_index(tmp_path)
    out = build_context_tool(str(idx), "nonexistent")
    assert out["root"] is None
    assert out["outgoing"] == [] and out["incoming"] == []


def test_recent_tool_lists_notes(tmp_path):
    idx = _build_index(tmp_path)
    out = recent_tool(str(idx), n=10)
    perms = {r["permalink"] for r in out["notes"]}
    assert {"coffee", "tea"} <= perms
    assert all({"permalink", "title", "path", "type"} <= set(r) for r in out["notes"])


def test_remember_writes_redacted_note(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    out = remember_tool(
        str(vault),
        "Always pin the store path. The old key was ghp_16C7e42F292c6912E7710c838347Ae178B4a.",
        title="store path rule",
        tags=["ops"],
    )
    assert out["permalink"]
    path = Path(out["path"])
    assert vault in path.resolve().parents
    assert out["redactions"] >= 1
    body = path.read_text()
    # secret never lands on disk; redaction marker present
    assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in body
    assert "[REDACTED" in body
    # round-trips through the real parser
    parsed = parse_note(body)
    assert parsed.frontmatter["type"] == "memory"
    assert "ops" in parsed.frontmatter["tags"]


def test_remember_rejects_empty_text(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    with pytest.raises(ValueError):
        remember_tool(str(vault), "   ")


# ---------------------------------------------------------------------------
# Fix B: title and tags must be redacted before write
# ---------------------------------------------------------------------------


def test_remember_redacts_secret_in_title(tmp_path):
    """A token in the caller-supplied title must NOT reach the written file."""
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    out = remember_tool(str(vault), "harmless body text here", title=f"key {secret}")
    body = Path(out["path"]).read_text()
    assert secret not in body, "secret in title leaked to disk"
    assert "[REDACTED" in body


def test_remember_redacts_secret_in_tags(tmp_path):
    """A token passed as a tag must NOT reach the written file."""
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    out = remember_tool(str(vault), "harmless body text here", tags=[secret])
    body = Path(out["path"]).read_text()
    assert secret not in body, "secret in tags leaked to disk"
    assert "[REDACTED" in body


def test_remember_redaction_count_includes_title_and_tags(tmp_path):
    """The reported `redactions` count must include title + tag redactions, not
    just the body — else a secret only in the title reports 0 (misrepresents)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    secret = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    # secret ONLY in title (body is clean) -> count must still be >= 1
    out = remember_tool(str(vault), "harmless body text here", title=f"key {secret}")
    assert out["redactions"] >= 1
    # secret only in a tag -> counted too
    out2 = remember_tool(str(vault), "another harmless body", tags=[secret])
    assert out2["redactions"] >= 1


# ---------------------------------------------------------------------------
# Fix D: search_tool must not return duplicate permalinks
# ---------------------------------------------------------------------------


def _build_index_chunky(tmp_path: Path) -> tuple[Path, Path]:
    """Build an index with a note long enough to produce >=2 chunks (>1500 chars)."""
    vault = tmp_path / "vault"
    vault.mkdir()
    paragraph = (
        "Chunking is important for retrieval. "
        "Each paragraph adds content to ensure this note exceeds the chunk size limit. "
    )
    # Repeat to exceed 1500 chars so the note produces multiple chunks.
    long_body = (paragraph * 20).strip()
    (vault / "long.md").write_text(
        f"---\ntitle: Long Note\npermalink: long-note\n---\n{long_body}\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    return idx, vault


def test_search_tool_no_duplicate_permalinks(tmp_path):
    """search_tool must return at most one hit per permalink (deduped by best score)."""
    idx, _vault = _build_index_chunky(tmp_path)
    out = search_tool(str(idx), "chunking retrieval", embedder="fake", k=20)
    perms = [h["permalink"] for h in out["hits"]]
    assert len(perms) == len(set(perms)), f"Duplicate permalinks in hits: {perms}"


# ---------------------------------------------------------------------------
# Fix 1+4: search_tool & recall_tool return up to k DISTINCT notes
# (over-fetch chunks, dedup by permalink)
# ---------------------------------------------------------------------------


def _build_index_multi_chunky(tmp_path: Path) -> Path:
    """Build an index where one note monopolises the top-k chunk slots.

    alpha-note has ~4 chunks (body ~4500 chars) and is highly relevant to
    the test query; beta-note and gamma-note are short (1 chunk each) with
    completely unrelated content (gardening).  Searching for the alpha topic
    with k=3 causes the naive implementation to return all 3 slots from
    alpha-note, collapsing to 1 distinct note after dedup.  The fix must
    over-fetch and then dedup to surface all 3 notes.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    alpha_phrase = "retrieval augmented generation combines dense and sparse methods for memory. "
    # alpha: ~4500 chars → 4 chunks (chunk limit is 1500 chars)
    alpha_body = (alpha_phrase * 60).strip()
    (vault / "alpha.md").write_text(
        f"---\ntitle: alpha-note\npermalink: alpha-note\n---\n{alpha_body}\n"
    )
    # beta and gamma: completely unrelated content — one chunk each, will rank below alpha
    unrelated = "Gardening is a relaxing hobby. Soil preparation is key for healthy plants. "
    unrelated_body = (unrelated * 3).strip()
    (vault / "beta.md").write_text(
        f"---\ntitle: beta-note\npermalink: beta-note\n---\n{unrelated_body}\n"
    )
    (vault / "gamma.md").write_text(
        f"---\ntitle: gamma-note\npermalink: gamma-note\n---\n{unrelated_body} extra content\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    # Sanity: alpha must have ≥3 chunks for the naive top-3 to be all-alpha.
    import duckdb as _ddb

    con2 = _ddb.connect(str(idx))
    alpha_chunks = con2.execute(
        "SELECT count(*) FROM chunks WHERE note_permalink = 'alpha-note'"
    ).fetchone()[0]
    con2.close()
    assert alpha_chunks >= 3, f"alpha-note has {alpha_chunks} chunks; increase alpha_body"
    return idx


def test_search_tool_returns_k_distinct_notes(tmp_path):
    """search_tool(k=3) must return 3 DISTINCT notes even when one note has ≥3 chunks."""
    idx = _build_index_multi_chunky(tmp_path)
    out = search_tool(str(idx), "retrieval augmented generation", embedder="fake", k=3)
    perms = [h["permalink"] for h in out["hits"]]
    assert len(set(perms)) == 3, f"Expected 3 distinct notes, got: {perms}"
    assert len(perms) == len(set(perms)), f"Duplicate permalinks: {perms}"


def test_recall_tool_returns_k_distinct_notes(tmp_path):
    """recall_tool(k=3) must hydrate 3 DISTINCT notes even when one note has ≥3 chunks."""
    idx = _build_index_multi_chunky(tmp_path)
    out = recall_tool(str(idx), "retrieval augmented generation", embedder="fake", k=3)
    perms = [n["permalink"] for n in out["notes"]]
    assert len(set(perms)) == 3, f"Expected 3 distinct notes, got: {perms}"
    assert len(perms) == len(set(perms)), f"Duplicate permalinks: {perms}"


# ---------------------------------------------------------------------------
# Fix 5: read tools raise clean ValueError when index is missing
# ---------------------------------------------------------------------------


def test_search_tool_missing_index_raises_valueerror(tmp_path):
    """search_tool must raise ValueError (not a cryptic crash) when index is absent."""
    missing = str(tmp_path / "nope.duckdb")
    with pytest.raises(ValueError, match="no index"):
        search_tool(missing, "anything")


def test_recent_tool_missing_index_raises_valueerror(tmp_path):
    """recent_tool must raise ValueError when index is absent."""
    missing = str(tmp_path / "nope.duckdb")
    with pytest.raises(ValueError, match="no index"):
        recent_tool(missing)


# ---------------------------------------------------------------------------
# Bugbot fix: search_tool/recall_tool return k DISTINCT notes under rerank=True
# ---------------------------------------------------------------------------


def _build_index_rerank_dominant(tmp_path: Path) -> Path:
    """Build an index where alpha-note completely dominates BM25 + cosine scores.

    Strategy: alpha uses a unique token ('xyzretrieval') repeated many times across
    many chunks.  beta and gamma use completely different (non-matching) tokens.
    The query targets 'xyzretrieval', so alpha's chunks fill all top-k BM25 slots.

    With the engine bug (limit=20 for rerank), search(k=25) fetches only 20
    candidates — all from alpha.  The dedup then yields 1 distinct note, not 3.
    After the fix (limit=max(20,k)=25), the 25-candidate pool still comes entirely
    from alpha (since alpha has 25+ chunks), so the dedup still yields 1 note.

    To make the test observable we verify the candidate count via monkeypatching,
    not the dedup output (which depends on data distribution).
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    # alpha: 25+ chunks (phrase ~55 chars × 700 reps ≈ 38 500 chars, chunk limit ~1500 chars)
    # Use a unique query token so alpha dominates BM25.
    alpha_phrase = "xyzretrieval dense sparse memory augmented generation. "
    alpha_body = (alpha_phrase * 700).strip()
    (vault / "alpha.md").write_text(
        f"---\ntitle: alpha-note\npermalink: alpha-note\n---\n{alpha_body}\n"
    )
    # beta/gamma: no query tokens at all → zero BM25 score for the query
    unrelated = "Gardening is a relaxing hobby. Soil preparation is key for healthy plants. "
    (vault / "beta.md").write_text(
        f"---\ntitle: beta-note\npermalink: beta-note\n---\n{(unrelated * 5).strip()}\n"
    )
    (vault / "gamma.md").write_text(
        f"---\ntitle: gamma-note\npermalink: gamma-note\n---\n{(unrelated * 5).strip()} extra.\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()

    # Sanity: alpha must produce >20 chunks so the 20-cap definitely bites.
    import duckdb as _ddb

    con2 = _ddb.connect(str(idx))
    n = con2.execute("SELECT count(*) FROM chunks WHERE note_permalink = 'alpha-note'").fetchone()[
        0
    ]
    assert n > 20, f"alpha-note has only {n} chunks; need >20 to exercise the 20-cap bug"
    con2.close()
    return idx


def test_search_tool_rerank_candidate_count_honors_k(tmp_path, monkeypatch):
    """With rerank=True, the engine must pass max(20, k) candidates to the reranker.

    The Bugbot bug: `limit=(20 if rerank else k)` hard-caps the rerank fetch at 20
    regardless of the k passed by the tool layer.  The fix changes this to
    `limit=(max(20, k) if rerank else k)`.

    This test uses a monkeypatched rerank stub to observe the candidate count
    received by the reranker — the key invariant exposed by the bug.
    """
    idx = _build_index_rerank_dominant(tmp_path)

    received: list[int] = []

    def capturing_rerank(query, candidates, *, top_k):
        received.append(len(candidates))
        sliced = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[:top_k]
        return [{**c, "rerank_score": 1.0 - i * 0.01} for i, c in enumerate(sliced)]

    monkeypatch.setattr("cairn.search.engine.rerank_candidates", capturing_rerank)

    # k=3 → tool requests fetch = max(3*5, 25) = 25 candidates from engine.
    # Before fix: engine caps at 20, reranker sees 20.
    # After fix: engine fetches max(20, 25) = 25, reranker sees 25.
    search_tool(str(idx), "xyzretrieval dense sparse", embedder="fake", k=3, rerank=True)

    assert received, "rerank stub was never called"
    # After the fix the reranker must receive at least 25 candidates (the tool's fetch value).
    assert received[0] >= 25, (
        f"Expected reranker to receive >=25 candidates for k=3 (fetch=25), got {received[0]}. "
        "The 20-cap Bugbot bug is still present."
    )


# ---------------------------------------------------------------------------
# Bugbot Fix 1: _embedder must cache — same instance returned across calls
# ---------------------------------------------------------------------------


def test_embedder_returns_cached_instance():
    """_embedder("fake") must return the SAME object on repeated calls (lru_cache)."""
    from cairn.mcp.tools import _embedder

    a = _embedder("fake")
    b = _embedder("fake")
    assert a is b, (
        "Expected _embedder to return the same cached instance; got two different objects"
    )


def test_embedder_none_returns_none():
    """_embedder(None) and _embedder('none') must return None (bypass cache)."""
    from cairn.mcp.tools import _embedder

    assert _embedder(None) is None
    assert _embedder("none") is None


# ---------------------------------------------------------------------------
# Task 4: validity annotation in search_tool, recall_tool, build_context_tool
# ---------------------------------------------------------------------------


def _build_validity_index(tmp_path: Path) -> Path:
    """Build an index with one current, one superseded, and one expired note."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "current.md").write_text(
        "---\ntitle: Current\npermalink: current\n---\nfavorite color is green\n"
    )
    (vault / "old.md").write_text(
        "---\ntitle: Old\npermalink: old\nsuperseded_by: current\n---\nfavorite color is blue\n"
    )
    (vault / "expired.md").write_text(
        "---\ntitle: Expired\npermalink: expired\nvalid_until: 2020-01-01\n"
        "---\nfavorite color was red\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    return idx


def test_search_tool_annotates_validity(tmp_path):
    """search_tool hits must carry a validity sub-dict with correct status; no TypeError."""
    idx = _build_validity_index(tmp_path)
    out = search_tool(str(idx), "favorite color", embedder="fake", k=10)
    assert "as_of" in out, "search_tool must include top-level as_of"
    hits_by_perm = {h["permalink"]: h for h in out["hits"]}
    assert "current" in hits_by_perm, "current note must appear in hits"
    h_current = hits_by_perm["current"]
    assert "validity" in h_current, f"hit missing validity sub-dict: {h_current}"
    assert h_current["validity"]["status"] == "current"

    if "old" in hits_by_perm:
        h_old = hits_by_perm["old"]
        assert "validity" in h_old
        assert h_old["validity"]["status"] == "superseded"
        assert h_old["validity"]["superseded_by"] == "current"

    if "expired" in hits_by_perm:
        h_exp = hits_by_perm["expired"]
        assert "validity" in h_exp
        assert h_exp["validity"]["status"] == "expired"


def test_recall_tool_annotates_validity(tmp_path):
    """recall_tool notes must carry a validity sub-dict with correct status; no TypeError."""
    idx = _build_validity_index(tmp_path)
    out = recall_tool(str(idx), "favorite color", embedder="fake", k=10)
    assert "as_of" in out, "recall_tool must include top-level as_of"
    notes_by_perm = {n["permalink"]: n for n in out["notes"]}
    assert "current" in notes_by_perm, "current note must appear in notes"
    n_current = notes_by_perm["current"]
    assert "validity" in n_current, f"note missing validity sub-dict: {n_current}"
    assert n_current["validity"]["status"] == "current"

    if "old" in notes_by_perm:
        n_old = notes_by_perm["old"]
        assert "validity" in n_old
        assert n_old["validity"]["status"] == "superseded"

    if "expired" in notes_by_perm:
        n_exp = notes_by_perm["expired"]
        assert "validity" in n_exp
        assert n_exp["validity"]["status"] == "expired"


def test_build_context_tool_annotates_validity(tmp_path):
    """build_context_tool root and resolved neighbors must carry validity sub-dict."""
    idx = _build_validity_index(tmp_path)
    out = build_context_tool(str(idx), "old")
    root = out["root"]
    assert root is not None
    assert "validity" in root, f"root missing validity: {root}"
    assert root["validity"]["status"] == "superseded"


def test_no_type_error_on_validity_annotation(tmp_path):
    """Annotating a note with valid_from/valid_until must not raise TypeError.

    The fix in Commit 1 ensures Hit.valid_from is an aware-UTC ISO string
    ("+00:00"), so _parse_iso returns an aware datetime that compares cleanly
    against datetime.now(UTC) without a naive-vs-aware TypeError.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        "---\ntitle: Note\npermalink: note\nvalid_from: 2024-06-15T10:30:45Z\n"
        "valid_until: 2099-01-01T00:00:00Z\n---\nsome content here\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(vault), emb)
    con.close()
    # Must not raise TypeError (naive vs aware comparison)
    out = search_tool(str(idx), "content", embedder="fake", k=5)
    assert out["hits"]
    h = out["hits"][0]
    assert h["validity"]["status"] == "current"
    assert h["validity"]["valid_from"] is not None
    assert h["validity"]["valid_until"] is not None


# ---------------------------------------------------------------------------
# Bugbot Fix 2: search_tool/recall_tool must pass pool >= fetch to search()
# ---------------------------------------------------------------------------


def test_search_tool_passes_pool_ge_fetch(tmp_path, monkeypatch):
    """search_tool must pass pool >= fetch (k*_FETCH_FACTOR, min 25) to search().

    With k=100, fetch = max(100*5, 25) = 500.  The default pool=200 is less than
    500, so the CTE LIMITs would cap candidates below the over-fetch target.
    After the fix, pool=max(200, fetch)=500 is forwarded so the pool matches.
    """
    idx = _build_index(tmp_path)

    recorded: list[dict] = []

    def spy(con, query, **kw):
        recorded.append(dict(kw))
        return []

    monkeypatch.setattr("cairn.mcp.tools.search", spy)

    from cairn.mcp.tools import search_tool

    search_tool(str(idx), "q", embedder="fake", k=100)

    assert recorded, "search() spy was never called"
    kw = recorded[0]
    fetch = kw.get("k", 0)
    pool = kw.get("pool", 200)  # 200 is search()'s default — failing if not passed
    assert pool >= fetch, (
        f"pool={pool} < fetch={fetch}: search_tool does not widen pool to match over-fetch"
    )


def test_recall_tool_passes_pool_ge_fetch(tmp_path, monkeypatch):
    """recall_tool must pass pool >= fetch to search(), same invariant as search_tool."""
    idx = _build_index(tmp_path)

    recorded: list[dict] = []

    def spy(con, query, **kw):
        recorded.append(dict(kw))
        return []

    monkeypatch.setattr("cairn.mcp.tools.search", spy)

    from cairn.mcp.tools import recall_tool

    recall_tool(str(idx), "q", embedder="fake", k=100)

    assert recorded, "search() spy was never called"
    kw = recorded[0]
    fetch = kw.get("k", 0)
    pool = kw.get("pool", 200)  # 200 is search()'s default — failing if not passed
    assert pool >= fetch, (
        f"pool={pool} < fetch={fetch}: recall_tool does not widen pool to match over-fetch"
    )
