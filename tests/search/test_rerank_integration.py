# SPDX-License-Identifier: Apache-2.0
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAIRN_RUN_INTEGRATION") != "1",
    reason="set CAIRN_RUN_INTEGRATION=1 to run (downloads reranker model)",
)


def test_rerank_candidates_orders_by_relevance():
    from cairn.search.rerank import rerank_candidates

    cands = [
        {"chunk_id": "a", "text": "the capital of france is paris"},
        {"chunk_id": "b", "text": "bananas are a good source of potassium"},
    ]
    out = rerank_candidates("what is the capital of france", cands, top_k=2)
    assert out[0]["chunk_id"] == "a"  # the relevant doc ranks first
    assert "rerank_score" in out[0]
