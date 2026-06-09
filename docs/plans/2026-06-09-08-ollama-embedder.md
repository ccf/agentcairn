# Ollama Embedding Tier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `OllamaEmbedder` behind the existing `Embedder` Protocol, selectable via `CAIRN_EMBEDDER=ollama`, per `docs/specs/2026-06-09-ollama-embedder-design.md`.

**Architecture:** A new `OllamaEmbedder` talks to a local Ollama server over stdlib HTTP (no new dependency), with an **injectable `post`** callable (so tests need no server) and a **lazy `dim` probe**. `get_embedder("ollama")` constructs it from env (`CAIRN_EMBED_MODEL`/`OLLAMA_HOST`, resolved in `cairn.config`). `model_id` is `ollama:<model>`, so switching embedders triggers `reconcile`'s existing model-mismatch rebuild — the index schema and search engine are untouched.

**Tech Stack:** Python 3.12, stdlib `urllib`/`json`. No new dependencies.

---

## Conventions
- Run with `uv` from `/Users/ccf/git/agentcairn`. Branch `feat/v1.1-ollama-embedder` (already created; never `main`).
- SPDX header on new files; `from __future__ import annotations`; ruff `E,F,I,UP,B` (B008 ignored), line-length 100. Keep `uv run ruff check .` + `uv run pre-commit run --all-files` green. Pre-commit ruff pinned `v0.15.16` (= CI).
- Baseline: core 183 passed, 2 skipped.

## Existing API (do NOT change)
- `cairn.embed.base.Embedder` Protocol: `model_id: str`, `dim: int`, `embed(list[str]) -> list[list[float]]`, `embed_query(str) -> list[float]`.
- `cairn.embed.get_embedder(name)` dispatches `"fake"`/`"fastembed"` (lazy import), else `ValueError`.
- `cairn.config` already has `parse_bool` + `resolve_rerank`.
- `reconcile`/`meta` rebuild the index when `model_id`/`dim` change — no schema work needed here.

## File structure
```
src/cairn/config.py                 # Task 1: ollama_config()
tests/test_config.py                # Task 1
src/cairn/embed/ollama_embedder.py  # Task 2 (new)
tests/embed/__init__.py             # Task 2 (new package, if absent)
tests/embed/test_ollama.py          # Task 2
src/cairn/embed/__init__.py         # Task 3: get_embedder("ollama")
tests/embed/test_get_embedder.py    # Task 3
src/cairn/cli.py                    # Task 3: --embedder help mentions ollama
src/cairn/mcp/server.py             # Task 3: CAIRN_EMBEDDER docstring mentions ollama
README.md                           # Task 3: roadmap note
```

---

### Task 1: `ollama_config` in `cairn.config`

**Files:**
- Modify: `src/cairn/config.py`
- Test: `tests/test_config.py`

**Context:** Resolve the Ollama model + host from env, with defaults. Lives next to `parse_bool`/`resolve_rerank`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_config.py
from cairn.config import ollama_config


def test_ollama_config_defaults():
    assert ollama_config(env={}) == ("nomic-embed-text", "http://localhost:11434")


def test_ollama_config_env_override():
    env = {"CAIRN_EMBED_MODEL": "mxbai-embed-large", "OLLAMA_HOST": "http://box:11434"}
    assert ollama_config(env=env) == ("mxbai-embed-large", "http://box:11434")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_config.py -k ollama -v`
Expected: FAIL — `cannot import name 'ollama_config'`.

- [ ] **Step 3: Implement in `src/cairn/config.py`**

Add (the module already has `from __future__ import annotations`, `import os`, `from collections.abc import Mapping`):
```python
_DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
_DEFAULT_OLLAMA_HOST = "http://localhost:11434"


def ollama_config(env: Mapping[str, str] | None = None) -> tuple[str, str]:
    """Resolve (model, host) for the Ollama embedder from env, with defaults.
    model ← CAIRN_EMBED_MODEL or 'nomic-embed-text'; host ← OLLAMA_HOST or localhost."""
    if env is None:
        env = os.environ
    model = env.get("CAIRN_EMBED_MODEL") or _DEFAULT_OLLAMA_MODEL
    host = env.get("OLLAMA_HOST") or _DEFAULT_OLLAMA_HOST
    return model, host
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_config.py -v`
Expected: PASS (ollama cases + existing config tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/config.py tests/test_config.py
git commit -m "feat(config): ollama_config (CAIRN_EMBED_MODEL/OLLAMA_HOST)"
```

---

### Task 2: `OllamaEmbedder`

