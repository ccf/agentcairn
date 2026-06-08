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
