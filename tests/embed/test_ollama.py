# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from cairn.embed.ollama_embedder import OllamaEmbedder


class FakePost:
    """Records calls and returns canned embeddings (one 3-d vec per input)."""

    def __init__(self, vec=(0.1, 0.2, 0.3), raises=None, embeddings=None):
        self.calls = []
        self._vec = list(vec)
        self._raises = raises
        self._embeddings = embeddings

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        if self._raises is not None:
            raise self._raises
        if self._embeddings is not None:
            return {"embeddings": self._embeddings}
        return {"embeddings": [list(self._vec) for _ in payload["input"]]}


def test_embed_request_shape_and_doc_prefix():
    post = FakePost()
    emb = OllamaEmbedder(model="nomic-embed-text", host="http://h:11434", post=post)
    out = emb.embed(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    url, payload = post.calls[-1]
    assert url == "http://h:11434/api/embed"
    assert payload == {
        "model": "nomic-embed-text",
        "input": ["search_document: a", "search_document: b"],
    }


def test_embed_query_uses_query_prefix():
    post = FakePost()
    emb = OllamaEmbedder(model="nomic-embed-text", host="http://h:11434", post=post)
    v = emb.embed_query("q")
    assert v == [0.1, 0.2, 0.3]
    assert post.calls[-1][1]["input"] == ["search_query: q"]


def test_non_nomic_model_no_prefix():
    post = FakePost()
    emb = OllamaEmbedder(model="mxbai-embed-large", post=post)
    emb.embed(["x"])
    assert post.calls[-1][1]["input"] == ["x"]


def test_dim_is_lazy_and_cached():
    post = FakePost()
    emb = OllamaEmbedder(post=post)
    assert post.calls == []  # construction does NOT hit the server
    assert emb.dim == 3  # first access probes once
    assert len(post.calls) == 1
    assert emb.dim == 3  # cached: no further calls
    assert len(post.calls) == 1


def test_model_id():
    assert (
        OllamaEmbedder(model="nomic-embed-text", post=FakePost()).model_id
        == "ollama:nomic-embed-text"
    )


def test_error_wraps_actionably():
    post = FakePost(raises=ConnectionError("refused"))
    emb = OllamaEmbedder(model="nomic-embed-text", host="http://h:11434", post=post)
    with pytest.raises(RuntimeError) as ei:
        emb.embed_query("q")
    msg = str(ei.value)
    assert "http://h:11434" in msg and "nomic-embed-text" in msg
    assert "ollama serve" in msg and "pull" in msg


def test_empty_embeddings_raises():
    emb = OllamaEmbedder(post=FakePost(embeddings=[]))
    with pytest.raises(RuntimeError):
        emb.embed(["x"])
