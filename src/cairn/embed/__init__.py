# SPDX-License-Identifier: Apache-2.0
from cairn.embed.base import Embedder
from cairn.embed.fake import FakeEmbedder


def get_embedder(name: str = "fastembed") -> Embedder:
    """Return an Embedder by name. 'fake' for tests; 'fastembed' (default) for real use
    (model ← CAIRN_EMBED_MODEL or nomic-embed-text-v1.5); 'ollama' for a local Ollama server
    (CAIRN_EMBED_MODEL/OLLAMA_HOST); 'voyage' for Voyage AI cloud embeddings
    (CAIRN_EMBED_MODEL/VOYAGE_API_KEY)."""
    if name == "fake":
        return FakeEmbedder()
    if name == "fastembed":
        from cairn.config import fastembed_model
        from cairn.embed.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder(model_name=fastembed_model())
    if name == "ollama":
        from cairn.config import ollama_config
        from cairn.embed.ollama_embedder import OllamaEmbedder

        return OllamaEmbedder(*ollama_config())
    if name == "voyage":
        from cairn.config import voyage_config
        from cairn.embed.voyage_embedder import VoyageEmbedder

        return VoyageEmbedder(*voyage_config())
    raise ValueError(f"unknown embedder: {name!r}")


def __getattr__(name: str) -> object:
    if name == "FastEmbedEmbedder":
        from cairn.embed.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder
    if name == "OllamaEmbedder":
        from cairn.embed.ollama_embedder import OllamaEmbedder

        return OllamaEmbedder
    if name == "VoyageEmbedder":
        from cairn.embed.voyage_embedder import VoyageEmbedder

        return VoyageEmbedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Embedder", "FakeEmbedder", "get_embedder"]
