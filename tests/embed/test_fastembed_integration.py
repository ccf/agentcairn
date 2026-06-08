# SPDX-License-Identifier: Apache-2.0
"""Real-model test — downloads ~32MB on first run. Skipped unless
CAIRN_RUN_INTEGRATION=1 so the default suite stays fast and offline."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("CAIRN_RUN_INTEGRATION") != "1",
    reason="set CAIRN_RUN_INTEGRATION=1 to run (downloads model)",
)


def test_fastembed_bge_small_dims_and_determinism():
    from cairn.embed import FastEmbedEmbedder

    e = FastEmbedEmbedder()
    assert e.model_id == "BAAI/bge-small-en-v1.5"
    assert e.dim == 384
    vecs = e.embed(["the cat sat", "the cat sat"])
    assert len(vecs) == 2 and all(len(v) == 384 for v in vecs)
    assert vecs[0] == vecs[1]  # deterministic for identical input
    assert len(e.embed_query("a query")) == 384
