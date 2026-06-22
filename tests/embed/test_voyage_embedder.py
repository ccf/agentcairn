# SPDX-License-Identifier: Apache-2.0
from cairn.embed.voyage_embedder import VoyageEmbedder


def make(captured):
    def post(url, payload, headers):
        captured.append((url, payload, headers))
        n = len(payload["input"])
        return {"data": [{"index": i, "embedding": [float(i), 0.0]} for i in range(n)]}

    return post


def test_model_id_and_input_types():
    cap = []
    emb = VoyageEmbedder(model="voyage-3", api_key="k", post=make(cap))
    emb.embed(["doc1", "doc2"])
    emb.embed_query("q")
    assert emb.model_id == "voyage:voyage-3"
    assert cap[0][0].endswith("/embeddings")
    assert cap[0][1]["input_type"] == "document"
    assert cap[1][1]["input_type"] == "query"
    assert cap[0][2]["Authorization"] == "Bearer k"


def test_dim_probes_lazily_and_caches():
    calls = {"n": 0}

    def post(url, payload, headers):
        calls["n"] += 1
        n = len(payload["input"])
        return {"data": [{"index": i, "embedding": [0.0, 1.0, 2.0]} for i in range(n)]}

    emb = VoyageEmbedder(api_key="k", post=post)
    assert emb.dim == 3
    assert emb.dim == 3  # cached
    assert calls["n"] == 1


def test_batches_over_128_in_input_order():
    cap = []
    emb = VoyageEmbedder(api_key="k", post=make(cap))
    out = emb.embed([f"d{i}" for i in range(130)])
    assert len(cap) == 2 and len(out) == 130  # 128 + 2
    assert out[0] == [0.0, 0.0] and out[129][0] == 1.0  # 2nd chunk's index-1 row


def test_get_embedder_wiring(monkeypatch):
    """get_embedder('voyage') wires up without hitting the network."""
    monkeypatch.setenv("VOYAGE_API_KEY", "test-key")
    monkeypatch.delenv("CAIRN_EMBED_MODEL", raising=False)
    from cairn.embed import get_embedder

    emb = get_embedder("voyage")
    assert emb.model_id == "voyage:voyage-3"
