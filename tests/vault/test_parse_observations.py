# SPDX-License-Identifier: Apache-2.0
from cairn.vault.parse import parse_observation_line


def test_full_observation():
    obs = parse_observation_line("- [method] Pour over highlights flavor #brewing #coffee (slow)")
    assert obs.category == "method"
    assert obs.content == "Pour over highlights flavor"
    assert obs.tags == ["brewing", "coffee"]
    assert obs.context == "slow"


def test_observation_without_tags_or_context():
    obs = parse_observation_line("- [fact] Water boils at 100C")
    assert obs.category == "fact"
    assert obs.content == "Water boils at 100C"
    assert obs.tags == []
    assert obs.context is None


def test_non_observation_returns_none():
    assert parse_observation_line("- [[Tea]]") is None
    assert parse_observation_line("just text") is None


def test_context_must_trail_tags():
    # Canonical order: tags then trailing (context)
    obs = parse_observation_line("- [method] Pour over #brewing (slow)")
    assert obs.context == "slow"
    assert obs.tags == ["brewing"]
    # Reversed order: a paren before a trailing tag is NOT context; it stays in content
    obs2 = parse_observation_line("- [method] Pour over (slow) #brewing")
    assert obs2.context is None
    assert "(slow)" in obs2.content
    assert obs2.tags == ["brewing"]
