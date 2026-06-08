# SPDX-License-Identifier: Apache-2.0
"""DuckDB index schema. The .duckdb file is a DISPOSABLE, rebuildable cache —
never the source of truth (that is the markdown vault). `meta` records the
embedding model + dim so a model/dim mismatch can trigger a rebuild."""

from __future__ import annotations

import duckdb


def open_index(path: str, *, dim: int, model_id: str) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    con.execute("INSTALL vss; LOAD vss;")
    con.execute("INSTALL fts; LOAD fts;")
    con.execute(
        "CREATE TABLE IF NOT EXISTS notes ("
        "  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR, type VARCHAR,"
        "  content_hash VARCHAR, mtime DOUBLE)"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS chunks ("
        "  chunk_id VARCHAR PRIMARY KEY, note_permalink VARCHAR,"
        "  heading_path VARCHAR, ordinal INTEGER, text VARCHAR)"
    )
    # NOTE: IF NOT EXISTS keeps an existing vec column's width. A change in
    # embedding dimension is handled by reconcile() (which recreates this
    # table), not here — re-calling open_index with a new dim does NOT widen it.
    con.execute(
        f"CREATE TABLE IF NOT EXISTS chunk_embeddings ("
        f"  chunk_id VARCHAR PRIMARY KEY, vec FLOAT[{dim}])"
    )
    con.execute(
        "CREATE TABLE IF NOT EXISTS links ("
        # dst_target is the raw, unresolved link target (e.g. display text from
        # a wikilink or relation). Plan 3 will resolve it to a permalink for joins.
        "  src_permalink VARCHAR, dst_target VARCHAR, edge_type VARCHAR)"
    )
    con.execute("CREATE TABLE IF NOT EXISTS meta (key VARCHAR PRIMARY KEY, value VARCHAR)")
    set_meta(con, "embedding_model", model_id)
    set_meta(con, "embedding_dim", str(dim))
    return con


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def get_meta(con: duckdb.DuckDBPyConnection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
    return row[0] if row else None
