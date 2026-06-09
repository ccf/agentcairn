# SPDX-License-Identifier: Apache-2.0
"""Hybrid retrieval over a Plan-2 DuckDB index. Read-only; brute-force cosine
(core `array_cosine_similarity`, no vss) + BM25, fused with RRF."""

from __future__ import annotations

import duckdb

_RRF_MACRO = "CREATE OR REPLACE MACRO rrf(rank, k := 60) AS coalesce(1.0 / (k + rank), 0)"


def open_search(index_path: str) -> duckdb.DuckDBPyConnection:
    """Open the persistent index READ-ONLY for querying (multi-reader safe).

    DuckDB read-only connections reject DDL, so we open an in-memory connection,
    attach the on-disk index read-only, and install the rrf() macro there.
    All query tables (notes/chunks/chunk_embeddings/links/meta) are accessed via
    the attached database automatically — DuckDB resolves unqualified names across
    attachments when there is only one attached database.
    """
    con = duckdb.connect()  # in-memory; permits DDL for the rrf macro
    con.execute("LOAD fts;")  # match_bm25 lives in the fts extension; array_* are core
    con.execute(_RRF_MACRO)  # create macro in the in-memory db BEFORE attaching read-only db
    con.execute(f"ATTACH '{index_path}' AS idx (READ_ONLY)")
    con.execute("USE idx;")  # set default schema so unqualified table names resolve
    return con


def _dim(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT value FROM meta WHERE key = 'embedding_dim'").fetchone()
    return int(row[0]) if row else 0


def vector_search(
    con: duckdb.DuckDBPyConnection, qvec: list[float], *, dim: int, pool: int = 200
) -> list[tuple[str, float]]:
    """Brute-force cosine top-`pool`. Returns [(chunk_id, similarity)] highest-first.
    `array_cosine_similarity` is a core function (no vss); the `?::FLOAT[dim]` cast
    is required (a bare list is a generic LIST, not a fixed-size ARRAY)."""
    sql = (
        f"SELECT chunk_id, array_cosine_similarity(vec, ?::FLOAT[{dim}]) AS sim "
        f"FROM chunk_embeddings ORDER BY sim DESC LIMIT ?"
    )
    return [(r[0], float(r[1])) for r in con.execute(sql, [qvec, pool]).fetchall()]
