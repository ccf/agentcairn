# SPDX-License-Identifier: Apache-2.0
"""Hybrid retrieval over a Plan-2 DuckDB index. Read-only; brute-force cosine
(core `array_cosine_similarity`, no vss) + BM25, fused with RRF."""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from cairn.search.rerank import rerank_candidates

_RRF_MACRO = "CREATE OR REPLACE MACRO rrf(rank, k := 60) AS coalesce(1.0 / (k + rank), 0)"


def open_search(index_path: str) -> duckdb.DuckDBPyConnection:
    """Open the persistent index READ-ONLY for querying.

    Multiple concurrent read-only openers coexist without conflict, but each
    read-only ATTACH still acquires a DuckDB file lock for the connection's
    lifetime.  That lock will block a separate ``reindex`` writer process (and
    vice-versa) until the connection is closed.  A long-lived consumer (e.g. an
    MCP server) should therefore open-per-query or reopen after a rebuild rather
    than hold this connection open across a ``reindex`` call.

    DuckDB read-only connections reject DDL, so we open an in-memory connection,
    attach the on-disk index read-only, and install the rrf() macro there.
    All query tables (notes/chunks/chunk_embeddings/links/meta) are accessed via
    the attached database. After USE idx + search_path, unqualified names resolve
    against idx and the rrf() macro resolves against memory.
    """
    con = duckdb.connect()  # in-memory; permits DDL for the rrf macro
    con.execute("LOAD fts;")  # match_bm25 lives in the fts extension; array_* are core
    con.execute(_RRF_MACRO)  # create macro in the in-memory db BEFORE attaching read-only db
    # DuckDB cannot bind the ATTACH target as a parameter, so we SQL-literal-escape it.
    escaped = str(index_path).replace("'", "''")
    con.execute(f"ATTACH '{escaped}' AS idx (READ_ONLY)")
    con.execute("USE idx;")  # set default schema so unqualified table names resolve
    # After USE idx, DuckDB resolves unqualified names against idx but not memory.
    # Add memory to the search_path so rrf() and other in-memory macros remain accessible.
    con.execute("SET search_path = 'idx.main,memory.main'")
    return con


def _dim(con: duckdb.DuckDBPyConnection) -> int:
    row = con.execute("SELECT value FROM meta WHERE key = 'embedding_dim'").fetchone()
    return int(row[0]) if row else 0


@dataclass
class Hit:
    """A single retrieval result.

    ``score`` reflects relevance under the *active ranker* — higher is better
    and the returned list is always sorted in descending score order:
    - hybrid path (no rerank): RRF-fused score, graph-boosted.
    - rerank path: cross-encoder score assigned by the reranker.
    """

    chunk_id: str
    permalink: str
    heading_path: str
    snippet: str
    score: float


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


def _hybrid_sql(dim: int) -> str:
    # BM25 arm + brute-force cosine arm, each ranked, fused by rrf(), then a
    # graph-boost (x1.2 when the note is the target of any link). pool >> limit.
    return f"""
        WITH fts AS (
            SELECT chunk_id, rank() OVER (ORDER BY score DESC) AS r
            FROM (
                SELECT chunk_id, fts_main_chunks.match_bm25(chunk_id, ?, fields := 'text') AS score
                FROM chunks
            ) WHERE score IS NOT NULL
            ORDER BY score DESC LIMIT ?
        ),
        vec AS (
            SELECT chunk_id, rank() OVER (ORDER BY sim DESC) AS r
            FROM (
                SELECT chunk_id, array_cosine_similarity(vec, ?::FLOAT[{dim}]) AS sim
                FROM chunk_embeddings ORDER BY sim DESC LIMIT ?
            )
        ),
        fused AS (
            SELECT coalesce(fts.chunk_id, vec.chunk_id) AS chunk_id,
                   rrf(fts.r) + rrf(vec.r) AS rrf_score
            FROM fts FULL OUTER JOIN vec ON fts.chunk_id = vec.chunk_id
        )
        SELECT f.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               f.rrf_score
               * (CASE WHEN EXISTS (SELECT 1 FROM links l WHERE l.dst_target = c.note_permalink)
                       THEN 1.2 ELSE 1.0 END) AS score
        FROM fused f JOIN chunks c ON c.chunk_id = f.chunk_id
        ORDER BY score DESC LIMIT ?
    """


