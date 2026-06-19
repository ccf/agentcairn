# SPDX-License-Identifier: Apache-2.0
"""Hybrid retrieval over a Plan-2 DuckDB index. Read-only; brute-force cosine
(core `array_cosine_similarity`, no vss) + BM25, fused with RRF."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import duckdb

from cairn.ingest.events import project_from_cwd
from cairn.search.rerank import rerank_candidates
from cairn.temporal import db_now, from_db, validity_factor

_RRF_MACRO = "CREATE OR REPLACE MACRO rrf(rank, k := 60) AS coalesce(1.0 / (k + rank), 0)"
_VALIDITY_PENALTY = 0.5
_PROJECT_BOOST = 1.4


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


def resolve_current_project(explicit: str | None) -> str | None:
    """Current project for provenance-aware recall: an explicit arg wins, else the
    process cwd's repo name (final path segment), else None (no boost)."""
    if explicit:
        return explicit
    try:
        return project_from_cwd(os.getcwd())
    except OSError:
        return None


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
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by: str | None = None
    project: str | None = None


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


def _hybrid_sql(
    dim: int,
    graph_boost: bool = True,
    validity_aware: bool = True,
    project_boost: bool = False,
    scope_project: bool = False,
) -> str:
    # BM25 arm + brute-force cosine arm, each ranked, fused by rrf(), then an
    # optional graph-boost (x1.2 when the note is the target of any link). pool >> limit.
    # An optional validity multiplier (x0.5) demotes superseded/expired/not-yet-valid notes.
    # An optional provenance boost (x1.4) lifts notes whose project matches the current one
    # (non-lossy); an optional scope filter hard-restricts to the current project.
    boost = (
        " * (CASE WHEN EXISTS (SELECT 1 FROM links l WHERE l.dst_target = c.note_permalink) "
        "THEN 1.2 ELSE 1.0 END)"
        if graph_boost
        else ""
    )
    validity = (
        f" * (CASE WHEN n.superseded_by IS NOT NULL THEN {_VALIDITY_PENALTY}"
        f" WHEN n.valid_until IS NOT NULL AND n.valid_until <= ? THEN {_VALIDITY_PENALTY}"
        f" WHEN n.valid_from IS NOT NULL AND n.valid_from > ? THEN {_VALIDITY_PENALTY}"
        f" ELSE 1.0 END)"
        if validity_aware
        else ""
    )
    proj_boost = (
        f" * (CASE WHEN n.project = ? THEN {_PROJECT_BOOST} ELSE 1.0 END)" if project_boost else ""
    )
    scope = " WHERE n.project = ?" if scope_project else ""
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
               n.valid_from, n.valid_until, n.superseded_by, n.project,
               f.rrf_score{boost}{validity}{proj_boost} AS score
        FROM fused f JOIN chunks c ON c.chunk_id = f.chunk_id
        JOIN notes n ON n.permalink = c.note_permalink{scope}
        ORDER BY score DESC LIMIT ?
    """


def _bm25_only_sql(
    graph_boost: bool = True,
    validity_aware: bool = True,
    project_boost: bool = False,
    scope_project: bool = False,
) -> str:
    boost = (
        " * (CASE WHEN EXISTS (SELECT 1 FROM links l WHERE l.dst_target = c.note_permalink) "
        "THEN 1.2 ELSE 1.0 END)"
        if graph_boost
        else ""
    )
    validity = (
        f" * (CASE WHEN n.superseded_by IS NOT NULL THEN {_VALIDITY_PENALTY}"
        f" WHEN n.valid_until IS NOT NULL AND n.valid_until <= ? THEN {_VALIDITY_PENALTY}"
        f" WHEN n.valid_from IS NOT NULL AND n.valid_from > ? THEN {_VALIDITY_PENALTY}"
        f" ELSE 1.0 END)"
        if validity_aware
        else ""
    )
    proj_boost = (
        f" * (CASE WHEN n.project = ? THEN {_PROJECT_BOOST} ELSE 1.0 END)" if project_boost else ""
    )
    scope = " WHERE n.project = ?" if scope_project else ""
    return f"""
        WITH fts AS (
            SELECT chunk_id, rank() OVER (ORDER BY score DESC) AS r
            FROM (
                SELECT chunk_id, fts_main_chunks.match_bm25(chunk_id, ?, fields := 'text') AS score
                FROM chunks
            ) WHERE score IS NOT NULL
            ORDER BY score DESC LIMIT ?
        )
        SELECT c.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               n.valid_from, n.valid_until, n.superseded_by, n.project,
               rrf(f.r){boost}{validity}{proj_boost} AS score
        FROM fts f JOIN chunks c ON c.chunk_id = f.chunk_id
        JOIN notes n ON n.permalink = c.note_permalink{scope}
        ORDER BY score DESC LIMIT ?
    """


def bm25_only(
    con: duckdb.DuckDBPyConnection,
    query: str,
    *,
    limit: int = 10,
    pool: int = 200,
    graph_boost: bool = True,
    validity_aware: bool = True,
    now: datetime | None = None,
    current: str | None = None,
    scope: str = "all",
) -> list[dict]:
    now = now if now is not None else db_now()
    project_boost = current is not None
    scope_project = scope == "project" and current is not None
    sql = _bm25_only_sql(graph_boost, validity_aware, project_boost, scope_project)
    # Bind-param ordering (positional by appearance in SQL string):
    #   [query, pool]    — BM25 FTS arm
    #   + [now, now]     — validity CASE comparisons (only when validity_aware)
    #   + [current]      — project-boost CASE (only when project_boost)
    #   + [current]      — scope WHERE filter (only when scope_project)
    #   + [limit]        — LIMIT
    params: list = [query, pool]
    if validity_aware:
        params += [now, now]
    if project_boost:
        params.append(current)
    if scope_project:
        params.append(current)
    params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [
        {
            "chunk_id": r[0],
            "note_permalink": r[1],
            "heading_path": r[2],
            "snippet": r[3],
            "valid_from": from_db(r[4]).isoformat() if r[4] is not None else None,
            "valid_until": from_db(r[5]).isoformat() if r[5] is not None else None,
            "superseded_by": r[6],
            "project": r[7],
            "score": float(r[8]),
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
    graph_boost: bool = True,
    validity_aware: bool = True,
    now: datetime | None = None,
    current: str | None = None,
    scope: str = "all",
) -> list[dict]:
    """Hybrid BM25 + cosine via RRF, optionally graph-boosted. Returns compact dict rows."""
    now = now if now is not None else db_now()
    project_boost = current is not None
    scope_project = scope == "project" and current is not None
    sql = _hybrid_sql(dim, graph_boost, validity_aware, project_boost, scope_project)
    # Bind-param ordering (positional by appearance in SQL string):
    #   [query, pool, qvec, pool]  — BM25 FTS arm + cosine arm
    #   + [now, now]               — validity CASE comparisons (only when validity_aware)
    #   + [current]                — project-boost CASE (only when project_boost)
    #   + [current]                — scope WHERE filter (only when scope_project)
    #   + [limit]                  — LIMIT
    params: list = [query, pool, qvec, pool]
    if validity_aware:
        params += [now, now]
    if project_boost:
        params.append(current)
    if scope_project:
        params.append(current)
    params.append(limit)
    rows = con.execute(sql, params).fetchall()
    return [
        {
            "chunk_id": r[0],
            "note_permalink": r[1],
            "heading_path": r[2],
            "snippet": r[3],
            "valid_from": from_db(r[4]).isoformat() if r[4] is not None else None,
            "valid_until": from_db(r[5]).isoformat() if r[5] is not None else None,
            "superseded_by": r[6],
            "project": r[7],
            "score": float(r[8]),
        }
        for r in rows
    ]


def _dedupe_by_note(rows: list[dict]) -> list[dict]:
    """Collapse multiple chunks of the same note into one result, keeping the
    best-scoring chunk. ``rows`` must already be sorted best-first."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        pk = r["note_permalink"]
        if pk in seen:
            continue
        seen.add(pk)
        out.append(r)
    return out


