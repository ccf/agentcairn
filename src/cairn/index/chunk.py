# SPDX-License-Identifier: Apache-2.0
"""Split a Note's body into retrieval chunks: one or more per markdown header
section, each prefixed with a semantic anchor and carrying provenance back to
the source note + heading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from cairn.vault import Note


@dataclass
class Chunk:
    chunk_id: str
    note_permalink: str | None
    heading_path: str  # e.g. "Coffee > Brewing"
    ordinal: int
    text: str  # anchor-prefixed, ready to embed/index


def _sections(body: str) -> list[tuple[str, str]]:
    """Yield (heading_path_tail, section_body). Text before the first header
    goes under heading ''. Only ATX headers (#..######) split sections."""
    sections: list[tuple[str, str]] = []
    current_head = ""
    buf: list[str] = []
    for line in body.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#") and stripped.lstrip("#").startswith(" "):
            sections.append((current_head, "\n".join(buf).strip()))
            current_head = stripped.lstrip("#").strip()
            buf = []
        else:
            buf.append(line)
    sections.append((current_head, "\n".join(buf).strip()))
    return [(h, b) for h, b in sections if b]


def _windows(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    out: list[str] = []
    cur = ""
    for para in text.split("\n"):
        if cur and len(cur) + len(para) + 1 > max_chars:
            out.append(cur.strip())
            cur = ""
        cur = f"{cur}\n{para}" if cur else para
    if cur.strip():
        out.append(cur.strip())
    # hard-split any window still over the limit
    final: list[str] = []
    for w in out:
        while len(w) > max_chars:
            final.append(w[:max_chars])
            w = w[max_chars:]
        if w:
            final.append(w)
    return final


def chunk_note(note: Note, max_chars: int = 1500) -> list[Chunk]:
    title = str(note.frontmatter.get("title") or note.permalink or "")
    chunks: list[Chunk] = []
    ordinal = 0
    for head_tail, section_body in _sections(note.body):
        heading_path = f"{title} > {head_tail}".strip(" >") if head_tail else title
        section_label = head_tail or title or "note"
        for window in _windows(section_body, max_chars):
            anchor = f"Title: {title} | Section: {section_label} | "
            cid = hashlib.sha256(f"{note.permalink}\x00{ordinal}\x00{window}".encode()).hexdigest()[
                :16
            ]
            chunks.append(
                Chunk(
                    chunk_id=cid,
                    note_permalink=note.permalink,
                    heading_path=heading_path or section_label,
                    ordinal=ordinal,
                    text=anchor + window,
                )
            )
            ordinal += 1
    return chunks
