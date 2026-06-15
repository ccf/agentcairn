# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from cairn.embed import FakeEmbedder
from cairn.index import bm25_search, build_fts, index_note, index_vault, open_index


def _vault(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\n"
        "About [[Tea]].\n\n## Brewing\nPour over. \n\n- pairs_with [[Tea]]\n"
    )
    (v / "tea.md").write_text("---\ntitle: Tea\npermalink: tea\n---\nGreen tea.\n")
    return v


def test_index_vault_populates_rows_and_embeddings(tmp_path):
    v = _vault(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    stats = index_vault(con, str(v), emb)
    assert stats.notes == 2
    assert stats.chunks >= 2
    # every chunk has an embedding of the right width
    n_emb = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
    n_chunks = con.execute("SELECT count(*) FROM chunks").fetchone()[0]
    assert n_emb == n_chunks
    # link graph captured (coffee -> tea, both wikilink and pairs_with)
    edges = con.execute(
        "SELECT src_permalink, dst_target, edge_type FROM links ORDER BY edge_type"
    ).fetchall()
    assert ("coffee", "Tea", "links_to") in edges
    assert ("coffee", "Tea", "pairs_with") in edges


def test_fts_bm25_finds_chunk(tmp_path):
    v = _vault(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    build_fts(con)
    hits = bm25_search(con, "pour over brewing", limit=5)
    assert hits, "expected at least one BM25 hit"
    assert any("Brewing" in h[1] for h in hits)  # (chunk_id, heading_path, score)


def test_bm25_search_returns_empty_before_fts_built(tmp_path):
    v = _vault(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    assert bm25_search(con, "anything", 5) == []  # FTS not built yet


def test_index_note_populates_project_and_harness(tmp_path):
    note_path = tmp_path / "n.md"
    note_path.write_text(
        "---\ntitle: N\ntype: note\npermalink: n\n"
        "project: agentcairn\nharness: codex\n---\n"
        "- [context] something happened #ingested\n"
    )
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    index_note(con, note_path, emb, vault_dir=str(tmp_path))
    row = con.execute("SELECT project, harness FROM notes WHERE permalink='n'").fetchone()
    assert row == ("agentcairn", "codex")


def test_index_note_raises_on_embedder_count_mismatch(tmp_path):
    class Broken:
        model_id = "broken"

        @property
        def dim(self):
            return 8

        def embed(self, texts):
            return [[0.0] * 8]  # always 1 vector regardless of input

        def embed_query(self, t):
            return [0.0] * 8

    v = tmp_path / "v"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nintro\n\n## S1\nx\n\n## S2\ny\n")
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="broken")
    with pytest.raises(ValueError):
        index_note(con, v / "a.md", Broken(), vault_dir=str(v))
