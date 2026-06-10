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


def test_no_permalink_same_stem_subdirs_do_not_collide(tmp_path):
    v = tmp_path / "vault"
    (v / "a").mkdir(parents=True)
    (v / "b").mkdir(parents=True)
    (v / "a" / "note.md").write_text("# A\nalpha\n")  # NO frontmatter permalink
    (v / "b" / "note.md").write_text("# B\nbeta\n")
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    r = reconcile(con, str(v), emb)
    assert r.added == 2
    perms = {row[0] for row in con.execute("SELECT permalink FROM notes").fetchall()}
    assert perms == {"a/note", "b/note"}


def test_reconcile_updates_path_on_move(tmp_path):
    v = tmp_path / "vault"
    (v / "sub").mkdir(parents=True)
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha\n")
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(v), emb)
    old = con.execute("SELECT path FROM notes WHERE permalink='a'").fetchone()[0]
    (v / "a.md").rename(v / "sub" / "a.md")  # move, same content + permalink
    reconcile(con, str(v), emb)
    new = con.execute("SELECT path FROM notes WHERE permalink='a'").fetchone()[0]
    assert new != old and new.endswith("a.md") and "sub" in new
    assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 1


def test_reconcile_populates_validity_columns(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile

    v = tmp_path / "vault"
    v.mkdir()
    (v / "job.md").write_text(
        "---\ntitle: Job\npermalink: job\nvalid_from: 2024-01-01\n"
        "valid_until: 2024-06-01\nsuperseded_by: job2\n---\nworked at X\n"
    )
    # malformed valid_from -> NULL, note still indexes (non-lossy)
    (v / "ok.md").write_text(
        "---\ntitle: OK\npermalink: ok\nvalid_from: bad-date\n---\nplain note\n"
    )
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(v), emb)
    row = con.execute(
        "SELECT valid_from, valid_until, superseded_by FROM notes WHERE permalink='job'"
    ).fetchone()
    assert row[0] is not None and row[1] is not None and row[2] == "job2"
    # malformed valid_from -> NULL, but the note is still indexed (non-lossy)
    ok = con.execute("SELECT valid_from, superseded_by FROM notes WHERE permalink='ok'").fetchone()
    assert ok is not None and ok[0] is None and ok[1] is None


def test_open_index_does_not_clobber_meta_so_reconcile_rebuilds(tmp_path):
    # Simulates two CLI `reindex` invocations (open_index -> reconcile each time)
    # with a switched embedder. open_index must NOT overwrite the stored
    # model/dim, or reconcile would skip the rebuild and leave wrong-width vectors.
    v = _seed(tmp_path)
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=8, model_id="fake-8")
    reconcile(con, str(v), FakeEmbedder(dim=8))
    con.close()
    con2 = open_index(idx, dim=16, model_id="fake-16")
    r = reconcile(con2, str(v), FakeEmbedder(dim=16))
    assert r.rebuilt is True
    assert (
        con2.execute("SELECT count(*) FROM chunk_embeddings WHERE len(vec) != 16").fetchone()[0]
        == 0
    )
