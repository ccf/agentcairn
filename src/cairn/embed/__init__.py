# SPDX-License-Identifier: Apache-2.0
from cairn.embed.base import Embedder
from cairn.embed.fake import FakeEmbedder


def get_embedder(name: str = "fastembed") -> Embedder:
    """Return an Embedder by name. 'fake' for tests; 'fastembed' (default) for real use."""
    if name == "fake":
        return FakeEmbedder()
    if name == "fastembed":
        from cairn.embed.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder()
    raise ValueError(f"unknown embedder: {name!r}")


def __getattr__(name: str) -> object:
    if name == "FastEmbedEmbedder":
        from cairn.embed.fastembed_embedder import FastEmbedEmbedder

        return FastEmbedEmbedder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Embedder", "FakeEmbedder", "FastEmbedEmbedder", "get_embedder"]
