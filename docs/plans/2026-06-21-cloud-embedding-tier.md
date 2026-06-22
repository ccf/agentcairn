# Cloud Embedding Tier — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in cloud embedders (Voyage default, OpenAI) behind `cairn.embed.get_embedder`, for higher-recall-quality users. stdlib-only, fail-closed, keys env-only.

**Architecture:** A shared `_cloud.embed_request` helper does the bearer-auth POST + ordered parse + retry/fail-closed for both providers (both use the OpenAI-style `{data:[{index,embedding}]}` response). `VoyageEmbedder`/`OpenAIEmbedder` are thin classes over it, mirroring `OllamaEmbedder`. Config via `CAIRN_EMBEDDER=voyage|openai`.

**Tech Stack:** Python ≥3.12, stdlib `urllib` (no new deps), pytest.

## Global Constraints

- **No new runtime dependency** — stdlib `urllib` only (no `openai`/`voyageai` SDK).
- **Construction never hits the network** — `dim` is probed lazily (mirror `OllamaEmbedder`).
- **`post` is injectable** (signature `post(url, payload, headers) -> dict`) so tests never call a real API.
- **Fail-closed:** any failure (missing key, 429-after-retries, transport, bad shape) **raises** an actionable `RuntimeError` — never returns zero/partial vectors.
- **API key from env only, never logged, never written to the vault.**
- `Embedder` protocol (`src/cairn/embed/base.py`): `model_id` (prop), `dim` (prop), `embed(texts: list[str]) -> list[list[float]]`, `embed_query(text: str) -> list[float]`.
- Every commit leaves `uv run pytest -q` green + ruff clean.

---

## Task 1: config resolution (`voyage_config`, `openai_config`)

**Files:** Modify `src/cairn/config.py`; Test: `tests/test_config.py` (or the existing config test file — check where `test_ollama_config`/`fastembed_model` are tested and add there).

**Interfaces:** Produces `voyage_config(env=None) -> tuple[str, str | None]` (model, api_key) and `openai_config(env=None) -> tuple[str, str | None, str]` (model, api_key, base_url).

- [ ] **Step 1: Write the failing test:**
```python
from cairn.config import voyage_config, openai_config

def test_voyage_config_defaults_and_env():
    assert voyage_config({}) == ("voyage-3", None)
    assert voyage_config({"CAIRN_EMBED_MODEL": "voyage-3-large", "VOYAGE_API_KEY": "k"}) == ("voyage-3-large", "k")

def test_openai_config_defaults_and_env():
    assert openai_config({}) == ("text-embedding-3-small", None, "https://api.openai.com/v1")
    assert openai_config({"OPENAI_API_KEY": "k", "OPENAI_BASE_URL": "https://x/v1"}) == (
        "text-embedding-3-small", "k", "https://x/v1")
```
Run: `uv run pytest tests/test_config.py -q` → FAIL.

- [ ] **Step 2: Implement** — in `src/cairn/config.py`, near `ollama_config` (add module-level defaults next to the existing `_DEFAULT_OLLAMA_*`):
```python
_DEFAULT_VOYAGE_MODEL = "voyage-3"
_DEFAULT_OPENAI_MODEL = "text-embedding-3-small"
_DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


def voyage_config(env: Mapping[str, str] | None = None) -> tuple[str, str | None]:
    """Resolve (model, api_key) for the Voyage embedder.
    model ← CAIRN_EMBED_MODEL or 'voyage-3'; api_key ← VOYAGE_API_KEY."""
    if env is None:
        env = cairn_env()
    return env.get("CAIRN_EMBED_MODEL") or _DEFAULT_VOYAGE_MODEL, env.get("VOYAGE_API_KEY")


def openai_config(env: Mapping[str, str] | None = None) -> tuple[str, str | None, str]:
    """Resolve (model, api_key, base_url) for the OpenAI embedder.
    model ← CAIRN_EMBED_MODEL or 'text-embedding-3-small'; api_key ← OPENAI_API_KEY;
    base_url ← OPENAI_BASE_URL or api.openai.com (allows OpenAI-compatible endpoints)."""
    if env is None:
        env = cairn_env()
    model = env.get("CAIRN_EMBED_MODEL") or _DEFAULT_OPENAI_MODEL
    base = env.get("OPENAI_BASE_URL") or _DEFAULT_OPENAI_BASE_URL
    return model, env.get("OPENAI_API_KEY"), base
```

