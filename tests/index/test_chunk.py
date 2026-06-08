# SPDX-License-Identifier: Apache-2.0
from cairn.index.chunk import chunk_note
from cairn.vault import parse_note

NOTE = """---
title: Coffee
permalink: coffee
---
Intro paragraph about coffee.

## Brewing
Pour over is great.

## Storage
Keep beans sealed.
"""


def test_chunks_have_anchor_and_provenance():
    note = parse_note(NOTE)
    chunks = chunk_note(note, max_chars=1500)
    assert len(chunks) >= 3  # intro + Brewing + Storage
    # each chunk carries a semantic-anchor prefix and provenance
    brewing = next(c for c in chunks if c.heading_path.endswith("Brewing"))
    assert brewing.text.startswith("Title: Coffee | Section: Brewing |")
    assert "Pour over is great." in brewing.text
    assert brewing.note_permalink == "coffee"
    assert all(c.chunk_id and c.note_permalink == "coffee" for c in chunks)
    # ordinals are unique and contiguous from 0
    assert sorted(c.ordinal for c in chunks) == list(range(len(chunks)))


def test_long_section_is_split_by_max_chars():
    big = "para. " * 1000  # ~6000 chars
    note = parse_note(f"---\ntitle: T\npermalink: t\n---\n## S\n{big}\n")
    chunks = chunk_note(note, max_chars=1500)
    seg = [c for c in chunks if c.heading_path.endswith("S")]
    assert len(seg) >= 3
    assert all(len(c.text) <= 1500 + 200 for c in seg)  # anchor prefix adds a little
