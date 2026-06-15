# Retrieval-Latency Benchmark

**Status:** Approved (2026-06-15)
**Affects:** `benchmarks/cairn_bench/` (new `latency.py`), `benchmarks/tests/` (smoke test), `benchmarks/README.md` (recorded results + conclusion), CLAUDE.md (resolve the "unvalidated threshold" note). No change to `src/cairn` — the live search path is untouched.

## Problem

agentcairn's vector search is brute-force cosine (`array_cosine_similarity` over all `chunk_embeddings`, top-`pool`, fused with BM25 via RRF). The roadmap lists "in-memory HNSW for large-vault retrieval latency," and CLAUDE.md/the original design flag the in-memory-vs-persisted HNSW threshold as **unvalidated** ("benchmark rebuild/cold-start latency vs vault size to set the threshold"). But there is **no latency data** in the repo, the real vault is small (~119 notes / a few hundred chunks), and every query opens a fresh DuckDB connection (no long-lived state to host an in-memory index) — so building HNSW now would be premature and architecturally awkward.

This builds the **measurement prerequisite**: a retrieval-latency benchmark that shows how brute-force latency scales with vault size and where (if anywhere ≤ 100k chunks) it stops meeting an interactive budget. The result decides whether/when HNSW is worth building and becomes the guardrail for that decision. **HNSW itself is out of scope** — this is "measure first."

## Research — current state (verified on disk)

- **Vector arm:** `vector_search(con, qvec, *, dim, pool=200)` (`src/cairn/search/engine.py:89-99`) — `SELECT chunk_id, array_cosine_similarity(vec, ?::FLOAT[dim]) ... ORDER BY sim DESC LIMIT pool`. The same SQL is the `vec` CTE inside `_hybrid_sql`.
- **End-to-end retrieval:** `hybrid_search(con, query, qvec, *, dim, limit, pool, …)` (`engine.py:203-239`) runs BM25 + cosine + RRF fusion in one SQL and **takes a precomputed `qvec`** — so the benchmark can drive it without an embedder. `search()` (the wrapper) is what embeds the query and optionally reranks; the benchmark deliberately measures `hybrid_search`/`vector_search` to exclude query-embedding (constant, size-independent) and rerank (size-independent).
- **Storage:** `chunk_embeddings(chunk_id VARCHAR PRIMARY KEY, vec FLOAT[dim])`; `chunks(chunk_id, note_permalink, heading_path, ordinal, text)`; `notes(permalink, …, valid_from, valid_until, superseded_by, project, harness)`. The hybrid SQL JOINs `notes`, so synthetic data needs `notes` rows too. Default dim = 768 (`nomic-embed-text-v1.5`).
- **FTS:** `build_fts(con)` (`src/cairn/index/build.py:221`) runs `PRAGMA create_fts_index('chunks', 'chunk_id', 'text', overwrite=1)` — reused so the BM25 arm is real.
- **Benchmarks package:** `benchmarks/cairn_bench/` (importable; `pyproject.toml` sets `pythonpath = ["src", ".", "benchmarks"]`). Tests in `benchmarks/tests/` run in CI via `bench-offline` (`uv run pytest benchmarks/tests/ -q`).

## Goal / decisions (brainstorm)

