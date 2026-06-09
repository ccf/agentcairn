# agentcairn benchmark harness — Design Spec

**Date:** 2026-06-09
**Status:** approved (design); implementation plan to follow.

## 1. Goal & non-goals

**Goal:** A reproducible benchmark harness that validates agentcairn's retrieval (and, optionally, end-to-end QA) quality on **LongMemEval-S** and **LoCoMo**, sweeping an ablation matrix over v1 retrieval components — turning "competitive with agentmemory/Mem0" from a claim into measured ranges. Realizes spec §13 of the main design (`docs/specs/2026-06-08-agentcairn-design.md`).

**Non-goals (this spec):**
- Not a runtime/product feature — it is a **dev/research tool**, never shipped in the `agentcairn` wheel.
- No new retrieval algorithms; we measure what v1 already has (plus one small enabling toggle, §4).
- No cloud/Ollama embedding-tier axis yet (deferred to v1.1; the config interface accommodates it).
- No leaderboard claims: our QA numbers use a non-canonical judge and are explicitly **not** comparable to published LongMemEval/LoCoMo numbers (§8).

## 2. Foundations (approved decisions)

- **(C) Two layers.** A deterministic, no-LLM, CI-able **retrieval-metrics** layer (the primary gate), plus an **opt-in QA-accuracy** layer behind an optional dependency group.
- **(A) Acquisition.** A version-pinned downloader into a **gitignored cache**; a small **committed synthetic fixture** drives offline CI. Real datasets are never vendored and are fetched manually/nightly.
- **(B) Ablation matrix** over v1 components: `bm25-only` · `vector-only` · `hybrid-rrf` (no boost) · `hybrid+graph-boost` · `hybrid+reranker`.
- **(A) QA layer** uses Anthropic for both generation and judging, behind a thin provider seam.

## 3. Architecture & layout

A **top-level `benchmarks/` directory that imports `cairn`**, NOT a shipped `cairn.bench` module. Rationale:
1. **License containment.** LoCoMo is **CC BY-NC 4.0**; any code that downloads/handles it must stay out of the Apache-2.0 wheel (`[tool.hatch.build.targets.wheel] packages = ["src/cairn"]`). `benchmarks/` is never packaged.
2. **Dependency hygiene.** Retrieval metrics need nothing extra; the QA layer needs the Anthropic SDK + `huggingface_hub`. These go in a new optional `bench` dependency-group, keeping the runtime install lean.
3. **Needs engine internals.** The matrix calls `cairn.search.engine` symbols (`vector_search`, the new `graph_boost` toggle) directly — cleaner from a sibling tool than from a shipped module restricted to the stable surface.

```
benchmarks/
  manifest.toml                  # dataset source URLs + pinned revisions + SHA256
  fixtures/synthetic/            # the ONLY committed data (LongMemEval- & LoCoMo-shaped)
  cairn_bench/
    __init__.py
    download.py                  # HF + GitHub-raw fetch into ~/.cache/agentcairn/bench, SHA-verify
    adapters/
      longmemeval.py             # instance -> Note(permalink=session_id, ## turn headers)
      locomo.py                  # sample -> Notes; dia_id normalization
    vaultize.py                  # adapter Notes -> markdown via cairn.vault.write_note
    build.py                     # reconcile() a scoped index per instance/conversation
    config.py                    # ArmConfig (the 5 ablation arms) + run config
    retrieval_metrics.py         # recall@k / nDCG@k / MRR (turn + session), macro-avg
    ablation.py                  # the 5-arm matrix runner
    qa/
      generate.py                # Anthropic reader over get_chunks()/get_note()
      judge.py                   # per-type judge prompts, cached, temp=0
      provider.py                # thin LLM seam (Anthropic default)
    report.py                    # mean±std, per-category, Wilson CIs, labeled columns
  tests/
    test_synthetic.py            # exact recall@k on the fixture (FakeEmbedder, offline)
    test_locomo_denominator.py   # adversarial excluded from num AND denom (Zep bug)
```

