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
