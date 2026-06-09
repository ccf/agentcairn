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


def test_get_embedder_unknown_still_raises():
    with pytest.raises(ValueError):
        get_embedder("bogus")