- **Measure first, build HNSW only if warranted (option A).** Deliverable = the benchmark + a recorded result + a documented threshold. No HNSW, no search-path change.
- **Measure both (option C):** the **vector arm** in isolation (what HNSW replaces) *and* **end-to-end hybrid retrieval** (what the user feels), reporting the vector arm's p95 as a **% of** end-to-end p95.
- **Budget:** p95 end-to-end retrieval **< 100 ms** is "fast enough" for an interactive tool call. The benchmark reports the crossover size against it; raw curves are printed regardless so a stricter/looser line is derivable.
- **Sizes:** 500 / 1 000 / 10 000 / 50 000 / 100 000 chunks. **p50 + p95** over a fixed query count (default 50), after warm-up.
- **Synthetic random vectors, fixed seed** (cosine cost is data-independent; HNSW *recall* would need realistic clustering, but that's the follow-up, not this).
- **Manual/local tool, not a CI gate** (latency is machine-dependent). A tiny-size smoke test runs in CI to keep it from bit-rotting.

## Architecture

New module `benchmarks/cairn_bench/latency.py`, runnable as `uv run python -m cairn_bench.latency`.

### A. Synthetic index builder

```
build_synthetic_index(path, n_chunks, *, dim, seed) -> None
```
**Build/query split (matches production):** build through `open_index(path, …)` (a writable connection — creates tables, loads `vss`/`fts`), insert + `build_fts`, then **close**. Query through `open_search(path)` (`engine.py:19`), which opens an in-memory connection, installs the `rrf()` macro, loads `fts`, and ATTACHes the index read-only. This matters because `hybrid_search` depends on the `rrf()` macro and `match_bm25` — both provided by `open_search`, **not** `open_index`. Running the query functions on the raw `open_index` connection would fail (`rrf` undefined). So the benchmark uses a temp-file index path, builds with `open_index`, then measures with `open_search`.

The builder:
- Open the index via `cairn.index.schema.open_index(path, dim=dim, model_id="bench")` so the schema (incl. the `project`/`harness` columns) exactly matches production.
- **Generate all rows server-side in DuckDB** (review finding C1: per-row `executemany` of 768-float Python lists costs ~40 ms/row — the same Python↔DuckDB bind tax — making a 100k build take >1 hour). Generate entirely in-engine over `range(n_chunks)` so vectors never cross the Python boundary:
  - `SELECT setseed(<seed-normalized-to-[-1,1]>)` for reproducibility, then
  - `INSERT INTO chunk_embeddings SELECT 'c'||i, [random()::FLOAT FOR _x IN range(<dim>)] FROM range(<n>) t(i)` — a fresh random `dim`-vector per row, in one statement.
  - `INSERT INTO notes SELECT 'c'||i, '/bench/c'||i||'.md', 'note '||i, 'memory', 'c'||i, 0.0, NULL, NULL, NULL, NULL, NULL FROM range(<n>) t(i)`.
  - `INSERT INTO chunks SELECT 'c'||i, 'c'||i, '', 0, '<fixed 6-word phrase from the word list>' FROM range(<n>) t(i)` (constant tokenizable text is fine for latency — BM25 still does full work; query strings draw from the same words so they match).
  This is near-instant at all sizes and eliminates the build-time bind cost. (numpy is still used to generate the *query* vectors in `measure_size`, which is the production-representative bind we WANT to measure.)
- `build_fts(con)` to create the BM25 index.
- **Determinism:** `setseed` makes the embedding generation reproducible across builds; the determinism test asserts `c0`'s vector is identical across two same-seed builds.

### B. Timing

```
time_calls(fn, queries, *, warmup=3) -> (p50_ms, p95_ms)
```
- `queries` is a list of precomputed query vectors (and, for end-to-end, a paired query string drawn from the same word list so BM25 matches something).
- Run `warmup` calls (discarded — ≥3 to clear DuckDB extension/JIT/first-plan cold start), then time each remaining call with `time.perf_counter`; return p50/p95 in ms via a nearest-rank percentile on the sorted samples. (Wall-clock `perf_counter` is correct — it captures the cross-language bind cost CPU-time would hide. With ~50 samples a single GC/jitter spike can land on p95; acceptable for a manual tool, noted in the report.)

### C. Per-size measurement

```
measure_size(path, n_chunks, *, dim, n_queries, seed) -> SizeResult
```
- Build the synthetic index once into a temp-file path (`build_synthetic_index`), then open it for querying via `open_search(path)` (the connection that carries the `rrf` macro + fts).
- `bind`: time `lambda q: con.execute("SELECT ?::FLOAT[dim]", [q]).fetchall()` — the no-op query-vector bind baseline (the fixed, size-independent floor; see §1 finding).
- `vec`: time `lambda q: vector_search(con, q, dim=dim, pool=200)` over the same `n_queries` random vectors.
- `hybrid`: time `lambda (q, s): hybrid_search(con, s, q, dim=dim, limit=10, pool=200)` over the same vectors paired with query strings.
- Return `SizeResult(n_chunks, bind_p95, vec_p50, vec_p95, hybrid_p50, hybrid_p95, scan_p95=max(0.0, vec_p95 - bind_p95))`. The **`scan_p95`** (size-dependent cosine cost, with the fixed bind floor subtracted) is the number the HNSW decision keys off — it is what HNSW would actually reduce.

### D. Runner + report

```
run(sizes, *, dim=768, n_queries=50, budget_ms=100.0, seed=0) -> list[SizeResult]
main()  # argparse: --sizes, --queries, --dim, --budget-ms, --seed
```
- Print a table: `size | bind p95 | vec p95 | scan p95 | hybrid p50 | hybrid p95` (scan = vec − bind, the HNSW-addressable cost).
- Print a verdict keyed off **`scan_p95`** (not the raw vector floor): the smallest size whose `scan_p95 >= budget_ms` ("scan crossover at N chunks — HNSW warranted above ~N"), or "no scan crossover ≤ {max size}: the cosine scan stays under {budget}ms at all tested sizes; recall latency is dominated by the fixed ~{bind}ms query-vector bind, which HNSW does not address." The verdict explicitly names the fixed bind floor so the reader doesn't mistake it for scan cost.
- The operator pastes the table + verdict into `benchmarks/README.md` (a new "Retrieval latency" subsection) and updates CLAUDE.md's threshold note with the measured numbers.

## Data flow

```
for n in sizes:
  # build (writable) — vectors/notes/chunks generated server-side via range(n); then close
  con = open_index(tmp, dim); setseed; INSERT ... SELECT ... FROM range(n); build_fts(con); con.close()
  # query (read-only attach + rrf macro + fts)
  con = open_search(tmp)
  qvecs = [rng.standard_normal(dim).tolist() for _ in range(n_queries+warmup)]   # production-style list bind
  bind_p95       = time_calls(λ q: con.execute("SELECT ?::FLOAT[dim]", [q]).fetchall(), qvecs)[1]
  vec_p50/p95    = time_calls(λ q: vector_search(con, q, dim, pool=200), qvecs)
  hyb_p50/p95    = time_calls(λ (q,s): hybrid_search(con, s, q, dim, limit=10, pool=200), zip(qvecs, qstrings))
  scan_p95       = max(0.0, vec_p95 - bind_p95)
  con.close()
print table + verdict(budget_ms)   # verdict keys off scan_p95
```

## Error handling

- Sizes are bounded by `--sizes`; default max 100k (≈100k×768×4 B ≈ 307 MB of vectors — fine in memory). The builder is the only heavy step; if a host can't hold 100k, the operator passes smaller `--sizes`.
- Each size uses its own fresh index/connection, closed before the next (no cross-size state).
- The benchmark imports from `cairn.search.engine` / `cairn.index` — if those move, the smoke test fails loudly in CI.
- numpy import: relied on transitively today; the plan confirms it resolves under `uv run` and, if not, adds `numpy` to the `bench` extra.

## Testing / verification

- **Smoke test** (`benchmarks/tests/test_latency.py`, runs in CI): `run(sizes=[50], n_queries=5, dim=8)` returns one `SizeResult` with `vec_p95 >= 0`, `hybrid_p50 > 0`, `bind_p95 >= 0`, `scan_p95 >= 0`, and `measure_size` doesn't raise; assert the table-render function produces the expected column headers. **No latency-value assertions** (machine-dependent).
- **Determinism:** same seed → identical synthetic data (assert two builds at a tiny size produce identical `chunk_embeddings` row counts and an identical `c0` vector — `setseed`-backed).
- `uv run pytest benchmarks/tests/ -q` green (the CI `bench-offline` job).
- **Dogfood (manual):** run `PYTHONPATH=benchmarks uv run python -m cairn_bench.latency` (the repo's established invocation — `benchmarks/` is on pytest's pythonpath but not the interpreter's) on the dev machine across the full size set; record the table + verdict into `benchmarks/README.md`; update CLAUDE.md's "unvalidated threshold" line with the measured numbers (the scan crossover, or "scan stays sub-budget ≤ 100k; recall latency is bind-dominated").

## File-by-file

| File | Change |
|---|---|
| `benchmarks/cairn_bench/latency.py` | **new** — synthetic builder, timing, per-size measurement, runner + `main()` (argparse) |
| `benchmarks/tests/test_latency.py` | **new** — tiny-size smoke + determinism (no latency-value asserts) |
| `benchmarks/README.md` | add a "Retrieval latency" subsection with the recorded table + verdict |
| `CLAUDE.md` | replace the "unvalidated HNSW threshold" note with the measured result |
| `pyproject.toml` | (only if needed) add `numpy` to the `bench` extra if it doesn't resolve transitively under `uv run` |

## Non-goals

- **Building HNSW** (in-memory or DuckDB VSS) or any change to the live search path / connection lifecycle — that's the follow-up this benchmark informs.
- Measuring **query-embedding** latency (constant, size-independent) or **rerank** latency (size-independent; scales with `pool`, not vault size).
- **recall@k / quality** measurement — the existing `retrieval_metrics.py` harness owns that.
- Making the latency benchmark a **CI gate** (machine-dependent and noisy).

## Open questions

None.
