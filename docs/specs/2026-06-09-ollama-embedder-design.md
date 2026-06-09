# Ollama embedding tier — Design Spec (v1.1)

**Date:** 2026-06-09
**Status:** approved (design); implementation to follow.

## 1. Goal & motivation

Add an **Ollama** embedding provider behind the existing `Embedder` Protocol, selectable via `CAIRN_EMBEDDER=ollama`. Motivated by the v1 benchmark: local `bge-small` **vector-only trails BM25** on LoCoMo (recall@5 0.483 vs 0.527), so a stronger *local* embedder is the clearest way to lift the vector and hybrid arms — keyless, zero-cost, and benchmarkable immediately. Second v1.1 sub-project; the `Embedder` interface admits a cloud provider as a later tier.

## 2. Decisions (from brainstorming)

- **Provider:** Ollama only this round (local, keyless). Cloud is a future tier via the same interface.
- **Config:** env-driven via `cairn.config` — `CAIRN_EMBEDDER=ollama` selects; `CAIRN_EMBED_MODEL` (default `nomic-embed-text`); `OLLAMA_HOST` (default `http://localhost:11434`). The single-name selector keeps working across CLI `--embedder ollama` and MCP `CAIRN_EMBEDDER=ollama`.
- **Transport:** stdlib `urllib.request` JSON POST — **no new dependency** (mirrors the LoCoMo downloader). No `ollama`/`httpx` package.
- **`dim`:** auto-probed lazily and cached; switching models changes `model_id`, so `reconcile` rebuilds the index on the mismatch (existing behavior).

## 3. Architecture

**`src/cairn/embed/ollama_embedder.py`** — `OllamaEmbedder` implementing `Embedder` (`model_id`, `dim`, `embed`, `embed_query`):

- `__init__(self, model: str = "nomic-embed-text", host: str = "http://localhost:11434", post: Callable | None = None)`:
  - Stores `model`, `host`, and a `post` callable (defaults to an internal `_http_post(url, payload) -> dict` using `urllib.request`). **No network at construction** — `post` is injectable so tests need no server.
  - `_dim: int | None = None` (lazy cache).
- `model_id` → `f"ollama:{model}"` (distinct from `bge-small-en-v1.5`, so a switch triggers `reconcile`'s model/dim-mismatch rebuild).
- `dim` (property) → if `_dim is None`, embed a single `"probe"` string, cache `len(vec)`. Network hit + fail-fast happen here.
- `embed(texts: list[str]) -> list[list[float]]` → POST `{"model": model, "input": [prefixed docs]}` to `{host}/api/embed`; return `response["embeddings"]`.
- `embed_query(text: str) -> list[float]` → POST the single query (with the query prefix); return the one vector.
- **Asymmetric prefixes** via a per-model map `_PREFIXES`: keys matched by model-family prefix (`nomic` → `("search_document: ", "search_query: ")`); unknown models → `("", "")` (symmetric, safe). Applied in `embed` (document prefix) and `embed_query` (query prefix).
- **Errors:** wrap `urllib` `URLError`/`HTTPError`/JSON/`KeyError` into a `RuntimeError` with an actionable message: `f"Ollama embed failed at {host} (model {model!r}): {detail}. Is 'ollama serve' running and 'ollama pull {model}' done?"`. An empty/malformed `embeddings` payload raises the same.

**`src/cairn/config.py`** — add:
- `ollama_config(env: Mapping[str, str] | None = None) -> tuple[str, str]` → `(CAIRN_EMBED_MODEL or "nomic-embed-text", OLLAMA_HOST or "http://localhost:11434")`.

**`src/cairn/embed/__init__.py`** — `get_embedder("ollama")`:
- Lazy-import `OllamaEmbedder`; construct with `OllamaEmbedder(*ollama_config())`. Keep `fake`/`fastembed` branches unchanged. Unknown name still raises `ValueError`.

The `/api/embed` request/response shape (current Ollama API): request `{"model": str, "input": str | list[str]}`; response `{"embeddings": [[float, ...], ...], ...}`. The implementer verifies against the installed Ollama if available; the unit tests pin the shape via the fake `post`.

## 4. Testing

- **`tests/embed/test_ollama.py`** (new), all offline via an injected fake `post`:
  - request shaping: `embed(["a","b"])` POSTs `{"model": "nomic-embed-text", "input": ["search_document: a", "search_document: b"]}` to `…/api/embed`; `embed_query("q")` uses `"search_query: q"`.
  - response parsing: fake returns `{"embeddings": [[...]]}` → `embed`/`embed_query` return the float lists.
  - prefixes: a non-nomic model (e.g. `mxbai-embed-large`) applies no prefix (input passed through unprefixed).
  - `dim`: lazy — not probed at construction; first `.dim` access calls `post` once (probe) and caches (second access does NOT call again).
  - `model_id == "ollama:nomic-embed-text"`.
  - errors: fake `post` raising → `RuntimeError` whose message names the host, model, and the `ollama serve`/`pull` hint; empty `{"embeddings": []}` → same `RuntimeError`.
- **`tests/embed/test_get_embedder.py`** (extend or new): `get_embedder("ollama")` with monkeypatched env (`CAIRN_EMBED_MODEL`, `OLLAMA_HOST`) returns an `OllamaEmbedder` with those values and performs **no network** (construction is lazy); `ollama_config` env resolution + defaults.
- **`tests/config` :** `ollama_config` defaults + env override (in `tests/test_config.py`).
- **Opt-in live integration test:** `@pytest.mark.skipif` unless `CAIRN_OLLAMA_LIVE=1` (and reachable) — embeds against a real local server, asserts `dim > 0` and stable vectors. Skipped in CI/default runs (mirrors the fastembed/reranker model-gated skips).
- Default `uv run pytest` and CI stay fully offline (no Ollama, no network).

## 5. Docs

- `README.md`: the v1.1 roadmap "cloud/Ollama embedding tier" bullet — note Ollama landed (`CAIRN_EMBEDDER=ollama`, `CAIRN_EMBED_MODEL`, `OLLAMA_HOST`); cloud still pending.
- `cairn recall`/CLI `--embedder` help + MCP `CAIRN_EMBEDDER` docs mention `ollama` as a value.

## 6. Non-goals (deferred)

- **No cloud provider** (OpenAI/Voyage/Cohere) — the next tier; this round designs the interface to admit it but builds only Ollama.
- **No model auto-pull** — the user runs `ollama pull <model>`; we give a clear error if it's missing.
- **No batching tuning** beyond a single `/api/embed` call per `embed`.
- **No prefixes beyond `nomic-*`** + symmetric passthrough; richer per-model prompt schemes are future work.
- **No change to `cairn.search.engine`** or the index schema — `dim`/`model_id` flow through the existing `reconcile`/`meta` rebuild-on-mismatch path.

## 7. Risks

- **Server/model not present:** default-on only if the user sets `CAIRN_EMBEDDER=ollama`; otherwise unaffected. Clear fail-fast error when selected but unavailable.
- **API shape drift:** Ollama's `/api/embed` is current; older deployments use `/api/embeddings` (single `prompt`). We target `/api/embed`; document the minimum Ollama version, and the error message surfaces a 404 clearly. (Supporting the legacy endpoint is out of scope.)
- **Quality unverified for *this* corpus:** the benchmark will tell us whether `nomic`/`mxbai` actually beat `bge-small` on agentcairn content — that's the point; this ships the capability to measure it.