def search(
    con: duckdb.DuckDBPyConnection,
    query: str,
    *,
    embedder=None,
    k: int = 10,
    pool: int = 200,
    rerank: bool = False,
    graph_boost: bool = True,
    validity_aware: bool = True,
    project: str | None = None,
    scope: str = "all",
) -> list[Hit]:
    """Top-level retrieval (degradation ladder): hybrid when an embedder is given
    (auto-degrades to BM25 if no embeddings exist), else BM25-only.

    A single ``now`` instant is captured once and threaded into both the SQL
    validity comparisons and the post-rerank validity factor, so the two
    ranking stages are always coherent."""
    # Capture one instant for both SQL and rerank validity comparisons.
    now_aware = datetime.now(UTC)
    now_naive = now_aware.replace(tzinfo=None)  # naive-UTC for DuckDB TIMESTAMP bind

    current = resolve_current_project(project)
    if scope == "project" and current is None:
        logging.getLogger(__name__).warning(
            "recall scope='project' requested but no current project resolved; "
            "falling back to scope='all'"
        )

    if embedder is not None:
        qvec = embedder.embed_query(query)
        rows = hybrid_search(
            con,
            query,
            qvec,
            dim=embedder.dim,
            limit=max(20, k),
            pool=pool,
            graph_boost=graph_boost,
            validity_aware=validity_aware,
            now=now_naive,
            current=current,
            scope=scope,
        )
    else:
        rows = bm25_only(
            con,
            query,
            limit=max(20, k),
            pool=pool,
            graph_boost=graph_boost,
            validity_aware=validity_aware,
            now=now_naive,
            current=current,
            scope=scope,
        )
    if rerank and rows:
        # Hydrate full text for a precise rerank, then map back to compact Hits.
        # Hit.score is set to the cross-encoder score so the list remains sorted
        # descending by the active ranker's score (not the original RRF score).
        # Validity fields are preserved from the original row dicts.
        text_by_id = {
            c["chunk_id"]: c["text"] for c in get_chunks(con, [r["chunk_id"] for r in rows])
        }
        cands = [{**r, "text": text_by_id.get(r["chunk_id"], r["snippet"])} for r in rows]
        ranked = rerank_candidates(query, cands, top_k=max(20, k))
        if current is not None:
            ranked = sorted(
                (
                    {
                        **c,
                        "rerank_score": c["rerank_score"]
                        * (_PROJECT_BOOST if c.get("project") == current else 1.0),
                    }
                    for c in ranked
                ),
                key=lambda c: c["rerank_score"],
                reverse=True,
            )
        if validity_aware:
            # Apply validity penalty to cross-encoder scores so superseded/expired/
            # not-yet-valid notes are still demoted in the reranked order.
            # Parse ISO strings from the candidate dicts; missing → None.
            def _parse_iso(s: str | None) -> datetime | None:
                if s is None:
                    return None
                dt = datetime.fromisoformat(s)
                return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)

            ranked = sorted(
                [
                    {
                        **c,
                        "rerank_score": c["rerank_score"]
                        * validity_factor(
                            _parse_iso(c.get("valid_from")),
                            _parse_iso(c.get("valid_until")),
                            c.get("superseded_by"),
                            now_aware,
                        ),
                    }
                    for c in ranked
                ],
                key=lambda c: c["rerank_score"],
                reverse=True,
            )
        rows = [
            {
                "chunk_id": c["chunk_id"],
                "note_permalink": c["note_permalink"],
                "heading_path": c["heading_path"],
                "snippet": c["snippet"],
                "valid_from": c.get("valid_from"),
                "valid_until": c.get("valid_until"),
                "superseded_by": c.get("superseded_by"),
                "project": c.get("project"),
                "score": c["rerank_score"],  # use cross-encoder score, not RRF
            }
            for c in ranked
        ]
    rows = _dedupe_by_note(rows)[:k]
    return [
        Hit(
            chunk_id=r["chunk_id"],
            permalink=r["note_permalink"],
            heading_path=r["heading_path"],
            snippet=r["snippet"],
            score=r["score"],
            valid_from=r.get("valid_from"),
            valid_until=r.get("valid_until"),
            superseded_by=r.get("superseded_by"),
            project=r.get("project"),
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


def semantic_neighbors(
    con: duckdb.DuckDBPyConnection, permalink: str, *, k: int = 5, min_score: float = 0.0
) -> list[dict]:
    """Top-`k` semantically-nearest live notes to `permalink`, by cosine over the
    note's own indexed chunk vectors (no re-embedding). Excludes the note itself and
    superseded notes. Best-effort: any failure (no vectors, missing note, empty index)
    → []. Reusable core for build_context's `related` and a future `cairn link`."""
    try:
        dim = _dim(con)
        if dim <= 0:
            return []
        vecs = [
            r[0]
            for r in con.execute(
                "SELECT ce.vec FROM chunk_embeddings ce "
                "JOIN chunks c ON ce.chunk_id = c.chunk_id "
                "WHERE c.note_permalink = ?",
                [permalink],
            ).fetchall()
        ]
        if not vecs:
            return []
        n = len(vecs)
        centroid = [sum(col) / n for col in zip(*vecs, strict=False)]  # note-level mean vector
        rows = con.execute(
            f"SELECT n.permalink, n.title, "
            f"max(array_cosine_similarity(ce.vec, ?::FLOAT[{dim}])) AS sim "
            f"FROM chunk_embeddings ce "
            f"JOIN chunks c ON ce.chunk_id = c.chunk_id "
            f"JOIN notes n ON c.note_permalink = n.permalink "
            f"WHERE n.permalink != ? AND n.superseded_by IS NULL "
            f"GROUP BY n.permalink, n.title ORDER BY sim DESC LIMIT ?",
            [centroid, permalink, k],
        ).fetchall()
        return [
            {"permalink": r[0], "title": r[1], "score": round(float(r[2]), 4)}
            for r in rows
            if float(r[2]) >= min_score
        ]
    except Exception:
        return []


def get_note(con: duckdb.DuckDBPyConnection, permalink: str) -> dict | None:
    """Hydrate full note text and metadata by permalink."""
    row = con.execute(
        "SELECT permalink, path, title, type, valid_from, valid_until, superseded_by, project "
        "FROM notes WHERE permalink = ?",
        [permalink],
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
        "valid_from": from_db(row[4]).isoformat() if row[4] is not None else None,
        "valid_until": from_db(row[5]).isoformat() if row[5] is not None else None,
        "superseded_by": row[6],
        "project": row[7],
        "text": "\n\n".join(c[0] for c in chunks),
    }