- Add `bench = ["anthropic>=0.40", "huggingface-hub>=0.25"]` (and any pure-python metric helpers) under `[dependency-groups]`. Retrieval-metric code imports only stdlib + `cairn`.
- `pyproject.toml` `[tool.pytest.ini_options] testpaths` currently is `["tests"]`; the bench tests live under `benchmarks/tests/` and run via an explicit path (kept out of the default `cairn` suite to avoid pulling bench deps into the core test run). CI adds a separate offline job for them.

## 4. Enabling change to `cairn.search` (plan task 1)

The matrix requires "hybrid **without** graph-boost," but the ×1.2 boost is currently unconditional in `_hybrid_sql`. **Add a `graph_boost: bool = True` parameter** threaded through `search()` and `hybrid_search()` (and `_hybrid_sql` builds the boost CASE only when true). Default `True` preserves all existing behavior and tests. This is a ~10-line, separately-tested change to `src/cairn/search/engine.py` (+ a test asserting boost-on vs boost-off differ on a linked corpus), useful beyond the benchmark. The five arms then map to public calls:

| Arm | Call |
|---|---|
| `bm25-only` | `search(con, q, embedder=None, ...)` |
| `vector-only` | `vector_search(con, qvec, dim=..., pool=...)` (+ light wrapper to shape as Hits) |
| `hybrid-rrf` | `search(con, q, embedder=E, graph_boost=False, ...)` |
| `hybrid+graph-boost` | `search(con, q, embedder=E, graph_boost=True, ...)` |
| `hybrid+reranker` | `search(con, q, embedder=E, rerank=True, ...)` |

## 5. Corpus → vault mapping

The vault is markdown (frontmatter + body). Retrieval returns **chunks** (`Hit(chunk_id, permalink, heading_path, snippet, score)`); chunks are header-section windows (`index/chunk.py`, `max_chars=1500`) carrying `note_permalink`. We exploit authored structure so gold IDs land in `permalink`/`heading_path` — no fuzzy matching.

**Mapping: one note per session, one chunk per turn** (each turn is its own `##` ATX header → ~one chunk/turn; a turn >1500 chars splits into multiple chunks sharing `note_permalink`+`heading_path`, handled in gold matching).

### LongMemEval-S (`longmemeval_s_cleaned.json`, JSON array of 500 instances)
Per instance, build a **scoped vault** from that instance's haystack:
- One note per session, `permalink = haystack_session_ids[i]`. Frontmatter: `permalink`, `title`, `type: session`, `session_date = haystack_dates[i]`, `instance_id = question_id`.
- Body: `## {session_id}_{turn_idx+1}  ({role}, {date})` per turn; body = turn `content`. Turn ids are **positional, 1-based** (not stored).
- Query = bare `instance["question"]` (no query-time temporal injection; bi-temporal is deferred). Record `question_date`/`session_date` only for the QA judge's temporal tolerance.
- Fresh `.duckdb` per instance (~40–50 short sessions → brute-force cosine is cheap). `_abs` instances dropped from the retrieval pass.

