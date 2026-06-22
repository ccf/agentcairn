# Cloud Embedding Tier — Design

**Status:** approved 2026-06-21
**Goal:** Add an opt-in **cloud embedding tier** (Voyage + OpenAI) behind agentcairn's existing `cairn.embed` seam, for users who want **higher recall quality** than the local `fastembed`/`ollama` embedders. Voyage is the default cloud provider.

## Motivation & principle trade-off

Today agentcairn has only local embedders (`fastembed` ONNX default, `ollama` local server). The driver for cloud is **recall quality** — Voyage (`voyage-3`) and OpenAI (`text-embedding-3-large`) generally out-retrieve local `nomic-embed-text`. The cost is the local-first principle: a cloud embedder **sends note text to a third party**. This is acceptable, opt-in, and disclosed — consistent with the existing optional `CAIRN_JUDGE=anthropic` LLM tier (which also sends content off-machine). Crucially, because agentcairn **redacts before every write**, the vault text a cloud embedder sends is *already secret-scrubbed* — this is a content-privacy choice, not a secret-leak.

## Architecture

Two new `Embedder` implementations behind the existing `cairn.embed.get_embedder(name)` seam, **mirroring `OllamaEmbedder`** (`src/cairn/embed/ollama_embedder.py`): stdlib `urllib` HTTP (**no new dependency** — no `openai`/`voyageai` SDK), an **injectable `post` callable** (tests never hit the network), a **lazily-probed `dim`** (construction stays offline), `model_id`/`dim` properties, and `embed_query`/`embed_document` methods. Selected via `CAIRN_EMBEDDER=voyage|openai`.

A small shared helper module may hold the common bearer-auth POST + batch-chunking logic; the two providers are separate classes (like `fastembed`/`ollama` are).

## Components

### `src/cairn/config.py` — config resolution
- `voyage_config() -> (model: str, api_key: str | None)` — `CAIRN_EMBED_MODEL` or `"voyage-3"`; key from `VOYAGE_API_KEY`.
- `openai_config() -> (model: str, api_key: str | None, base_url: str)` — `CAIRN_EMBED_MODEL` or `"text-embedding-3-small"`; key from `OPENAI_API_KEY`; base URL `OPENAI_BASE_URL` or `https://api.openai.com/v1` (allows Azure/OpenAI-compatible endpoints).
- Mirror the existing `ollama_config()` shape.

### `src/cairn/embed/voyage_embedder.py` — `VoyageEmbedder` (default cloud)
- `__init__(model="voyage-3", api_key=None, post=None)` — no network at construction.
- `model_id` → `f"voyage:{model}"`.
- `dim` → lazily probed: `len(self.embed_query("probe"))`, cached.
- `embed_document(texts)` / `embed_query(texts|text)` → call with Voyage's asymmetric **`input_type`** (`"document"` / `"query"`) for better retrieval.
- `_call(inputs, input_type)` → `POST https://api.voyageai.com/v1/embeddings` with body `{model, input, input_type}` and header `Authorization: Bearer <key>`; parse `data[]` sorted by `index` → `embedding`. Batch inputs in chunks of ≤128.

### `src/cairn/embed/openai_embedder.py` — `OpenAIEmbedder`
- `__init__(model="text-embedding-3-small", api_key=None, base_url="https://api.openai.com/v1", post=None)`.
- `model_id` → `f"openai:{model}"`.
- `dim` → lazily probed + cached.
- Symmetric: `embed_query` and `embed_document` make the same call (no `input_type`).
- `_call(inputs)` → `POST {base_url}/embeddings` with `{model, input}` + `Authorization: Bearer <key>`; parse `data[]` sorted by `index` → `embedding`. Batch in chunks (large limit, e.g. ≤2048).

### `src/cairn/embed/__init__.py` — wiring
Extend `get_embedder(name)`:
```python
if name == "voyage":
    from cairn.config import voyage_config
    from cairn.embed.voyage_embedder import VoyageEmbedder
    return VoyageEmbedder(*voyage_config())
if name == "openai":
    from cairn.config import openai_config
    from cairn.embed.openai_embedder import OpenAIEmbedder
    return OpenAIEmbedder(*openai_config())
```
Update the `get_embedder` docstring + `__getattr__` lazy exports to match.

## Error handling — fail-closed

- **Missing API key:** the first network call raises a clear, actionable error naming the env var (`"set VOYAGE_API_KEY"` / `"set OPENAI_API_KEY"`). Construction never raises (offline), matching `OllamaEmbedder`.
- **Failures (429 / transport / parse):** retry a few times with backoff on 429/transient, then **raise** an actionable error. An embedding call **never returns zero or partial vectors** — a silent bad vector would corrupt the index/recall. Reindex/recall surfaces the error.
- **API key is never logged and never written to the vault.**
- Reasonable request timeout (≈60s, like `OllamaEmbedder`).

## Index interaction (documented, no code change)

The index stores `embedding_model` + `dim`. Switching to/from a cloud embedder changes `model_id`/`dim`, so the existing `reconcile` already **fully rebuilds** the index — i.e. re-embeds the entire vault through the cloud API. This is real cost/latency and is documented as the cost of switching embedder tiers. No change to `reconcile`.

## Privacy (opt-in, disclosed)

- Vault **documents** are already redacted at write → safe to embed.
- Recall **queries are sent un-redacted** (ephemeral; redacting would blunt recall).
- Docs state plainly: with the cloud tier on, your already-redacted note text **and** your queries reach the provider. This is opt-in (default stays local `fastembed`).

## Testing

All via the **injected `post`** — zero real API calls, no keys/network:
- **Request shape:** correct endpoint, `Authorization: Bearer` header present (value not asserted), `model`, Voyage `input_type` for query vs document, batch chunking (>chunk-size input → multiple posts, results concatenated in order).
- **Response parse:** `data[]` out of order → vectors returned in input order; `dim` probe returns the right length and is cached (one probe call).
- **Fail-closed:** missing key → actionable error on first call; a `post` that raises / returns a 429-shaped error → raised (never zeros); a partial/empty `data` → raised.
- **Wiring:** `get_embedder("voyage")` / `("openai")` returns the right type with env-resolved model; unknown name still raises.

## Docs

README + website embedder section: add `voyage`/`openai` to `CAIRN_EMBEDDER`, the default models + key env vars, the **privacy disclosure**, the **re-embed-on-switch cost**, and that this is the recall-quality tier (local stays default).

## Out of scope (follow-ups)

- Other providers (Cohere, Gemini embeddings).
- A benchmark run quantifying the recall lift vs `fastembed` (worth doing later to substantiate the "quality" claim, but not a blocker).
- `dimensions`-truncation (OpenAI) / output-dtype options — default full dim for v1.
- Caching/persisting embeddings beyond the existing index.

## Definition of done

- `CAIRN_EMBEDDER=voyage` (default model `voyage-3`) and `=openai` (default `text-embedding-3-small`) work behind `get_embedder`, exposing `model_id`/`dim`/`embed_query`/`embed_document`, with keys from `VOYAGE_API_KEY`/`OPENAI_API_KEY`.
- stdlib-only (no new dependency); construction offline; `post` injectable.
- Fail-closed: failures raise actionable errors, never zero vectors; key never logged/persisted.
- Voyage uses `input_type`; both batch + preserve input order.
- Switching tiers re-embeds the vault via existing `reconcile` (documented).
- Tests pass with no network; README/website document the tier + privacy + cost.
