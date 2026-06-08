# SPDX-License-Identifier: Apache-2.0
"""The Embedder interface. Implementations turn text into fixed-dimension
float vectors. Keep this surface small and stable — the index and (Plan 3)
search both depend on it."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    @property
    def model_id(self) -> str: ...
    @property
    def dim(self) -> int: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...
