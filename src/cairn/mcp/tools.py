# src/cairn/mcp/tools.py
# SPDX-License-Identifier: Apache-2.0
"""Pure tool functions for the MCP surface. Each returns JSON-serializable data
and (for reads) opens the index READ_ONLY per call — never holding the DuckDB
file lock across a reindex (Plan 3 connection-lifecycle guidance)."""

from __future__ import annotations

import re
from pathlib import Path

from cairn.embed import get_embedder
from cairn.ingest import write_derived_note
from cairn.ingest.dedup import content_hash
from cairn.ingest.redact import redact
from cairn.search import get_note, open_search, search
from cairn.vault import Note

# Over-fetch this many chunk candidates per requested note, then dedup by
# permalink so that a note with many chunks cannot consume all k slots.
_FETCH_FACTOR = 5


def _embedder(name: str | None):
    return None if name in (None, "none") else get_embedder(name)


def _open(index_path: str):
    """Open the index READ-ONLY, raising a clean ValueError if absent."""
    if not Path(index_path).exists():
        raise ValueError(f"no index at {index_path} — run `cairn reindex <vault>` first")
    return open_search(index_path)


def search_tool(
    index_path: str,
    query: str,
    *,
    embedder: str = "fastembed",
    k: int = 10,
    rerank: bool = False,
) -> dict:
    """Progressive-disclosure hybrid search: compact id + snippet index."""
    fetch = max(k * _FETCH_FACTOR, 25)
    con = _open(index_path)
    try:
        hits = search(con, query, embedder=_embedder(embedder), k=fetch, rerank=rerank)
    finally:
        con.close()
    # Dedup by permalink, keeping the highest-scoring chunk per note, up to k notes.
    seen_perms: set[str] = set()
    deduped = []
    for h in hits:
        if h.permalink not in seen_perms:
            seen_perms.add(h.permalink)
            deduped.append(h)
            if len(deduped) == k:
                break
    return {
        "query": query,
        "hits": [
            {
                "permalink": h.permalink,
                "heading_path": h.heading_path,
                "snippet": h.snippet.strip()[:240],
                "score": round(h.score, 4),
            }
            for h in deduped
        ],
    }


def recall_tool(
    index_path: str,
    query: str,
    *,
    embedder: str = "fastembed",
    k: int = 5,
    rerank: bool = False,
) -> dict:
    """Search then hydrate the top-k notes' full text (one-shot content)."""
    fetch = max(k * _FETCH_FACTOR, 25)
    con = _open(index_path)
    try:
        hits = search(con, query, embedder=_embedder(embedder), k=fetch, rerank=rerank)
        seen: set[str] = set()
        notes: list[dict] = []
        for h in hits:
            if h.permalink in seen:
                continue
            seen.add(h.permalink)
            note = get_note(con, h.permalink)
            if note is not None:
                note["score"] = round(h.score, 4)
                notes.append(note)
            if len(notes) == k:
                break
    finally:
        con.close()
    return {"query": query, "notes": notes}


def build_context_tool(index_path: str, permalink: str) -> dict:
    """Return a note plus its 1-hop graph neighbors from the links table.
    `dst_target` is raw/unresolved (Plan 3 caveat): a neighbor resolves when the
    target equals a note permalink or title; otherwise it is reported raw."""
    con = _open(index_path)
    try:
        root = get_note(con, permalink)
        if root is None:
            return {"root": None, "outgoing": [], "incoming": []}
        title = root.get("title")
        out_rows = con.execute(
            "SELECT DISTINCT dst_target, edge_type FROM links WHERE src_permalink = ?",
            [permalink],
        ).fetchall()
        outgoing = []
        for dst, edge in out_rows:
            n = get_note(con, dst)
            if n is None:
                # best-effort title match
                row = con.execute(
                    "SELECT permalink FROM notes WHERE title = ? LIMIT 1", [dst]
                ).fetchone()
                if row:
                    n = get_note(con, row[0])
            outgoing.append(
                {
                    "edge_type": edge,
                    "target": dst,
                    "permalink": n["permalink"] if n else None,
                    "title": n["title"] if n else None,
                }
            )
        in_rows = con.execute(
            "SELECT DISTINCT src_permalink FROM links WHERE dst_target = ? OR dst_target = ?",
            [permalink, title or permalink],
        ).fetchall()
        incoming = [{"permalink": r[0]} for r in in_rows if r[0] != permalink]
    finally:
        con.close()
    return {"root": root, "outgoing": outgoing, "incoming": incoming}


def recent_tool(index_path: str, *, n: int = 10) -> dict:
    """Most-recently-modified notes (notes table has mtime, not created)."""
    con = _open(index_path)
    try:
        rows = con.execute(
            "SELECT permalink, title, path, type FROM notes ORDER BY mtime DESC LIMIT ?",
            [n],
        ).fetchall()
    finally:
        con.close()
    return {"notes": [{"permalink": r[0], "title": r[1], "path": r[2], "type": r[3]} for r in rows]}


def _slugify(text: str, max_words: int = 6) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()
    return "-".join(words[:max_words]) or "memory"


def remember_tool(
    vault_root: str,
    text: str,
    *,
    title: str | None = None,
    tags: list[str] | None = None,
    subdir: str = "memories",
) -> dict:
    """Agent-loop capture: redact, build a non-lossy memory note, write it under
    the vault. Does not reindex (run `cairn sweep`/`reindex` to make it searchable)."""
    if not text or not text.strip():
        raise ValueError("remember: text must be non-empty")
    red = redact(text)
    body_text = red.text.strip()
    h = content_hash(body_text)
    slug = f"{_slugify(body_text)}-{h[:8]}"
    title_red = redact(title or body_text.splitlines()[0])
    tag_reds = [redact(t) for t in (tags or ["remembered"])]
    safe_title = title_red.text[:80]
    safe_tags = [tr.text for tr in tag_reds]
    # Count redactions across ALL written fields (body + title + tags), not just
    # the body — else a secret only in title/tags reports 0 and misrepresents.
    total_redactions = red.count + title_red.count + sum(tr.count for tr in tag_reds)
    note = Note(
        permalink=slug,
        frontmatter={
            "title": safe_title,
            "type": "memory",
            "permalink": slug,
            "tags": safe_tags,
            "source": "memory://agent/remember",
        },
        body=f"- [context] {body_text} #remembered\n",
    )
    path = write_derived_note(note, Path(vault_root), subdir=subdir)
    return {
        "permalink": slug,
        "path": str(path),
        "redactions": total_redactions,
        "note": "written; run `cairn sweep` or `cairn reindex` to make it searchable",
    }
