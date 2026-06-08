# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note, write_note

SAMPLE = """\
---
title: Coffee
type: note
permalink: coffee
tags:
- drinks
- morning
---

Notes about [[Coffee]] brewing.

- [method] Pour over highlights flavor #brewing (slow)
- pairs_with [[Tea]]
"""


def test_roundtrip_is_idempotent():
    note = parse_note(SAMPLE)
    out = write_note(note)
    # Parsing the written output yields an equivalent Note (stable fixpoint).
    reparsed = parse_note(out)
    assert reparsed.frontmatter == note.frontmatter
    assert reparsed.body.strip() == note.body.strip()
    # Writing again is byte-identical (idempotent).
    assert write_note(reparsed) == out


def test_write_preserves_frontmatter_keys():
    note = parse_note(SAMPLE)
    out = write_note(note)
    assert "title: Coffee" in out
    assert "permalink: coffee" in out
    assert out.startswith("---")


def test_write_preserves_frontmatter_key_order():
    note = parse_note("---\ntitle: T\ntype: note\npermalink: t\n---\nbody\n")
    out = write_note(note)
    assert out.index("title:") < out.index("type:") < out.index("permalink:")


def test_no_frontmatter_note_has_no_yaml_block():
    note = parse_note("just plain prose, no frontmatter\n")
    out = write_note(note)
    assert not out.startswith("---")
    assert "just plain prose" in out


def test_write_persists_permalink_field_even_if_absent_from_frontmatter():
    from cairn.vault import Note

    note = Note(permalink="mem-123", frontmatter={"title": "X"}, body="hello")
    out = write_note(note)
    assert "permalink: mem-123" in out
    assert parse_note(out).permalink == "mem-123"  # round-trips back
