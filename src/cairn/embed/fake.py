# SPDX-License-Identifier: Apache-2.0
"""Deterministic, dependency-free embedder for fast offline tests. NOT for
real retrieval — vectors are hash-derived, not semantic."""

from __future__ import annotations

import hashlib
import math


class FakeEmbedder:
    def __init__(self, dim: int = 8) -> None:
        self._dim = dim

    @property
    def model_id(self) -> str:
        return f"fake-{self._dim}"

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def _vec(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        raw = [digest[i % len(digest)] / 255.0 for i in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in raw)) or 1.0
        return [v / norm for v in raw]
