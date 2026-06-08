# SPDX-License-Identifier: Apache-2.0
import duckdb
import pytest

from cairn.index.schema import get_meta, open_index, set_meta


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
