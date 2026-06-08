# SPDX-License-Identifier: Apache-2.0
from cairn.embed import FakeEmbedder


def test_fake_embedder_shape_and_determinism():
    e = FakeEmbedder(dim=8)
    assert e.dim == 8
    assert e.model_id == "fake-8"
    v1 = e.embed(["hello", "world"])
    assert len(v1) == 2 and all(len(v) == 8 for v in v1)
    # deterministic: same text -> same vector
    assert e.embed(["hello"])[0] == v1[0]
    # query path returns one vector of the right dim
    q = e.embed_query("hello")
    assert len(q) == 8 and q == v1[0]
    # roughly unit-normalized
    assert abs(sum(x * x for x in q) - 1.0) < 1e-6