**Files:**
- Create: `src/cairn/embed/ollama_embedder.py`
- Create: `tests/embed/__init__.py` (only if `tests/embed/` does not already exist)
- Test: `tests/embed/test_ollama.py`

**Context:** Implements the `Embedder` Protocol over Ollama's `/api/embed`. Network goes through an injectable `post(url, payload) -> dict` (default = stdlib urllib). `dim` is probed lazily and cached. Asymmetric prefixes per model family (`nomic-*`); unknown models pass through unprefixed. All errors wrap into an actionable `RuntimeError`.

- [ ] **Step 1: Write the failing test**

```python
# tests/embed/__init__.py
# SPDX-License-Identifier: Apache-2.0
```

```python
# tests/embed/test_ollama.py
# SPDX-License-Identifier: Apache-2.0
import pytest

from cairn.embed.ollama_embedder import OllamaEmbedder


class FakePost:
    """Records calls and returns canned embeddings (one 3-d vec per input)."""

    def __init__(self, vec=(0.1, 0.2, 0.3), raises=None, embeddings=None):
        self.calls = []
        self._vec = list(vec)
        self._raises = raises
        self._embeddings = embeddings

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        if self._raises is not None:
            raise self._raises
        if self._embeddings is not None:
            return {"embeddings": self._embeddings}
        return {"embeddings": [list(self._vec) for _ in payload["input"]]}


def test_embed_request_shape_and_doc_prefix():
    post = FakePost()
    emb = OllamaEmbedder(model="nomic-embed-text", host="http://h:11434", post=post)
    out = emb.embed(["a", "b"])
    assert out == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    url, payload = post.calls[-1]
    assert url == "http://h:11434/api/embed"
    assert payload == {"model": "nomic-embed-text", "input": ["search_document: a", "search_document: b"]}


def test_embed_query_uses_query_prefix():
    post = FakePost()
    emb = OllamaEmbedder(model="nomic-embed-text", host="http://h:11434", post=post)
    v = emb.embed_query("q")
    assert v == [0.1, 0.2, 0.3]
    assert post.calls[-1][1]["input"] == ["search_query: q"]


def test_non_nomic_model_no_prefix():
    post = FakePost()
    emb = OllamaEmbedder(model="mxbai-embed-large", post=post)
    emb.embed(["x"])
    assert post.calls[-1][1]["input"] == ["x"]


def test_dim_is_lazy_and_cached():
    post = FakePost()
    emb = OllamaEmbedder(post=post)
    assert post.calls == []          # construction does NOT hit the server
    assert emb.dim == 3              # first access probes once
    assert len(post.calls) == 1
    assert emb.dim == 3              # cached: no further calls
    assert len(post.calls) == 1


def test_model_id():
    assert OllamaEmbedder(model="nomic-embed-text", post=FakePost()).model_id == "ollama:nomic-embed-text"


def test_error_wraps_actionably():
    post = FakePost(raises=ConnectionError("refused"))
    emb = OllamaEmbedder(model="nomic-embed-text", host="http://h:11434", post=post)
    with pytest.raises(RuntimeError) as ei:
        emb.embed_query("q")
    msg = str(ei.value)
    assert "http://h:11434" in msg and "nomic-embed-text" in msg
    assert "ollama serve" in msg and "pull" in msg


def test_empty_embeddings_raises():
    emb = OllamaEmbedder(post=FakePost(embeddings=[]))
    with pytest.raises(RuntimeError):
        emb.embed(["x"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/embed/test_ollama.py -v`
Expected: FAIL — `No module named 'cairn.embed.ollama_embedder'`.

- [ ] **Step 3: Implement `src/cairn/embed/ollama_embedder.py`**

```python
# src/cairn/embed/ollama_embedder.py
# SPDX-License-Identifier: Apache-2.0
"""Ollama embedding provider (local server, keyless). Talks to /api/embed over
stdlib HTTP — no extra dependency. `post` is injectable for tests; `dim` is probed
lazily so construction never hits the network."""

from __future__ import annotations

import json
import urllib.request
from typing import Callable

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/embed/test_ollama.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/embed/ollama_embedder.py tests/embed/
git commit -m "feat(embed): OllamaEmbedder (stdlib HTTP, lazy dim, asymmetric prefixes)"
```

---

### Task 3: Wire `get_embedder("ollama")` + docs

**Files:**
- Modify: `src/cairn/embed/__init__.py`
- Test: `tests/embed/test_get_embedder.py`
- Modify: `src/cairn/cli.py` (the `--embedder` help text), `src/cairn/mcp/server.py` (docstring), `README.md` (roadmap)

