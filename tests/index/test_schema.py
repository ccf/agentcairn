# SPDX-License-Identifier: Apache-2.0
import duckdb
import pytest

from cairn.index.schema import get_meta, open_index, set_meta


def _column_names(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    rows = con.execute(f"PRAGMA table_info('{table}')").fetchall()
    return [r[1] for r in rows]


def test_open_index_migrates_old_6col_notes_table(tmp_path):
    """open_index must additively migrate an existing pre-bitemporal notes table.

    An old .duckdb has a 6-column notes table (no validity columns). After
    calling open_index, valid_from, valid_until, and superseded_by must exist.
    """
    db_path = str(tmp_path / "old.duckdb")
    # Create the old 6-column schema as it existed before this branch.
    old_con = duckdb.connect(db_path)
    old_con.execute(
        "CREATE TABLE notes ("
        "  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR,"
        "  type VARCHAR, content_hash VARCHAR, mtime DOUBLE)"
    )
    old_con.execute("INSERT INTO notes VALUES ('p1', '/a.md', 'A', 'note', 'abc', 1.0)")
    old_con.close()

    # open_index on the existing DB must migrate the table, not silently no-op.
    con = open_index(db_path, dim=8, model_id="fake-8")
    cols = _column_names(con, "notes")
    assert "valid_from" in cols, f"valid_from missing after migration; got {cols}"
    assert "valid_until" in cols, f"valid_until missing after migration; got {cols}"
    assert "superseded_by" in cols, f"superseded_by missing after migration; got {cols}"
    # Existing row must survive with NULLs for the new columns.
    row = con.execute(
        "SELECT valid_from, valid_until, superseded_by FROM notes WHERE permalink='p1'"
    ).fetchone()
    assert row is not None
    assert row[0] is None and row[1] is None and row[2] is None
    con.close()


def test_open_index_migrate_then_reconcile_works(tmp_path):
    """After migrating an old 6-col index, reconcile must succeed without
    'column not found' errors when indexing a note with validity frontmatter."""
    from cairn.embed import FakeEmbedder
    from cairn.index import reconcile

    db_path = str(tmp_path / "old.duckdb")
    # Simulate the pre-bitemporal index.
    old_con = duckdb.connect(db_path)
    old_con.execute(
        "CREATE TABLE notes ("
        "  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR,"
        "  type VARCHAR, content_hash VARCHAR, mtime DOUBLE)"
    )
    old_con.execute(
        "CREATE TABLE chunks (chunk_id VARCHAR PRIMARY KEY, note_permalink VARCHAR,"
        " heading_path VARCHAR, ordinal INTEGER, text VARCHAR)"
    )
    old_con.execute("CREATE TABLE chunk_embeddings (chunk_id VARCHAR PRIMARY KEY, vec FLOAT[8])")
    old_con.execute(
        "CREATE TABLE links (src_permalink VARCHAR, dst_target VARCHAR, edge_type VARCHAR)"
    )
    old_con.execute("CREATE TABLE meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    old_con.execute("INSERT INTO meta VALUES ('embedding_model', 'fake-8')")
    old_con.execute("INSERT INTO meta VALUES ('embedding_dim', '8')")
    old_con.close()

    vault = tmp_path / "v"
    vault.mkdir()
    (vault / "job.md").write_text(
        "---\ntitle: Job\npermalink: job\nvalid_from: 2024-01-01\n"
        "valid_until: 2025-01-01\nsuperseded_by: job2\n---\nworked at X\n"
    )
    emb = FakeEmbedder(dim=8)
    con = open_index(db_path, dim=emb.dim, model_id=emb.model_id)
    # Must not raise "Binder Error: Column not found" or similar.
    result = reconcile(con, str(vault), emb)
    assert result.added == 1
    row = con.execute(
        "SELECT valid_from, valid_until, superseded_by FROM notes WHERE permalink='job'"
    ).fetchone()
    assert row is not None
    assert row[0] is not None and row[1] is not None and row[2] == "job2"
    con.close()


def test_open_index_creates_tables_and_meta(tmp_path):
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    assert {"notes", "chunks", "chunk_embeddings", "links", "meta"} <= tables
    assert get_meta(con, "embedding_model") == "fake-8"
    assert get_meta(con, "embedding_dim") == "8"
    set_meta(con, "k", "v")
    assert get_meta(con, "k") == "v"


def test_embedding_vec_column_is_fixed_width(tmp_path):
    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="fake-8")
    # inserting a wrong-width vector must fail (fixed FLOAT[8])
    con.execute("INSERT INTO chunk_embeddings VALUES ('c1', [0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8])")
    with pytest.raises(duckdb.ConversionException):
        con.execute("INSERT INTO chunk_embeddings VALUES ('c2', [0.1,0.2,0.3])")