- [ ] **Step 3: Run** `uv run pytest tests/test_config.py -q` → PASS; then `uv run pytest -q`.
- [ ] **Step 4: Commit** `git add src/cairn/config.py tests/test_config.py && git commit -m "feat(config): voyage_config + openai_config resolution"`

---

## Task 2: shared cloud HTTP helper (`_cloud.embed_request`)

**Files:** Create `src/cairn/embed/_cloud.py`; Test: `tests/embed/test_cloud.py`.

**Interfaces:** Produces `embed_request(url, payload, api_key, *, label, post=None, retries=3) -> list[list[float]]` and `batched(seq, n)`. `post` signature: `Callable[[str, dict, dict], dict]` (url, payload, headers).

- [ ] **Step 1: Write the failing tests** — `tests/embed/test_cloud.py`:
```python
import pytest
from cairn.embed._cloud import batched, embed_request

URL = "https://api.example/v1/embeddings"


def fake_post_factory(captured):
    def post(url, payload, headers):
        captured.append((url, payload, headers))
        # return data OUT OF ORDER to prove we re-sort by index
        return {"data": [
            {"index": 1, "embedding": [0.2]},
            {"index": 0, "embedding": [0.1]},
        ]}
    return post


def test_batched():
    assert list(batched([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]


def test_bearer_header_and_ordered_parse():
    cap = []
    vecs = embed_request(URL, {"model": "m", "input": ["a", "b"]}, "secret",
                         label="X", post=fake_post_factory(cap))
    assert vecs == [[0.1], [0.2]]  # re-ordered by index
    _, _, headers = cap[0]
    assert headers["Authorization"] == "Bearer secret"
    assert headers["Content-Type"] == "application/json"


def test_missing_key_raises():
    with pytest.raises(RuntimeError, match="API key"):
        embed_request(URL, {"model": "m", "input": ["a"]}, None, label="X", post=lambda *a: {})


def test_empty_data_raises_never_zero():
    with pytest.raises(RuntimeError):
        embed_request(URL, {"model": "m", "input": ["a"]}, "k", label="X",
                      post=lambda *a: {"data": []})


def test_count_mismatch_raises():
    with pytest.raises(RuntimeError):
        embed_request(URL, {"model": "m", "input": ["a", "b"]}, "k", label="X",
                      post=lambda *a: {"data": [{"index": 0, "embedding": [0.1]}]})


def test_retries_then_raises():
    calls = {"n": 0}
    def flaky(url, payload, headers):
        calls["n"] += 1
        raise TimeoutError("boom")
    with pytest.raises(RuntimeError, match="failed"):
        embed_request(URL, {"model": "m", "input": ["a"]}, "k", label="X", post=flaky, retries=3)
    assert calls["n"] == 3  # retried up to the limit
```
Run: `uv run pytest tests/embed/test_cloud.py -q` → FAIL.

- [ ] **Step 2: Implement** `src/cairn/embed/_cloud.py`:
```python
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
```
(Add `tests/embed/__init__.py` only if pytest needs it for collection.)

- [ ] **Step 3: Run** `uv run pytest tests/embed/test_cloud.py -q` → PASS; then `uv run pytest -q`.
- [ ] **Step 4: Commit** `git add src/cairn/embed/_cloud.py tests/embed/test_cloud.py && git commit -m "feat(embed): shared cloud /embeddings HTTP helper (fail-closed)"`

---

## Task 3: `VoyageEmbedder` (default cloud) + wiring

**Files:** Create `src/cairn/embed/voyage_embedder.py`; Modify `src/cairn/embed/__init__.py`; Test: `tests/embed/test_voyage_embedder.py`.

**Interfaces:** Consumes `_cloud.embed_request`/`batched`; produces `VoyageEmbedder(model="voyage-3", api_key=None, post=None)` satisfying `Embedder`. `get_embedder("voyage")` returns it via `voyage_config()`.

