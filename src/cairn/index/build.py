# SPDX-License-Identifier: Apache-2.0
"""Populate the DuckDB index from a markdown vault. Idempotent per note:
re-indexing a note replaces its prior rows."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import duckdb

from cairn.embed.base import Embedder
from cairn.index.chunk import chunk_note
from cairn.temporal import parse_temporal, to_db
from cairn.vault import parse_note


def _safe_temporal(value: object):
    try:
        return parse_temporal(value)
    except (TypeError, ValueError):
        return None


@dataclass
class IndexStats:
    notes: int = 0
    chunks: int = 0


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _permalink_for(permalink_field: str | None, path: Path, vault_dir: str | None) -> str:
    """Derive a stable, unique permalink for a note.

    If the frontmatter supplies one, use it as-is.  Otherwise fall back to a
    vault-relative slug (``subdir/stem``) so that two notes with the same
    filename in different subdirectories never share a primary key.  When
    ``vault_dir`` is not available the bare stem is returned as a last resort.
    """
    if permalink_field:
        return permalink_field
    if vault_dir is not None:
        return Path(path).relative_to(vault_dir).with_suffix("").as_posix()  # e.g. "a/note"
    return Path(path).stem


def _delete_note(con: duckdb.DuckDBPyConnection, permalink: str) -> None:
    con.execute(
        "DELETE FROM chunk_embeddings WHERE chunk_id IN "
        "(SELECT chunk_id FROM chunks WHERE note_permalink = ?)",
        [permalink],
    )
    con.execute("DELETE FROM chunks WHERE note_permalink = ?", [permalink])
    con.execute("DELETE FROM links WHERE src_permalink = ?", [permalink])
    con.execute("DELETE FROM notes WHERE permalink = ?", [permalink])


def index_note(
    con: duckdb.DuckDBPyConnection,
    path: Path,
    embedder: Embedder,
    *,
    vault_dir: str | None = None,
) -> int:
    """(Re)index a single note file. Returns number of chunks.

    Permalink falls back to a vault-relative slug when frontmatter omits it,
    ensuring uniqueness across subdirectories.  Pass ``vault_dir`` so the
    fallback is relative to the vault root rather than the bare file stem.
    """
    text = path.read_text()
    note = parse_note(text)
    permalink = _permalink_for(note.permalink, path, vault_dir)
    note.permalink = permalink  # ensure downstream rows are keyed consistently

    _delete_note(con, permalink)
    fm = note.frontmatter
    con.execute(
        "INSERT INTO notes "
        "(permalink, path, title, type, content_hash, mtime, "
        " valid_from, valid_until, superseded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            permalink,
            str(path),
            str(fm.get("title") or ""),
            str(fm.get("type") or ""),
            _content_hash(text),
            path.stat().st_mtime,
            to_db(_safe_temporal(fm.get("valid_from"))),
            to_db(_safe_temporal(fm.get("valid_until"))),
            (fm.get("superseded_by") or None),
        ],
    )
    chunks = chunk_note(note)
    if chunks:
        vecs = embedder.embed([c.text for c in chunks])
        # strict=True raises ValueError when the embedder returns a different
        # number of vectors than there are chunks (silent partial indexing).
        for c, vec in zip(chunks, vecs, strict=True):
            con.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                [c.chunk_id, permalink, c.heading_path, c.ordinal, c.text],
            )
            con.execute("INSERT INTO chunk_embeddings VALUES (?, ?)", [c.chunk_id, vec])
    # dst_target holds the raw (unresolved) link target; resolution to a permalink
    # will happen in Plan 3 during graph-join queries.
    for t in note.wikilinks:
        con.execute("INSERT INTO links VALUES (?, ?, ?)", [permalink, t, "links_to"])
    for rel in note.relations:
        if rel.rel_type != "links_to":
            con.execute("INSERT INTO links VALUES (?, ?, ?)", [permalink, rel.target, rel.rel_type])
    return len(chunks)


def index_vault(con: duckdb.DuckDBPyConnection, vault_dir: str, embedder: Embedder) -> IndexStats:
    stats = IndexStats()
    for path in sorted(Path(vault_dir).rglob("*.md")):
        stats.chunks += index_note(con, path, embedder, vault_dir=vault_dir)
        stats.notes += 1
    return stats


@dataclass
class ReconcileStats:
    added: int = 0
    updated: int = 0
    deleted: int = 0
    rebuilt: bool = False


def reconcile(
    con: duckdb.DuckDBPyConnection,
    vault_dir: str,
    embedder: Embedder,
    *,
    model_id_override: str | None = None,
) -> ReconcileStats:
    """Bring the index in sync with the vault, re-processing only changed notes.
    A change in embedding model/dim triggers a full rebuild (vectors from
    different models are not comparable). Always rebuilds the FTS index when any
    content changed."""
    from cairn.index.schema import get_meta, set_meta

    model_id = model_id_override or embedder.model_id
    stats = ReconcileStats()

    if get_meta(con, "embedding_model") != model_id or get_meta(con, "embedding_dim") != str(
        embedder.dim
    ):
        con.execute("DROP TABLE IF EXISTS chunk_embeddings")
        ddl = (
            f"CREATE TABLE chunk_embeddings "
            f"(chunk_id VARCHAR PRIMARY KEY, vec FLOAT[{embedder.dim}])"
        )
        con.execute(ddl)
        con.execute("DELETE FROM chunks")
        con.execute("DELETE FROM links")
        con.execute("DELETE FROM notes")
        set_meta(con, "embedding_model", model_id)
        set_meta(con, "embedding_dim", str(embedder.dim))
        stats.rebuilt = True

    # map permalink->(path, hash, mtime) currently in the index
    indexed = {
        row[0]: (row[1], row[2], row[3])
        for row in con.execute("SELECT permalink, path, content_hash, mtime FROM notes").fetchall()
    }

    # process files on disk: add new, update changed, or fix stale path on move
    # Iterate the rglob list directly (not a stem-keyed dict) so that notes in
    # different subdirectories with the same filename are each processed.
    seen_permalinks: set[str] = set()
    for path in sorted(Path(vault_dir).rglob("*.md")):
        text = path.read_text()
        permalink = _permalink_for(parse_note(text).permalink, path, vault_dir)
        seen_permalinks.add(permalink)
        prev = indexed.get(permalink)
        cur_hash = _content_hash(text)
        if prev is None:
            index_note(con, path, embedder, vault_dir=vault_dir)
            stats.added += 1
        elif prev[1] != cur_hash:
            index_note(con, path, embedder, vault_dir=vault_dir)
            stats.updated += 1
        elif prev[0] != str(path):
            # content unchanged but file moved: update stored path only,
            # no re-embedding and no FTS rebuild required.
            con.execute("UPDATE notes SET path = ? WHERE permalink = ?", [str(path), permalink])

    # deletions: indexed notes whose file no longer exists
    for permalink in set(indexed) - seen_permalinks:
        _delete_note(con, permalink)
        stats.deleted += 1

    if stats.added or stats.updated or stats.deleted or stats.rebuilt:
        build_fts(con)
    # Cache the whole-haystack token estimate for `cairn savings` (read cheaply
    # at recall time; recomputed only here, off the hot path). Recompute on any
    # change, or once if the key is missing (index built before this feature).
    if (
        stats.added
        or stats.updated
        or stats.deleted
        or stats.rebuilt
        or get_meta(con, "haystack_tokens") is None
    ):
        total = con.execute(
            "SELECT COALESCE(SUM(CAST((LENGTH(text)+3)/4 AS BIGINT)),0) FROM chunks"
        ).fetchone()[0]
        set_meta(con, "haystack_tokens", str(int(total)))
    return stats


def build_fts(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)build the BM25 full-text index over chunk text. Must be called after
    any change to `chunks` — DuckDB's FTS index does not auto-update."""
    con.execute("PRAGMA create_fts_index('chunks', 'chunk_id', 'text', overwrite=1)")


def bm25_search(
    con: duckdb.DuckDBPyConnection, query: str, limit: int = 10
) -> list[tuple[str, str, float]]:
    """Return [(chunk_id, heading_path, score)] ranked by BM25. Empty if the FTS
    index has not been built."""
    try:
        rows = con.execute(
            """
            WITH scored AS (
                SELECT c.chunk_id, c.heading_path,
                       fts_main_chunks.match_bm25(c.chunk_id, ?) AS score
                FROM chunks c
            )
            SELECT chunk_id, heading_path, score FROM scored
            WHERE score IS NOT NULL ORDER BY score DESC LIMIT ?
            """,
            [query, limit],
        ).fetchall()
    except duckdb.CatalogException:
        return []
    return [(r[0], r[1], float(r[2])) for r in rows]
