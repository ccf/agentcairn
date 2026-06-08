# SPDX-License-Identifier: Apache-2.0
from cairn.vault.models import Note, Observation, Relation


def test_observation_holds_fields():
    obs = Observation(
        category="method", content="Pour over highlights flavor", tags=["brewing"], context="manual"
    )
    assert obs.category == "method"
    assert obs.tags == ["brewing"]
    assert obs.context == "manual"


def test_relation_defaults_to_links_to():
    rel = Relation(rel_type="links_to", target="Chocolate")
    assert rel.rel_type == "links_to"
    assert rel.target == "Chocolate"


def test_note_aggregates_parts():
    note = Note(
        permalink="coffee",
        frontmatter={"title": "Coffee", "type": "note", "tags": ["drinks"]},
        body="hello",
        observations=[Observation("method", "x", [], None)],
        relations=[Relation("links_to", "Tea")],
        wikilinks=["Tea"],
    )
    assert note.permalink == "coffee"
    assert note.frontmatter["title"] == "Coffee"
    assert note.observations[0].category == "method"
    assert note.wikilinks == ["Tea"]
