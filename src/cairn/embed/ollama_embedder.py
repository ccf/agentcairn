# SPDX-License-Identifier: Apache-2.0
"""Ollama embedding provider (local server, keyless). Talks to /api/embed over
stdlib HTTP — no extra dependency. `post` is injectable for tests; `dim` is probed
lazily so construction never hits the network."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable

# model-family prefix → (document_prefix, query_prefix). Unknown families: no prefix.
_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic": ("search_document: ", "search_query: "),
}


def _http_post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - fixed localhost/Ollama host
        url, data=data, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


class OllamaEmbedder:
    def __init__(
        self,
        model: str = "nomic-embed-text",
        host: str = "http://localhost:11434",
        post: Callable[[str, dict], dict] | None = None,
    ) -> None:
        self._model = model
        self._host = host.rstrip("/")
        self._post = post or _http_post
        self._dim: int | None = None

    @property
    def model_id(self) -> str:
        return f"ollama:{self._model}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("probe"))
        return self._dim

    def _prefixes(self) -> tuple[str, str]:
        for family, prefixes in _PREFIXES.items():
            if self._model.startswith(family):
                return prefixes
        return "", ""

    def _call(self, inputs: list[str]) -> list[list[float]]:
        url = f"{self._host}/api/embed"
        try:
            resp = self._post(url, {"model": self._model, "input": inputs})
            embeddings = resp["embeddings"]
        except Exception as e:  # noqa: BLE001 - wrap any transport/parse error actionably
            raise RuntimeError(
                f"Ollama embed failed at {self._host} (model {self._model!r}): {e}. "
                f"Is 'ollama serve' running and 'ollama pull {self._model}' done?"
            ) from e
        if not embeddings:
            raise RuntimeError(
                f"Ollama returned no embeddings at {self._host} (model {self._model!r}). "
                f"Is 'ollama pull {self._model}' done?"
            )
        return embeddings

    def embed(self, texts: list[str]) -> list[list[float]]:
        doc_prefix, _ = self._prefixes()
        return self._call([doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        _, query_prefix = self._prefixes()
        return self._call([query_prefix + text])[0]
