# SPDX-License-Identifier: Apache-2.0
from cairn.vault import parse_note

SAMPLE = """\
---
title: Coffee
type: note
permalink: coffee
tags: [drinks, morning]
---

Notes about [[Coffee]] brewing. Pairs with [[Tea|green tea]].

- [method] Pour over highlights flavor #brewing (slow)
- pairs_with [[Tea]]
- [[Chocolate]]

rating:: 9
"""


def test_parse_note_extracts_all_parts():
    note = parse_note(SAMPLE)
    assert note.permalink == "coffee"
    assert note.frontmatter["title"] == "Coffee"
    assert note.frontmatter["tags"] == ["drinks", "morning"]
    # observations
    assert len(note.observations) == 1
    assert note.observations[0].category == "method"
    assert note.observations[0].tags == ["brewing"]
    # relations: typed + bare (implicit links_to)
    rels = {(r.rel_type, r.target) for r in note.relations}
    assert ("pairs_with", "Tea") in rels
    assert ("links_to", "Chocolate") in rels
    # body wikilinks (de-duplicated, in order of first appearance)
    assert note.wikilinks == ["Coffee", "Tea", "Chocolate"]
    # inline fields
    assert note.inline_fields["rating"] == "9"


def test_permalink_falls_back_to_none_when_absent():
    note = parse_note("---\ntitle: X\n---\nbody")
    assert note.permalink is None
    assert note.frontmatter["title"] == "X"
