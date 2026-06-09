# SPDX-License-Identifier: Apache-2.0
from cairn_bench.adapters import locomo, longmemeval


def test_longmemeval_adapter_notes_and_gold(lme_instances):
    inst = next(i for i in lme_instances if i["question_id"] == "synth_multi_1")
    notes, queries = longmemeval.adapt(inst)
    # one note per haystack session
    assert {n.permalink for n in notes} == {"s_a", "s_b", "s_distract"}
    q = queries[0]
    assert q.gold_sessions == {"s_a", "s_b"}
    # gold turn ids are positional 1-based on evidence (has_answer) turns
    assert "s_a_1" in q.gold_turns and "s_b_1" in q.gold_turns
    assert q.is_abstention is False
    # turn id is embedded in a header so it survives chunking
    body = next(n for n in notes if n.permalink == "s_a").body
    assert "s_a_1" in body


def test_longmemeval_abstention_flag(lme_instances):
    inst = next(i for i in lme_instances if i["question_id"].endswith("_abs"))
    _notes, queries = longmemeval.adapt(inst)
    assert queries[0].is_abstention is True
    assert queries[0].gold_sessions == set()


def test_locomo_adapter_notes_queries_and_normalization(locomo_samples):
    notes, queries = locomo.adapt(locomo_samples[0])
    assert {n.permalink for n in notes} == {"conv-synth-1_session_1", "conv-synth-1_session_2"}
    # category 5 (adversarial) is excluded from retrieval queries
    cats = {q.category for q in queries}
    assert 5 not in cats
    # malformed dia_id "D1:02" normalizes and matches the header-embedded "D1:2"
    q_age = next(q for q in queries if q.category == 1)
    assert q_age.gold_turns == {"D1:2", "D1:3"}
    body = next(n for n in notes if n.permalink == "conv-synth-1_session_1").body
    assert "D1:2" in body


def test_locomo_normalize_dia_id():
    assert locomo.normalize_dia_id("D1:02") == "D1:2"
    assert locomo.normalize_dia_id("D30:05") == "D30:5"


def test_build_scoped_index(lme_instances, tmp_path):
    from cairn_bench.adapters import longmemeval
    from cairn_bench.build import build_scoped_index

    from cairn.embed import FakeEmbedder
    from cairn.search import search

    inst = next(i for i in lme_instances if i["question_id"] == "synth_multi_1")
    notes, _q = longmemeval.adapt(inst)
    con, chunk_count = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        assert chunk_count >= 3  # at least one chunk per session
        hits = search(con, "cat named Mochi", embedder=FakeEmbedder(dim=8), k=10)
        assert any(h.permalink in {"s_a", "s_b"} for h in hits)
    finally:
        con.close()
