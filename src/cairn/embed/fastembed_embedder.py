# SPDX-License-Identifier: Apache-2.0
"""FastEmbed (ONNX) embedder — the real default. Derives `dim` from the model
at init (no hardcoded width) and exposes an asymmetric query path when the
backend supports one."""

from __future__ import annotations


class FastEmbedEmbedder:
    def __init__(self, model_name: str = "nomic-ai/nomic-embed-text-v1.5") -> None:
        from fastembed import TextEmbedding

        from cairn.paths import models_root

        self._name = model_name
        # Pin the cache to a persistent dir; fastembed's default is the OS temp
        # dir, which macOS purges out from under us (see paths.models_root).
        cache_dir = models_root()
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        # Probe one embedding to learn the dimension rather than hardcoding it.
        self._dim = len(next(iter(self._model.embed(["probe"]))).tolist())

    @property
    def model_id(self) -> str:
        return self._name

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> list[float]:
        query_embed = getattr(self._model, "query_embed", None)
        if query_embed is not None:
            return list(query_embed([text]))[0].tolist()
        return self.embed([text])[0]
