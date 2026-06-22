# SPDX-License-Identifier: Apache-2.0
"""OpenAI embedding provider (cloud, opt-in). Symmetric (query == document call).
stdlib HTTP via the shared _cloud helper; `post` injectable; `dim` probed lazily."""

from __future__ import annotations

from cairn.embed._cloud import PostFn, batched, embed_request

_BATCH = 2048


class OpenAIEmbedder:
    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
        post: PostFn | None = None,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._post = post
        self._dim: int | None = None

    @property
    def model_id(self) -> str:
        return f"openai:{self._model}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("probe"))
        return self._dim

    def _call(self, inputs: list[str]) -> list[list[float]]:
        vecs: list[list[float]] = []
        for chunk in batched(inputs, _BATCH):
            payload = {"model": self._model, "input": list(chunk)}
            vecs.extend(
                embed_request(
                    self._url,
                    payload,
                    self._api_key,
                    label=f"OpenAI({self._model})",
                    post=self._post,
                )
            )
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return vecs

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._call(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._call([text])[0]
