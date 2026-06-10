# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import pytest

from cairn.embed import get_embedder
from cairn.embed.ollama_embedder import OllamaEmbedder


def test_get_embedder_ollama_from_env(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_MODEL", "mxbai-embed-large")
    monkeypatch.setenv("OLLAMA_HOST", "http://box:11434")
    emb = get_embedder("ollama")
    assert isinstance(emb, OllamaEmbedder)
    assert emb.model_id == "ollama:mxbai-embed-large"
    assert emb._host == "http://box:11434"  # constructed, no network performed
    assert emb._dim is None  # dim not probed at construction — no HTTP


def test_get_embedder_ollama_defaults(monkeypatch):
    monkeypatch.delenv("CAIRN_EMBED_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    emb = get_embedder("ollama")
    assert emb.model_id == "ollama:nomic-embed-text"
    assert emb._dim is None  # dim not probed at construction — no HTTP


def test_get_embedder_fastembed_honors_env(monkeypatch):
    # CAIRN_EMBED_MODEL selects the FastEmbed model (default bge-small); patch the
    # backend so no ONNX model is downloaded/loaded at construction.
    captured = {}

    class _FakeFE:
        def __init__(self, model_name="BAAI/bge-small-en-v1.5"):
            captured["model"] = model_name

    monkeypatch.setattr("cairn.embed.fastembed_embedder.FastEmbedEmbedder", _FakeFE)
    monkeypatch.setenv("CAIRN_EMBED_MODEL", "mixedbread-ai/mxbai-embed-large-v1")
    get_embedder("fastembed")
    assert captured["model"] == "mixedbread-ai/mxbai-embed-large-v1"

    captured.clear()
    monkeypatch.delenv("CAIRN_EMBED_MODEL", raising=False)
    get_embedder("fastembed")
    assert captured["model"] == "BAAI/bge-small-en-v1.5"


def test_get_embedder_unknown_still_raises():
    with pytest.raises(ValueError):
        get_embedder("bogus")