### LoCoMo (`locomo10.json`, JSON array of 10 conversations)
Per `sample`:
- One note per session. Sessions are sibling keys `session_{N}` (turn list) with separate `session_{N}_date_time`; iterate `^session_\d+$`, exclude `_date_time`. `permalink = f"{sample_id}_session_{N}"`, frontmatter `session_date` verbatim.
- Body: `## {dia_id}  ({speaker})` per turn (native `dia_id` = `D{session}:{turn}`); body = `text` (+ `blip_caption` if present — images aren't released).
- Query = each `qa["question"]`. Category 5 (adversarial, no `evidence`) excluded from retrieval; kept for the QA-abstention metric.

## 6. Retrieval ground-truth

`Hit` exposes `permalink` (session) and `heading_path` (which we engineer to contain the turn id). Match at two granularities by parsing the turn id from `heading_path`, or collapsing to `permalink`.

- **LongMemEval-S:** session gold = `answer_session_ids` (matches `Hit.permalink`); turn gold = turns with `turn.get("has_answer") is True`, gold id `f"{session_id}_{turn_idx+1}"`. Recall@k/MRR/nDCG computable at both. `_abs` instances excluded.
- **LoCoMo:** turn gold = `qa["evidence"]` (list of `dia_id`); a chunk is a hit iff its parsed, **normalized** `dia_id ∈ set(evidence)` (strip zero-padding `D30:05`↔`D30:5`, handle semicolon-compound and bare-`D` malformed ids). Session-level collapses `dia_id → session`. Recall@k = `|topk_dia_ids ∩ evidence| / |evidence|` (partial credit), matching the official turn-level formula.

**Caveats baked into the harness:**
- Cairn's "hybrid+graph-boost" row is **near-inert** on these corpora (no native wikilink graph) — reported as-is, no manufactured links.
- The vector/BM25 arms fetch top-`pool` (default 200). A LoCoMo conversation can exceed 200 chunks → recall@k silently capped. The harness **sets `pool ≥ corpus_chunk_count`** when measuring recall ceilings.
- Map any chunk back to its turn-id/`note_permalink`; **dedup gold-turn coverage** so two chunks of one gold turn aren't double-counted.

## 7. Metrics

**Retrieval (deterministic, primary CI gate):**
- `recall@k`, `nDCG@k` at **k ∈ {1,3,5,10,20}**; `MRR` (untruncated). Macro-averaged over queries (skip empty-gold/abstention). Per-category breakdown (LongMemEval `question_type`; LoCoMo `category`).
- For LongMemEval, additionally emit strict **`recall_all@{5,10}`** and **`ndcg_any@{5,10}`** labeled "paper-style" for line-up against arXiv 2410.10813 — never conflated with our fractional recall.
- Report turn-level (primary) and session-level (collapsed) for LongMemEval; turn-level primary for LoCoMo.

**QA (opt-in):** binary LLM-judge accuracy, overall + per-category, **mean ± std over N runs** (default N configurable); abstention/adversarial reported separately. Every result row pins generator model, judge model, embedder, k.

## 8. QA layer (opt-in)

- **Generator:** Anthropic. Build the reader prompt from top-k `Hit`s, hydrating chunk text via `get_chunks(con, [h.chunk_id ...])` (or `get_note` for note-level), with `heading_path` provenance. Pin model + k.
- **Judge:** binary LLM-as-judge, **temperature=0**, fixed pinned model snapshot, prompt strings versioned in-repo, outputs cached by `(prompt_hash, model)`. Judge sees **question + gold answer + model response only**. `label = "yes" in out.lower()`.
- **Per-type judge prompts** (replicate official variants): base; temporal (off-by-one day/week/month tolerance); knowledge-update (updated value must be present); preference (rubric-lenient); abstention/`_abs` and LoCoMo cat-5 (correct = model refuses/says unanswerable). Route by `question_type`/`_abs` suffix / LoCoMo `category`.
- **Comparability caveat (prominent):** judge is Anthropic, not the canonical GPT-4o — QA numbers are NOT comparable to published leaderboards; they are valid for **relative** ablation signal only.

## 9. Datasets, pinning, license

| Dataset | Source | Pin | License |
|---|---|---|---|
| **LongMemEval-S** | HF `xiaowu0162/longmemeval-cleaned`, `longmemeval_s_cleaned.json` (~277 MB) via `hf_hub_download(revision=...)` | HF revision `98d7416c24c778c2fee6e6f3006e7a073259d48f` (verify SHA at fetch; treat as known-good, not only) | **MIT** — redistribution allowed; we still don't vendor. Cite ICLR 2025 (2410.10813). Use `-cleaned`, NOT the deprecated `longmemeval` nor the Apache-2.0 multimodal `longmemeval-v2`. |
| **LoCoMo** | GitHub raw `snap-research/locomo` `data/locomo10.json` (~2.68 MB) | commit `3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376` + stored file SHA256 | **CC BY-NC 4.0** — NonCommercial + attribution. **Never vendor.** Download at runtime; keep citation visible. |

`manifest.toml` records source + revision + SHA256; verify SHA on load (both repos have had label corrections). Cache under `~/.cache/agentcairn/bench/` (mirrors the existing ledger-cache convention in `cli.py`).

**Committed synthetic fixture** (the offline-CI engine, the only committed data, NOT derived from real datasets):
- A LongMemEval-shaped instance set (~5 sessions; `haystack_session_ids`/`haystack_dates`/`haystack_sessions` with `role`/`content`, evidence turns carrying `has_answer: true`; `answer_session_ids`); include ≥1 `*_abs`, one `knowledge-update`, one `temporal-reasoning`.
- A LoCoMo-shaped conversation (`conversation.{speaker_a,speaker_b,session_1,session_1_date_time,...}`, `qa` with `evidence` dia_ids across categories 1–4 + one cat-5 adversarial + one deliberately **malformed** `dia_id` like `D2:05`).
- Known gold → tests assert **exact** recall@k values (regression test on the metric code, not just smoke). Runs full pipeline (vaultize → reconcile → ladder → metrics → mock judge) with `FakeEmbedder` in seconds, offline.

## 10. Testing & CI

- **Offline CI job** (new, separate from the core `cairn` suite): runs `benchmarks/tests/` with base install + `FakeEmbedder` — no network, no keys, no model download. Asserts exact recall@k on the fixture and the LoCoMo adversarial-denominator exclusion.
- QA-layer tests require the `bench` group + an API key → **skip-if-missing**.
- Real-dataset runs are manual/nightly, never in the standard CI gate.

## 11. Honest-reporting principles (enforced by `report.py`)

- **No single headline number.** Publish ranges with explicit apples-to-oranges caveats.
- **Retrieval and QA are never the same column** — label every number's axis.
- **Wilson confidence intervals** on per-category accuracy (research: ~56% of adjacent per-category comparisons are statistically indistinguishable — don't over-read orderings).
- Cap claims at dataset wrong-gold ceilings (LoCoMo ~93.6%); note LongMemEval "cleaned" drift from paper tables.
- State the judge-model swap prominently.
- LoCoMo category map is code-derived (1=multi-hop, 4=single-hop, 2=temporal, 3=open-domain, 5=adversarial) — verified against the file at load, not assumed.

## 12. Risks / open items

- **Graph-boost near-inert** on conversational corpora (expected; documented finding, not a bug).
- **Reranker may lose** on chat turns (ms-marco domain shift) — a real result to report, not suppress.
- **Unconfirmed at design time** (verify at implementation, don't hardcode): exact LongMemEval per-type instance counts; whether the pinned HF revision is still latest at fetch (SHA-verify); LoCoMo per-category counts (verify against the downloaded file before reporting denominators); whether bge-small `query_embed` is truly asymmetric (affects vector-arm recall, not harness design).

## 13. Scope for the implementation plan

Tasks, in order: (1) `graph_boost` toggle in `cairn.search` (TDD); (2) synthetic fixtures + `manifest.toml`; (3) adapters (LongMemEval, LoCoMo) + `vaultize`; (4) `build` (scoped index); (5) `retrieval_metrics` + the ablation `config`/runner; (6) `report` (retrieval); (7) offline CI job + fixture regression tests; (8) opt-in QA layer (`provider`/`generate`/`judge`) + skip-if-no-key tests; (9) downloader (real datasets, pinned/SHA-verified) + a manual "run real benchmark" entrypoint; (10) docs (how to run, how to read the numbers + caveats). The QA layer (8) and downloader (9) can land after the retrieval core is proven on the fixture.
