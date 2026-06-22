# SPDX-License-Identifier: Apache-2.0
"""Voyage AI embedding provider (cloud, opt-in). OpenAI-style /embeddings response;
asymmetric input_type (document vs query). stdlib HTTP via the shared _cloud helper;
`post` injectable; `dim` probed lazily so construction never hits the network."""

from __future__ import annotations

from cairn.embed._cloud import PostFn, batched, embed_request

_URL = "https://api.voyageai.com/v1/embeddings"
_BATCH = 128


class VoyageEmbedder:
    def __init__(
        self, model: str = "voyage-3", api_key: str | None = None, post: PostFn | None = None
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._post = post
        self._dim: int | None = None

    @property
    def model_id(self) -> str:
        return f"voyage:{self._model}"

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self.embed_query("probe"))
        return self._dim

    def _call(self, inputs: list[str], input_type: str) -> list[list[float]]:
        vecs: list[list[float]] = []
        for chunk in batched(inputs, _BATCH):
            payload = {"model": self._model, "input": list(chunk), "input_type": input_type}
            vecs.extend(
                embed_request(
                    _URL, payload, self._api_key, label=f"Voyage({self._model})", post=self._post
                )
            )
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return vecs

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._call(list(texts), "document")

    def embed_query(self, text: str) -> list[float]:
        return self._call([text], "query")[0]