- [ ] **Step 1: Write the failing tests** — `tests/embed/test_voyage_embedder.py`:
```python
from cairn.embed.voyage_embedder import VoyageEmbedder


def make(captured):
    def post(url, payload, headers):
        captured.append((url, payload, headers))
        return {"data": [{"index": i, "embedding": [float(i), 0.0]} for i in range(len(payload["input"]))]}
    return post


def test_model_id_and_input_types():
    cap = []
    emb = VoyageEmbedder(model="voyage-3", api_key="k", post=make(cap))
    emb.embed(["doc1", "doc2"])
    emb.embed_query("q")
    assert emb.model_id == "voyage:voyage-3"
    assert cap[0][1]["input_type"] == "document" and cap[0][0].endswith("/embeddings")
    assert cap[1][1]["input_type"] == "query"
    assert cap[0][2]["Authorization"] == "Bearer k"


def test_dim_probes_lazily_and_caches():
    calls = {"n": 0}
    def post(url, payload, headers):
        calls["n"] += 1
        return {"data": [{"index": i, "embedding": [0.0, 1.0, 2.0]} for i in range(len(payload["input"]))]}
    emb = VoyageEmbedder(api_key="k", post=post)
    assert emb.dim == 3
    assert emb.dim == 3  # cached
    assert calls["n"] == 1


def test_batches_over_128_in_input_order():
    cap = []
    emb = VoyageEmbedder(api_key="k", post=make(cap))
    out = emb.embed([f"d{i}" for i in range(130)])
    assert len(cap) == 2 and len(out) == 130  # 128 + 2
    assert out[0] == [0.0, 0.0] and out[129][0] == 1.0  # 2nd chunk's index-1
```
Run → FAIL.

- [ ] **Step 2: Implement** `src/cairn/embed/voyage_embedder.py`:
```python
# SPDX-License-Identifier: Apache-2.0
"""Voyage AI embedding provider (cloud, opt-in). OpenAI-style /embeddings response;
asymmetric input_type (document vs query). stdlib HTTP via the shared _cloud helper;
`post` injectable; `dim` probed lazily so construction never hits the network."""

from __future__ import annotations

from cairn.embed._cloud import PostFn, batched, embed_request

_URL = "https://api.voyageai.com/v1/embeddings"
_BATCH = 128


class VoyageEmbedder:
    def __init__(self, model: str = "voyage-3", api_key: str | None = None, post: PostFn | None = None) -> None:
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
            vecs.extend(embed_request(_URL, payload, self._api_key,
                                      label=f"Voyage({self._model})", post=self._post))
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return vecs

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._call(list(texts), "document")

    def embed_query(self, text: str) -> list[float]:
        return self._call([text], "query")[0]
```
Then wire into `src/cairn/embed/__init__.py` `get_embedder`:
```python
    if name == "voyage":
        from cairn.config import voyage_config
        from cairn.embed.voyage_embedder import VoyageEmbedder

        return VoyageEmbedder(*voyage_config())
```
and add a wiring test (monkeypatch `cairn.config.cairn_env` or set env so `voyage_config` resolves) asserting `get_embedder("voyage")` returns a `VoyageEmbedder` with `model_id == "voyage:voyage-3"`. Update the `get_embedder` docstring to mention `voyage`.

- [ ] **Step 3: Run** the voyage tests + `uv run pytest -q`.
- [ ] **Step 4: Commit** `git add src/cairn/embed/voyage_embedder.py src/cairn/embed/__init__.py tests/embed/test_voyage_embedder.py && git commit -m "feat(embed): VoyageEmbedder (default cloud tier) + get_embedder wiring"`

---

## Task 4: `OpenAIEmbedder` + wiring

**Files:** Create `src/cairn/embed/openai_embedder.py`; Modify `src/cairn/embed/__init__.py`; Test: `tests/embed/test_openai_embedder.py`.

**Interfaces:** `OpenAIEmbedder(model="text-embedding-3-small", api_key=None, base_url="https://api.openai.com/v1", post=None)`; `get_embedder("openai")` via `openai_config()`.

