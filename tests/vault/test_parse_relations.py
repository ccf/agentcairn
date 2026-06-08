# SPDX-License-Identifier: Apache-2.0
from cairn.vault.parse import parse_relation_line


def test_bare_link_is_implicit_links_to():
    rel = parse_relation_line("- [[Chocolate Desserts]]")
    assert rel.rel_type == "links_to"
    assert rel.target == "Chocolate Desserts"


def test_typed_relation():
    rel = parse_relation_line("- pairs_with [[Tea]]")
    assert rel.rel_type == "pairs_with"
    assert rel.target == "Tea"


def test_quoted_multiword_relation_and_alias_ignored():
    rel = parse_relation_line('- "pairs well with" [[Dark Chocolate|cocoa]]')
    assert rel.rel_type == "pairs well with"
    assert rel.target == "Dark Chocolate"


def test_non_relation_returns_none():
    assert parse_relation_line("- [method] not a relation") is None
