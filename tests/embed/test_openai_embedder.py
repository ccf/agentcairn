# SPDX-License-Identifier: Apache-2.0
from cairn.embed.openai_embedder import OpenAIEmbedder


def make(cap):
    def post(url, payload, headers):
        cap.append((url, payload, headers))
        n = len(payload["input"])
        return {"data": [{"index": i, "embedding": [float(i)]} for i in range(n)]}

    return post


def test_symmetric_no_input_type_and_model_id():
    cap = []
    emb = OpenAIEmbedder(api_key="k", post=make(cap))
    emb.embed(["a"])
    emb.embed_query("q")
    assert emb.model_id == "openai:text-embedding-3-small"
    assert "input_type" not in cap[0][1]  # symmetric — no input_type
    assert cap[0][0] == "https://api.openai.com/v1/embeddings"
    assert cap[0][2]["Authorization"] == "Bearer k"


def test_custom_base_url():
    cap = []
    emb = OpenAIEmbedder(api_key="k", base_url="https://proxy/v1", post=make(cap))
    emb.embed(["a"])
    assert cap[0][0] == "https://proxy/v1/embeddings"


def test_dim_lazy_cached():
    calls = {"n": 0}

    def post(url, payload, headers):
        calls["n"] += 1
        return {"data": [{"index": 0, "embedding": [0.0, 1.0]}]}

    emb = OpenAIEmbedder(api_key="k", post=post)
    assert emb.dim == 2 and emb.dim == 2 and calls["n"] == 1


def test_get_embedder_wiring(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.delenv("CAIRN_EMBED_MODEL", raising=False)
    from cairn.embed import get_embedder

    assert get_embedder("openai").model_id == "openai:text-embedding-3-small"
