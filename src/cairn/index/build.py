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


def build_fts(con: duckdb.DuckDBPyConnection) -> None:
    """(Re)build the BM25 full-text index over chunk text. Must be called after
    any change to `chunks` — DuckDB's FTS index does not auto-update."""
    con.execute("PRAGMA create_fts_index('chunks', 'chunk_id', 'text', overwrite=1)")


def bm25_search(
    con: duckdb.DuckDBPyConnection, query: str, limit: int = 10
) -> list[tuple[str, str, float]]:
    """Return [(chunk_id, heading_path, score)] ranked by BM25. Empty if the FTS
    index has not been built."""
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
    return [(r[0], r[1], float(r[2])) for r in rows]