- [ ] **Step 1: Write the failing tests** — `tests/embed/test_openai_embedder.py`:
```python
from cairn.embed.openai_embedder import OpenAIEmbedder


def make(cap):
    def post(url, payload, headers):
        cap.append((url, payload, headers))
        return {"data": [{"index": i, "embedding": [float(i)]} for i in range(len(payload["input"]))]}
    return post


def test_symmetric_no_input_type_and_model_id():
    cap = []
    emb = OpenAIEmbedder(api_key="k", post=make(cap))
    emb.embed(["a"]); emb.embed_query("q")
    assert emb.model_id == "openai:text-embedding-3-small"
    assert "input_type" not in cap[0][1]                       # symmetric — no input_type
    assert cap[0][0] == "https://api.openai.com/v1/embeddings"
    assert cap[0][2]["Authorization"] == "Bearer k"


def test_custom_base_url():
    cap = []
    emb = OpenAIEmbedder(api_key="k", base_url="https://proxy/v1", post=make(cap))
    emb.embed(["a"])
    assert cap[0][0] == "https://proxy/v1/embeddings"


def test_dim_lazy_cached():
    calls = {"n": 0}
    def post(url, payload, headers):
        calls["n"] += 1
        return {"data": [{"index": 0, "embedding": [0.0, 1.0]}]}
    emb = OpenAIEmbedder(api_key="k", post=post)
    assert emb.dim == 2 and emb.dim == 2 and calls["n"] == 1
```
Run → FAIL.

- [ ] **Step 2: Implement** `src/cairn/embed/openai_embedder.py` (mirror Voyage; symmetric, no input_type, configurable base_url, batch 2048):
```python
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
            vecs.extend(embed_request(self._url, payload, self._api_key,
                                      label=f"OpenAI({self._model})", post=self._post))
        if self._dim is None and vecs:
            self._dim = len(vecs[0])
        return vecs

    def embed(self, texts: list[str]) -> list[list[float]]:
        return self._call(list(texts))

    def embed_query(self, text: str) -> list[float]:
        return self._call([text])[0]
```
Wire into `get_embedder`:
```python
    if name == "openai":
        from cairn.config import openai_config
        from cairn.embed.openai_embedder import OpenAIEmbedder

        return OpenAIEmbedder(*openai_config())
```
Add the wiring test + update the `get_embedder` docstring + `__getattr__` lazy exports (if other embedders are exported there) to include `VoyageEmbedder`/`OpenAIEmbedder`.

- [ ] **Step 3: Run** the openai tests + `uv run pytest -q`.
- [ ] **Step 4: Commit** `git add src/cairn/embed/openai_embedder.py src/cairn/embed/__init__.py tests/embed/test_openai_embedder.py && git commit -m "feat(embed): OpenAIEmbedder cloud tier + get_embedder wiring"`

---

## Task 5: docs (README + website)

**Files:** Modify `README.md`, `website/src/lib/content.ts` (+ the relevant page if embedders are documented there).

- [ ] **Step 1:** Find where embedders / `CAIRN_EMBEDDER` are documented (grep `CAIRN_EMBEDDER`, `fastembed`, `ollama`, `embedder` in `README.md` + `website/src/`). Add `voyage`/`openai` to the options with: default models (`voyage-3`, `text-embedding-3-small`), key env vars (`VOYAGE_API_KEY`/`OPENAI_API_KEY`), that this is the **recall-quality** tier (local stays default), the **privacy disclosure** (with the cloud tier on, your already-redacted note text + your queries reach the provider — opt-in, like `CAIRN_JUDGE=anthropic`), and the **cost note** (switching embedder tiers re-embeds the whole vault via the API). Match existing tone.
- [ ] **Step 2:** `cd website && npm run build` passes (if website changed); `uv run pytest -q` green.
- [ ] **Step 3: Commit** `git add README.md website/ && git commit -m "docs: document the opt-in cloud embedding tier (voyage/openai) + privacy/cost"`

---

## Self-Review

**Spec coverage:** config (T1); shared fail-closed HTTP core (T2); VoyageEmbedder + input_type + default + wiring (T3); OpenAIEmbedder + symmetric + base_url + wiring (T4); docs incl. privacy disclosure + re-embed cost (T5). stdlib-only, injectable post, lazy dim, fail-closed (raise never zeros), keys env-only — all enforced in T2/T3/T4. `reconcile` rebuild-on-switch needs no code (documented in T5).

**Placeholder scan:** every code step is complete. The only "find where it's documented" instruction (T5 Step 1) is a real grep task with an explicit content list, not a vague placeholder.

**Type consistency:** `embed_request(url, payload, api_key, *, label, post, retries)` and `batched` (T2) are called identically in T3/T4; `post` is the 3-arg `(url, payload, headers)` form throughout (helper + both embedders + all tests); `Embedder` methods (`model_id`/`dim`/`embed`/`embed_query`) match `base.py`; `voyage_config()`/`openai_config()` (T1) feed `get_embedder` (T3/T4).
