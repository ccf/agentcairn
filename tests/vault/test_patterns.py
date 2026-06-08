# SPDX-License-Identifier: Apache-2.0
from cairn.vault.patterns import OBSERVATION_RE, RELATION_RE, WIKILINK_RE


def test_observation_matches_single_bracket_not_double():
    assert OBSERVATION_RE.match("- [method] pour over #brewing (manual)")
    assert (
        OBSERVATION_RE.match("- [[Target]]") is None
    )  # double bracket = relation, not observation


def test_relation_matches_double_bracket_forms():
    assert RELATION_RE.match("- [[Tea]]").group(3) == "Tea"
    assert RELATION_RE.match("- pairs_with [[Tea]]").group(2) == "pairs_with"
    assert RELATION_RE.match('- "pairs well with" [[Tea]]').group(1) == "pairs well with"


def test_wikilink_extracts_target_without_alias():
    assert WIKILINK_RE.findall("see [[Tea|the tea note]] and [[Coffee]]") == ["Tea", "Coffee"]
