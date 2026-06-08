# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.embed import FakeEmbedder
from cairn.index import index_vault, open_index


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
        "SELECT src_permalink, dst_permalink, edge_type FROM links ORDER BY edge_type"
    ).fetchall()
    assert ("coffee", "Tea", "links_to") in edges
    assert ("coffee", "Tea", "pairs_with") in edges