**Context:** `get_embedder("ollama")` constructs `OllamaEmbedder` from `ollama_config()` (env), lazily (no network at selection). The CLI/MCP single-name selector then accepts `ollama`.

- [ ] **Step 1: Write the failing test**

```python
# tests/embed/test_get_embedder.py
# SPDX-License-Identifier: Apache-2.0
from cairn.embed import get_embedder
from cairn.embed.ollama_embedder import OllamaEmbedder


def test_get_embedder_ollama_from_env(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBED_MODEL", "mxbai-embed-large")
    monkeypatch.setenv("OLLAMA_HOST", "http://box:11434")
    emb = get_embedder("ollama")
    assert isinstance(emb, OllamaEmbedder)
    assert emb.model_id == "ollama:mxbai-embed-large"
    assert emb._host == "http://box:11434"  # constructed, no network performed


def test_get_embedder_ollama_defaults(monkeypatch):
    monkeypatch.delenv("CAIRN_EMBED_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    emb = get_embedder("ollama")
    assert emb.model_id == "ollama:nomic-embed-text"


def test_get_embedder_unknown_still_raises():
    import pytest

    with pytest.raises(ValueError):
        get_embedder("bogus")
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/embed/test_get_embedder.py -v`
Expected: FAIL — `get_embedder("ollama")` raises `ValueError` (branch not added yet).

- [ ] **Step 3: Add the `ollama` branch to `src/cairn/embed/__init__.py`**

In `get_embedder`, before the final `raise ValueError(...)`:
```python
    if name == "ollama":
        from cairn.config import ollama_config
        from cairn.embed.ollama_embedder import OllamaEmbedder

        return OllamaEmbedder(*ollama_config())
```
Also add `OllamaEmbedder` to the lazy `__getattr__`/`__all__` if the module exposes embedders that way (mirror how `FastEmbedEmbedder` is exposed): add an `__getattr__` branch returning `OllamaEmbedder` and include `"OllamaEmbedder"` in `__all__`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/embed/ -v`
Expected: PASS (ollama embedder + get_embedder tests).

- [ ] **Step 5: Docs — mention `ollama` as a value**

- `src/cairn/cli.py`: the `recall`/`reindex` `--embedder` help strings — append `"; 'ollama' (CAIRN_EMBED_MODEL/OLLAMA_HOST)"`.
- `src/cairn/mcp/server.py`: in the `resolve_config` docstring line for embedder, note `ollama` is a valid value (model/host via `CAIRN_EMBED_MODEL`/`OLLAMA_HOST`).
- `README.md`: in the v1.1 roadmap, update the embedding-tier bullet to: `- **Ollama embedding tier** — ✅ local models via \`CAIRN_EMBEDDER=ollama\` (\`CAIRN_EMBED_MODEL\`/\`OLLAMA_HOST\`); cloud (OpenAI/Voyage) still pending.`

- [ ] **Step 6: Full suite + pre-commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q && uv run pytest benchmarks/tests/ -q && uv run ruff check . && uv run pre-commit run --all-files`
Expected: core ~189 passed / 2 skipped; benchmark 29 passed; ruff clean; pre-commit green.

- [ ] **Step 7: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/embed/__init__.py tests/embed/test_get_embedder.py src/cairn/cli.py src/cairn/mcp/server.py README.md
git commit -m "feat(embed): get_embedder('ollama') + docs"
```

---

## Self-Review Notes (for the controller)
- **Spec coverage:** §2 decisions (Ollama-only, env config, stdlib transport, lazy dim) → Tasks 1–2; §3 architecture (OllamaEmbedder, ollama_config, get_embedder wiring, model_id triggers reconcile) → Tasks 1–3; §4 testing → Tasks 1–3 tests (offline, injected post); §5 docs → Task 3 Step 5; §6 non-goals respected (no cloud, no auto-pull, no schema/engine change). The opt-in live integration test (§4) is OPTIONAL — implementer may add a `@pytest.mark.skipif(not os.environ.get("CAIRN_OLLAMA_LIVE"))` test, but the offline suite is the gate.
- **Type consistency:** `OllamaEmbedder(model, host, post)`, `model_id == "ollama:<model>"`, `dim` lazy int, `embed(list[str])->list[list[float]]`, `embed_query(str)->list[float]`, `ollama_config(env)->(model,host)` — consistent across tasks and matching the `Embedder` Protocol.
- **Latitude:** if the installed Ollama uses a different `/api/embed` response key, adapt `_call`'s parse + the fake in the test together — keep the injected-`post` offline design. Never add a hard dependency; never change `cairn.search.engine` or the index schema.
