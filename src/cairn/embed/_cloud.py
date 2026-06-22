# SPDX-License-Identifier: Apache-2.0
"""Shared HTTP for OpenAI-style /embeddings endpoints (Voyage + OpenAI both return
{"data": [{"index", "embedding"}]}). stdlib only; `post` is injectable for tests.
Fail-closed: any failure raises actionably — never returns zero/partial vectors."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence

PostFn = Callable[[str, dict, dict], dict]


def _http_post(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 - fixed provider URL
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read())


def batched(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _retryable(e: Exception) -> bool:
    if isinstance(e, urllib.error.HTTPError):
        return e.code in (429, 500, 502, 503, 504)
    return isinstance(e, (urllib.error.URLError, TimeoutError))


def embed_request(
    url: str,
    payload: dict,
    api_key: str | None,
    *,
    label: str,
    post: PostFn | None = None,
    retries: int = 3,
) -> list[list[float]]:
    """Bearer POST to an OpenAI-style embeddings endpoint; return vectors in INPUT order.
    Retries on 429/transient with backoff; raises an actionable RuntimeError otherwise."""
    if not api_key:
        raise RuntimeError(f"{label}: missing API key — set the provider's API key env var.")
    p = post or _http_post
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = p(url, payload, headers)
            data = resp.get("data") or []
            if not data:
                raise RuntimeError(f"{label}: no embeddings in response")
            vecs = [d["embedding"] for d in sorted(data, key=lambda d: d.get("index", 0))]
            n_in = len(payload.get("input", []))
            if len(vecs) != n_in:
                raise RuntimeError(f"{label}: embedding count mismatch ({len(vecs)} != {n_in})")
            return vecs
        except Exception as e:  # noqa: BLE001 - wrap any transport/parse error actionably
            last = e
            if _retryable(e) and attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise RuntimeError(f"{label}: embedding request failed: {e}") from e
    raise RuntimeError(f"{label}: embedding request failed: {last}")
