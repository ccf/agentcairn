# SPDX-License-Identifier: Apache-2.0
"""Serialize a Note back to markdown without clobbering human edits.

The body string is authoritative for observations/relations/wikilinks/inline
fields (they are parsed *from* the body), so we re-emit it verbatim and only
re-render the frontmatter block. This makes parse->write a stable fixpoint."""

from __future__ import annotations

import frontmatter

from cairn.vault.models import Note


def write_note(note: Note) -> str:
    fm = dict(note.frontmatter)
    if note.permalink is not None:
        # permalink is authoritative: fold it in (updates existing key in place, preserving order)
        fm["permalink"] = note.permalink
    if not fm:
        body = note.body
        return body if body.endswith("\n") else body + "\n"
    post = frontmatter.Post(note.body, **fm)
    # frontmatter.dumps emits "---\n<yaml>---\n\n<body>"; normalize trailing newline.
    # sort_keys=False preserves the order in which keys appear in the original file.
    text = frontmatter.dumps(post, sort_keys=False, width=4096)
    if not text.endswith("\n"):
        text += "\n"
    return text
