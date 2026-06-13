# SPDX-License-Identifier: Apache-2.0
def test_verdict_values_and_neighbor():
    from cairn.ingest.consolidate import ConsolidationVerdict, Neighbor

    assert ConsolidationVerdict.DISTINCT == "distinct"
    assert ConsolidationVerdict.DUPLICATE == "duplicate"
    assert ConsolidationVerdict.SUPERSEDES == "supersedes"
    n = Neighbor(permalink="p", text="t", timestamp="t0")
    assert n.permalink == "p" and n.text == "t" and n.timestamp == "t0"


def test_gate_is_a_conservative_float():
    from cairn.ingest.consolidate import _CONSOLIDATE_GATE

    assert 0.5 < _CONSOLIDATE_GATE < 1.0  # a high cosine pre-gate
