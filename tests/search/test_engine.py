# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.embed import FakeEmbedder
from cairn.index import build_fts, index_vault, open_index
from cairn.search import open_search, vector_search


def build_index(tmp_path: Path, emb) -> str:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\n"
        "Pour over coffee brewing.\n\n## Beans\nArabica beans.\n"
    )
    (v / "tea.md").write_text("---\ntitle: Tea\npermalink: tea\n---\nGreen tea steeping.\n")
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    build_fts(con)
    con.close()
    return idx


def test_open_search_and_vector_search(tmp_path):
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    qvec = emb.embed_query("coffee brewing")
    hits = vector_search(con, qvec, dim=emb.dim, pool=10)
    assert hits, "vector search returned nothing"
    # returns (chunk_id, similarity) descending; chunk_id is a VARCHAR
    assert all(isinstance(h[0], str) for h in hits)
    sims = [h[1] for h in hits]
    assert sims == sorted(sims, reverse=True)  # higher sim first


from cairn.search import get_chunks, get_note, search  # noqa: E402


def test_open_search_with_single_quote_in_path(tmp_path):
    """ATTACH path containing a single quote must not raise a ParserException."""
    emb = FakeEmbedder(dim=8)
    # Build the index inside a directory whose name contains a single quote.
    weird_dir = tmp_path / "weird ' dir"
    weird_dir.mkdir()
    idx = build_index(weird_dir, emb)
    con = open_search(idx)
    # A trivial query proves the connection is usable, not just opened.
    count = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert count > 0


def test_progressive_disclosure_hydration(tmp_path):
    emb = FakeEmbedder(dim=8)
    idx = build_index(tmp_path, emb)
    con = open_search(idx)
    hits = search(con, "coffee", embedder=emb, k=3)
    ids = [h.chunk_id for h in hits]
    full = get_chunks(con, ids)  # list-cast hydration
    assert {f["chunk_id"] for f in full} == set(ids)
    assert all(
        "text" in f
        and len(f["text"]) >= len(next(h.snippet for h in hits if h.chunk_id == f["chunk_id"]))
        for f in full
    )
    note = get_note(con, "coffee")
    assert note["permalink"] == "coffee" and note["title"] == "Coffee"


def _build_index(tmp_path, notes):
    """notes: list of (permalink, body). Reindex with the fake embedder."""
    from typer.testing import CliRunner

    from cairn.cli import app

    v = tmp_path / "vault"
    v.mkdir()
    for permalink, body in notes:
        (v / f"{permalink}.md").write_text(
            f"---\ntitle: {permalink}\npermalink: {permalink}\n---\n{body}\n"
        )
    idx = tmp_path / "i.duckdb"
    r = CliRunner().invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    return str(idx)


def test_semantic_neighbors_excludes_self_and_returns_related(tmp_path):
    from cairn.search.engine import open_search, semantic_neighbors

    idx = _build_index(
        tmp_path,
        [
            ("ram", "scale the RAM to 4 gigabytes for the build"),
            ("ram2", "increase memory RAM to 8 gigabytes"),
            ("coffee", "pour over coffee brewing method beans"),
        ],
    )
    con = open_search(idx)
    try:
        rel = semantic_neighbors(con, "ram", k=5)
    finally:
        con.close()
    perms = [r["permalink"] for r in rel]
    assert "ram" not in perms  # excludes self
    assert "ram2" in perms  # a semantically-near note is returned
    assert all("score" in r and "title" in r for r in rel)


def test_semantic_neighbors_excludes_superseded(tmp_path):
    from typer.testing import CliRunner

    from cairn.cli import app
    from cairn.search.engine import open_search, semantic_neighbors

    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha topic widget\n")
    (v / "b.md").write_text(
        "---\ntitle: B\npermalink: b\nsuperseded_by: a\n---\nalpha topic widget\n"
    )
    idx = tmp_path / "i.duckdb"
    assert (
        CliRunner()
        .invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
        .exit_code
        == 0
    )
    con = open_search(str(idx))
    try:
        rel = semantic_neighbors(con, "a", k=5)
    finally:
        con.close()
    assert "b" not in [r["permalink"] for r in rel]  # superseded note never surfaced


def test_semantic_neighbors_missing_note_returns_empty(tmp_path):
    from cairn.search.engine import open_search, semantic_neighbors

    idx = _build_index(tmp_path, [("a", "alpha body")])
    con = open_search(idx)
    try:
        assert semantic_neighbors(con, "does-not-exist", k=5) == []
    finally:
        con.close()


def test_recall_dedupes_by_note(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import build_fts, index_vault, open_index
    from cairn.search import open_search, search

    emb = FakeEmbedder(dim=8)
    v = tmp_path / "vault"
    v.mkdir()
    # Two headed sections => two chunks of the SAME note, both mentioning "alpha".
    (v / "multi.md").write_text(
        "---\ntitle: Multi\npermalink: multi\n---\n"
        "## One\nalpha alpha beans.\n\n## Two\nalpha alpha brewing.\n"
    )
    (v / "other.md").write_text("---\ntitle: Other\npermalink: other\n---\nbeta gamma.\n")
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    hits = search(con, "alpha", embedder=emb, k=10)
    permalinks = [h.permalink for h in hits]
    assert permalinks.count("multi") == 1, f"note returned more than once: {permalinks}"
