# SPDX-License-Identifier: Apache-2.0
"""Serialize a Note back to markdown without clobbering human edits.

The body string is authoritative for observations/relations/wikilinks/inline
fields (they are parsed *from* the body), so we re-emit it verbatim and only
re-render the frontmatter block. This makes parse->write a stable fixpoint."""

from __future__ import annotations

import frontmatter

from cairn.vault.models import Note


def write_note(note: Note) -> str:
    post = frontmatter.Post(note.body, **note.frontmatter)
    # frontmatter.dumps emits "---\n<yaml>---\n\n<body>"; normalize trailing newline.
    text = frontmatter.dumps(post)
    if not text.endswith("\n"):
        text += "\n"
    return text
