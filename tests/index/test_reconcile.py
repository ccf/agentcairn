# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.embed import FakeEmbedder
from cairn.index import open_index, reconcile


def _seed(tmp_path: Path) -> Path:
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha\n")
    (v / "b.md").write_text("---\ntitle: B\npermalink: b\n---\nbeta\n")
    return v


def test_reconcile_only_touches_changed_notes(tmp_path):
    v = _seed(tmp_path)
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    r1 = reconcile(con, str(v), emb)
    assert r1.added == 2 and r1.updated == 0 and r1.deleted == 0

    # no changes -> nothing re-indexed
    r2 = reconcile(con, str(v), emb)
    assert (r2.added, r2.updated, r2.deleted) == (0, 0, 0)

    # edit one note, delete another
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha CHANGED\n")
    (v / "b.md").unlink()
    r3 = reconcile(con, str(v), emb)
    assert r3.updated == 1 and r3.deleted == 1 and r3.added == 0
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


def test_reconcile_rebuilds_on_model_mismatch(tmp_path):
    v = _seed(tmp_path)
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    reconcile(con, str(v), FakeEmbedder(dim=8))
    # a different model id at same dim must force a full rebuild (semantic mismatch)
    r = reconcile(con, str(v), FakeEmbedder(dim=8), model_id_override="other-8")
    assert r.rebuilt is True
    assert r.added == 2


def test_reconcile_rebuilds_on_dimension_change(tmp_path):
    v = _seed(tmp_path)
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    reconcile(con, str(v), FakeEmbedder(dim=8))
    r = reconcile(con, str(v), FakeEmbedder(dim=16))  # different dim
    assert r.rebuilt is True and r.added == 2
    assert (
        con.execute("SELECT count(*) FROM chunk_embeddings WHERE len(vec) != 16").fetchone()[0] == 0
    )


def test_reconcile_indexes_same_stem_in_subdirs(tmp_path):
    v = tmp_path / "vault"
    (v / "sub").mkdir(parents=True)
    (v / "note.md").write_text("---\ntitle: Top\npermalink: top\n---\ntop body\n")
    (v / "sub" / "note.md").write_text("---\ntitle: Sub\npermalink: sub\n---\nsub body\n")
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    r = reconcile(con, str(v), emb)
    assert r.added == 2
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 2