def _bm25_only_sql() -> str:
    return """
        WITH fts AS (
            SELECT chunk_id, rank() OVER (ORDER BY score DESC) AS r
            FROM (
                SELECT chunk_id, fts_main_chunks.match_bm25(chunk_id, ?, fields := 'text') AS score
                FROM chunks
            ) WHERE score IS NOT NULL
            ORDER BY score DESC LIMIT ?
        )
        SELECT c.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               rrf(f.r)
               * (CASE WHEN EXISTS (SELECT 1 FROM links l WHERE l.dst_target = c.note_permalink)
                       THEN 1.2 ELSE 1.0 END) AS score
        FROM fts f JOIN chunks c ON c.chunk_id = f.chunk_id
        ORDER BY score DESC LIMIT ?
    """


def bm25_only(
    con: duckdb.DuckDBPyConnection, query: str, *, limit: int = 10, pool: int = 200
) -> list[dict]:
    rows = con.execute(_bm25_only_sql(), [query, pool, limit]).fetchall()
    return [
        {
            "chunk_id": r[0],
            "note_permalink": r[1],
            "heading_path": r[2],
            "snippet": r[3],
            "score": float(r[4]),
        }
        for r in rows
    ]


def hybrid_search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    qvec: list[float],
    *,
    dim: int,
    limit: int = 10,
    pool: int = 200,
) -> list[dict]:
    """Hybrid BM25 + cosine via RRF, graph-boosted. Returns compact dict rows."""
    rows = con.execute(_hybrid_sql(dim), [query, pool, qvec, pool, limit]).fetchall()
    return [
        {
            "chunk_id": r[0],
            "note_permalink": r[1],
            "heading_path": r[2],
            "snippet": r[3],
            "score": float(r[4]),
        }
        for r in rows
    ]


def search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    *,
    embedder=None,
    k: int = 10,
    pool: int = 200,
    rerank: bool = False,
) -> list[Hit]:
    """Top-level retrieval (degradation ladder): hybrid when an embedder is given
    (auto-degrades to BM25 if no embeddings exist), else BM25-only."""
    if embedder is not None:
        qvec = embedder.embed_query(query)
        rows = hybrid_search(
            con, query, qvec, dim=embedder.dim, limit=(20 if rerank else k), pool=pool
        )
    else:
        rows = bm25_only(con, query, limit=(20 if rerank else k), pool=pool)
    if rerank and rows:
        # Hydrate full text for a precise rerank, then map back to compact Hits.
        # Hit.score is set to the cross-encoder score so the list remains sorted
        # descending by the active ranker's score (not the original RRF score).
        text_by_id = {
            c["chunk_id"]: c["text"] for c in get_chunks(con, [r["chunk_id"] for r in rows])
        }
        cands = [{**r, "text": text_by_id.get(r["chunk_id"], r["snippet"])} for r in rows]
        ranked = rerank_candidates(query, cands, top_k=k)
        rows = [
            {
                "chunk_id": c["chunk_id"],
                "note_permalink": c["note_permalink"],
                "heading_path": c["heading_path"],
                "snippet": c["snippet"],
                "score": c["rerank_score"],  # use cross-encoder score, not RRF
            }
            for c in ranked
        ]
    else:
        rows = rows[:k]
    return [
        Hit(
            chunk_id=r["chunk_id"],
            permalink=r["note_permalink"],
            heading_path=r["heading_path"],
            snippet=r["snippet"],
            score=r["score"],
        )
        for r in rows
    ]


def get_chunks(con: duckdb.DuckDBPyConnection, chunk_ids: list[str]) -> list[dict]:
    """Hydrate full chunk text by id. The bound list MUST be cast (`?::VARCHAR[]`)
    or DuckDB raises 'Cannot deduce template type T'."""
    if not chunk_ids:
        return []
    rows = con.execute(
        "SELECT chunk_id, note_permalink, heading_path, ordinal, text "
        "FROM chunks WHERE chunk_id = ANY(?::VARCHAR[])",
        [chunk_ids],
    ).fetchall()
    return [
        {
            "chunk_id": r[0],
            "note_permalink": r[1],
            "heading_path": r[2],
            "ordinal": r[3],
            "text": r[4],
        }
        for r in rows
    ]


def get_note(con: duckdb.DuckDBPyConnection, permalink: str) -> dict | None:
    """Hydrate full note text and metadata by permalink."""
    row = con.execute(
        "SELECT permalink, path, title, type FROM notes WHERE permalink = ?", [permalink]
    ).fetchone()
    if not row:
        return None
    chunks = con.execute(
        "SELECT text FROM chunks WHERE note_permalink = ? ORDER BY ordinal", [permalink]
    ).fetchall()
    return {
        "permalink": row[0],
        "path": row[1],
        "title": row[2],
        "type": row[3],
        "text": "\n\n".join(c[0] for c in chunks),
    }
