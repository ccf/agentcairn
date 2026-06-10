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
        "  content_hash VARCHAR, mtime DOUBLE,"
        "  valid_from TIMESTAMP, valid_until TIMESTAMP, superseded_by VARCHAR)"
    )
    # Additive migration: add validity columns to pre-bitemporal databases that
    # already have a 6-column notes table.  DuckDB supports IF NOT EXISTS here,
    # so these are no-ops on a freshly-created table.
    con.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS valid_from TIMESTAMP")
    con.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS valid_until TIMESTAMP")
    con.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS superseded_by VARCHAR")
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
    # Record the embedding model/dim ONLY on first creation (insert-if-absent).
    # Overwriting here would hide a model/dim change from reconcile(), which
    # relies on the STORED values to decide whether to rebuild — overwriting
    # makes reconcile think nothing changed and skip the rebuild, leaving
    # vectors of the wrong width/model. On an existing index the old values are
    # kept so reconcile() detects the mismatch and rebuilds.
    con.execute(
        "INSERT INTO meta VALUES ('embedding_model', ?) ON CONFLICT (key) DO NOTHING",
        [model_id],
    )
    con.execute(
        "INSERT INTO meta VALUES ('embedding_dim', ?) ON CONFLICT (key) DO NOTHING",
        [str(dim)],
    )
    return con


def set_meta(con: duckdb.DuckDBPyConnection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        [key, value],
    )


def get_meta(con: duckdb.DuckDBPyConnection, key: str) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key = ?", [key]).fetchone()
    return row[0] if row else None


def cached_haystack_tokens(con: duckdb.DuckDBPyConnection) -> int:
    """Whole-haystack token estimate. Reads the value cached at reindex time
    (meta key 'haystack_tokens'); falls back to a one-off scan if absent (an
    index built before this feature). Same per-chunk model as estimate_tokens."""
    cached = get_meta(con, "haystack_tokens")
    if cached is not None:
        try:
            return int(cached)
        except ValueError:
            pass
    row = con.execute(
        "SELECT COALESCE(SUM(CAST((LENGTH(text)+3)/4 AS BIGINT)),0) FROM chunks"
    ).fetchone()
    return int(row[0])
