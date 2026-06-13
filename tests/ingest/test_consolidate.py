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


def _resp(relation):
    return {"content": [{"type": "text", "text": __import__("json").dumps({"relation": relation})}]}


def test_llm_consolidator_parses_each_verdict(monkeypatch):
    import cairn.ingest.consolidate as cmod
    from cairn.ingest.consolidate import ConsolidationVerdict, LLMConsolidator, Neighbor

    nb = Neighbor(permalink="old", text="Fly RAM scaled to 2GB", timestamp="t1")
    for relation, expected in [
        ("distinct", ConsolidationVerdict.DISTINCT),
        ("duplicate", ConsolidationVerdict.DUPLICATE),
        ("supersedes", ConsolidationVerdict.SUPERSEDES),
    ]:
        monkeypatch.setattr(cmod, "_anthropic_request", lambda p, k, t, _r=relation: _resp(_r))
        c = LLMConsolidator(api_key="k", model="m", timeout=5.0)
        assert c.classify(new_text="Fly RAM scaled to 4GB", new_ts="t2", neighbor=nb) == expected


def test_llm_consolidator_failsafe_distinct(monkeypatch):
    import cairn.ingest.consolidate as cmod
    from cairn.ingest.consolidate import ConsolidationVerdict, LLMConsolidator, Neighbor

    nb = Neighbor(permalink="o", text="x", timestamp=None)
    c = LLMConsolidator(api_key="k", model="m", timeout=5.0)

    monkeypatch.setattr(cmod, "_anthropic_request", lambda p, k, t: _resp("merge?!"))
    assert c.classify(new_text="y", new_ts=None, neighbor=nb) == ConsolidationVerdict.DISTINCT

    monkeypatch.setattr(
        cmod, "_anthropic_request", lambda p, k, t: {"content": [{"type": "text", "text": "nope"}]}
    )
    assert c.classify(new_text="y", new_ts=None, neighbor=nb) == ConsolidationVerdict.DISTINCT

    def boom(p, k, t):
        raise TimeoutError("down")

    monkeypatch.setattr(cmod, "_anthropic_request", boom)
    assert c.classify(new_text="y", new_ts=None, neighbor=nb) == ConsolidationVerdict.DISTINCT


def test_resolve_consolidator(monkeypatch):
    from cairn.ingest.consolidate import LLMConsolidator, resolve_consolidator

    env = {"CAIRN_JUDGE": "anthropic", "ANTHROPIC_API_KEY": "k", "CAIRN_CONSOLIDATE": "true"}
    assert isinstance(resolve_consolidator(env=env), LLMConsolidator)
    assert resolve_consolidator(env={**env, "CAIRN_CONSOLIDATE": "false"}) is None
    assert (
        resolve_consolidator(env={"CAIRN_JUDGE": "anthropic", "CAIRN_CONSOLIDATE": "true"}) is None
    )  # noqa: E501
    assert (
        resolve_consolidator(env={"CAIRN_JUDGE": "embedding", "CAIRN_CONSOLIDATE": "true"}) is None
    )  # noqa: E501
