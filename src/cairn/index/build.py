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
from cairn.vault import parse_note


@dataclass
class IndexStats:
    notes: int = 0
    chunks: int = 0


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _delete_note(con: duckdb.DuckDBPyConnection, permalink: str) -> None:
    con.execute(
        "DELETE FROM chunk_embeddings WHERE chunk_id IN "
        "(SELECT chunk_id FROM chunks WHERE note_permalink = ?)",
        [permalink],
    )
    con.execute("DELETE FROM chunks WHERE note_permalink = ?", [permalink])
    con.execute("DELETE FROM links WHERE src_permalink = ?", [permalink])
    con.execute("DELETE FROM notes WHERE permalink = ?", [permalink])


def index_note(con: duckdb.DuckDBPyConnection, path: Path, embedder: Embedder) -> int:
    """(Re)index a single note file. Returns number of chunks. Permalink falls
    back to the file stem when frontmatter omits it."""
    text = path.read_text()
    note = parse_note(text)
    permalink = note.permalink or path.stem
    note.permalink = permalink  # ensure downstream rows are keyed consistently

    _delete_note(con, permalink)
    con.execute(
        "INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)",
        [
            permalink,
            str(path),
            str(note.frontmatter.get("title") or ""),
            str(note.frontmatter.get("type") or ""),
            _content_hash(text),
            path.stat().st_mtime,
        ],
    )
    chunks = chunk_note(note)
    if chunks:
        vecs = embedder.embed([c.text for c in chunks])
        for c, vec in zip(chunks, vecs, strict=False):
            con.execute(
                "INSERT INTO chunks VALUES (?, ?, ?, ?, ?)",
                [c.chunk_id, permalink, c.heading_path, c.ordinal, c.text],
            )
            con.execute("INSERT INTO chunk_embeddings VALUES (?, ?)", [c.chunk_id, vec])
    for t in note.wikilinks:
        con.execute("INSERT INTO links VALUES (?, ?, ?)", [permalink, t, "links_to"])
    for rel in note.relations:
        if rel.rel_type != "links_to":
            con.execute("INSERT INTO links VALUES (?, ?, ?)", [permalink, rel.target, rel.rel_type])
    return len(chunks)


def index_vault(con: duckdb.DuckDBPyConnection, vault_dir: str, embedder: Embedder) -> IndexStats:
    stats = IndexStats()
    for path in sorted(Path(vault_dir).rglob("*.md")):
        stats.chunks += index_note(con, path, embedder)
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

    on_disk = {p.stem: p for p in sorted(Path(vault_dir).rglob("*.md"))}
    # map permalink->(path, hash, mtime) currently in the index
    indexed = {
        row[0]: (row[1], row[2], row[3])
        for row in con.execute("SELECT permalink, path, content_hash, mtime FROM notes").fetchall()
    }

    # process files on disk: add new, update changed
    seen_permalinks: set[str] = set()
    for path in on_disk.values():
        text = path.read_text()
        permalink = parse_note(text).permalink or path.stem
        seen_permalinks.add(permalink)
        prev = indexed.get(permalink)
        cur_hash = _content_hash(text)
        if prev is None:
            index_note(con, path, embedder)
            stats.added += 1
        elif prev[1] != cur_hash:
            index_note(con, path, embedder)
            stats.updated += 1

    # deletions: indexed notes whose file no longer exists
    for permalink in set(indexed) - seen_permalinks:
        _delete_note(con, permalink)
        stats.deleted += 1

    if stats.added or stats.updated or stats.deleted or stats.rebuilt:
        build_fts(con)
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
