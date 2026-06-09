# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from cairn_bench.ablation import run_arm
from cairn_bench.adapters import locomo, longmemeval
from cairn_bench.build import build_scoped_index
from cairn_bench.config import ARMS

from cairn.embed import FakeEmbedder

KS = [1, 3, 5, 10, 20]


def _arm(name):
    return next(a for a in ARMS if a.name == name)


def test_longmemeval_pipeline_recovers_gold(lme_instances, tmp_path):
    inst = next(i for i in lme_instances if i["question_id"] == "synth_multi_1")
    notes, queries = longmemeval.adapt(inst)
    con, chunks = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        res = run_arm(
            con,
            _arm("hybrid+graph-boost"),
            queries[0],
            FakeEmbedder(dim=8),
            ks=KS,
            pool=max(200, chunks),
        )
    finally:
        con.close()
    # tiny corpus: both gold sessions must be in the top-20 -> session recall@20 == 1.0
    assert res["session"]["recall@20"] == 1.0


def test_locomo_pipeline_turn_gold(locomo_samples, tmp_path):
    notes, queries = locomo.adapt(locomo_samples[0])
    con, chunks = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        q = next(q for q in queries if q.category == 4)  # single-hop "name of cat"
        res = run_arm(
            con,
            _arm("bm25-only"),
            q,
            FakeEmbedder(dim=8),
            ks=KS,
            pool=max(200, chunks),
        )
    finally:
        con.close()
    assert res["turn"]["recall@20"] == 1.0  # gold dia_id D1:1 is recoverable
