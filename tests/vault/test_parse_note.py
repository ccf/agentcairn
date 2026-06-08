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


def test_inline_field_on_observation_line_is_not_captured():
    note = parse_note("---\ntitle: T\n---\n- [fact] origin [origin:: Ethiopia]\n")
    assert note.observations[0].category == "fact"
    assert "origin" not in note.inline_fields


def test_code_blocks_are_not_parsed_as_structure():
    md = (
        "---\ntitle: T\n---\n"
        "Real prose links to [[RealTarget]] and (env:: prod).\n\n"
        "```python\n"
        "x = '- [method] fake observation'\n"
        "link = '[[FakeLink]]'\n"
        "cfg = 'retries:: 3'\n"
        "rel = '- uses [[FakeRel]]'\n"
        "```\n\n"
        "- uses [[RealRel]]\n"
        "Inline `[[InlineFake]]` should be ignored too.\n"
    )
    note = parse_note(md)
    assert note.wikilinks == ["RealTarget", "RealRel"]  # no FakeLink/FakeRel/InlineFake
    assert all("Fake" not in r.target for r in note.relations)  # FakeRel not a relation
    assert any(r.target == "RealRel" and r.rel_type == "uses" for r in note.relations)
    assert "retries" not in note.inline_fields  # code-block field ignored
    assert note.inline_fields.get("env") == "prod"  # real prose field kept
    assert note.observations == []  # fake observation in code ignored
