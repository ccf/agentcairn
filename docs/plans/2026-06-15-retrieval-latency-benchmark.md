# Retrieval-Latency Benchmark Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A manual benchmark that measures brute-force vector-search latency (vector arm + end-to-end hybrid) across synthetic vault sizes, to decide whether/when an HNSW index is worth building.

**Architecture:** New `benchmarks/cairn_bench/latency.py`: build a synthetic DuckDB index (random `FLOAT[dim]` embeddings + pseudo-text, fixed seed) via the production `open_index`, then time `vector_search` and `hybrid_search` through the production `open_search` connection, reporting p50/p95 per size and the crossover vs a 100 ms budget. No change to `src/cairn`.

**Tech Stack:** Python 3.11+, DuckDB (FTS + array cosine), numpy (already available transitively), pytest, uv. Benchmarks package is on `pythonpath` (`pyproject.toml`), importable as `cairn_bench.*` / `cairn.*`.

**Reference:** Spec `docs/specs/2026-06-15-retrieval-latency-benchmark-design.md`. Branch `feat/retrieval-latency-benchmark` has the spec committed.

**Key production APIs (verified):**
- `cairn.index.schema.open_index(path, *, dim, model_id) -> con` — writable; creates schema, loads vss+fts.
- `cairn.index.build.build_fts(con)` — `PRAGMA create_fts_index('chunks','chunk_id','text',overwrite=1)`.
- `cairn.search.open_search(path) -> con` — read-only attach + `rrf()` macro + fts (the query connection).
- `cairn.search.engine.vector_search(con, qvec, *, dim, pool=200) -> list[(chunk_id, sim)]`.
- `cairn.search.engine.hybrid_search(con, query, qvec, *, dim, limit=10, pool=200, graph_boost=True, validity_aware=True, now=None) -> list[dict]`.
- `notes` columns: `permalink, path, title, type, content_hash, mtime, valid_from, valid_until, superseded_by, project, harness`. `chunks`: `chunk_id, note_permalink, heading_path, ordinal, text`. `chunk_embeddings`: `chunk_id, vec FLOAT[dim]`.

---

## File Structure

| File | Responsibility |
|---|---|
| `benchmarks/cairn_bench/latency.py` | **new** — synthetic builder, timing/percentile, per-size measurement, runner + `main()` |
| `benchmarks/tests/test_latency.py` | **new** — builder population + determinism; timing percentiles; run() smoke + verdict (no latency-value asserts) |
| `benchmarks/README.md` | add a "Retrieval latency" subsection with the recorded table + verdict (Task 4, from a real run) |
| `CLAUDE.md` | replace the "unvalidated HNSW threshold" note with the measured result (Task 4) |

---

## Task 1: Synthetic index builder

**Files:**
- Create: `benchmarks/cairn_bench/latency.py`
- Test: `benchmarks/tests/test_latency.py`

- [ ] **Step 1: Write the failing test**

Create `benchmarks/tests/test_latency.py`:

```python
# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from cairn_bench.latency import build_synthetic_index
from cairn.search import open_search
from cairn.search.engine import vector_search


def test_build_synthetic_index_populates(tmp_path):
    path = str(tmp_path / "bench.duckdb")
    build_synthetic_index(path, n_chunks=40, dim=8, seed=1)
    con = open_search(path)
    try:
        n = con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0]
        assert n == 40
        assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 40
        assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 40
        # vector arm returns results (embeddings are queryable)
        hits = vector_search(con, [0.1] * 8, dim=8, pool=10)
        assert len(hits) == 10
    finally:
        con.close()


def test_build_synthetic_index_is_deterministic(tmp_path):
    p1 = str(tmp_path / "a.duckdb")
    p2 = str(tmp_path / "b.duckdb")
    build_synthetic_index(p1, n_chunks=20, dim=8, seed=7)
    build_synthetic_index(p2, n_chunks=20, dim=8, seed=7)
    c1, c2 = open_search(p1), open_search(p2)
    try:
        v1 = c1.execute("SELECT vec FROM chunk_embeddings WHERE chunk_id = 'c0'").fetchone()[0]
        v2 = c2.execute("SELECT vec FROM chunk_embeddings WHERE chunk_id = 'c0'").fetchone()[0]
        assert list(v1) == list(v2)
    finally:
        c1.close()
        c2.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest benchmarks/tests/test_latency.py -k build_synthetic -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn_bench.latency'`.

- [ ] **Step 3: Implement the builder**

Create `benchmarks/cairn_bench/latency.py`:

```python
# SPDX-License-Identifier: Apache-2.0
"""Retrieval-latency benchmark: measure brute-force vector search (vector arm +
end-to-end hybrid) across synthetic vault sizes, to decide whether/when an HNSW
index is worth building. Manual tool — not a CI gate. See
docs/specs/2026-06-15-retrieval-latency-benchmark-design.md."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np

from cairn.index.build import build_fts
from cairn.index.schema import open_index
from cairn.search import open_search
from cairn.search.engine import hybrid_search, vector_search

_WORDS = (
    "deploy key rotation token cache schema vector index recall memory vault "
    "harness project session redact judge consolidate embed chunk note query"
).split()


def build_synthetic_index(path: str, n_chunks: int, *, dim: int, seed: int) -> None:
    """Build a temp DuckDB index of `n_chunks` synthetic notes/chunks/embeddings
    (random float32 vectors + pseudo-text, seeded). Build via open_index (writable),
    then close — queries use open_search (which carries the rrf macro + fts)."""
    rng = np.random.default_rng(seed)
    vecs = rng.standard_normal((n_chunks, dim)).astype("float32")
    con = open_index(path, dim=dim, model_id="bench")
    try:
        note_rows = []
        chunk_rows = []
        emb_rows = []
        for i in range(n_chunks):
            cid = f"c{i}"
            text = " ".join(rng.choice(_WORDS, size=6))
            note_rows.append((cid, f"/bench/{cid}.md", f"note {i}", "memory", cid, 0.0,
                              None, None, None, None, None))
            chunk_rows.append((cid, cid, "", 0, text))
            emb_rows.append((cid, vecs[i].tolist()))
        con.execute("BEGIN")
        con.executemany(
            "INSERT INTO notes (permalink, path, title, type, content_hash, mtime, "
            "valid_from, valid_until, superseded_by, project, harness) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            note_rows,
        )
        con.executemany("INSERT INTO chunks VALUES (?, ?, ?, ?, ?)", chunk_rows)
        con.executemany("INSERT INTO chunk_embeddings VALUES (?, ?)", emb_rows)
        con.execute("COMMIT")
        build_fts(con)
    finally:
        con.close()
```

(Note: `rng.choice(_WORDS, size=6)` returns a numpy array of strings; `" ".join(...)` works on it. The per-row `executemany` inside one transaction is fast enough for the smoke sizes and acceptable for the manual 100k run; if 100k proves too slow during dogfood, Task 3's report can note switching to a registered-relation bulk insert — not required for correctness.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest benchmarks/tests/test_latency.py -k build_synthetic -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/cairn_bench/latency.py benchmarks/tests/test_latency.py
git commit -m "feat(bench): synthetic index builder for retrieval-latency benchmark"
```

---

## Task 2: Timing + percentile helper

**Files:**
- Modify: `benchmarks/cairn_bench/latency.py`
- Test: `benchmarks/tests/test_latency.py`

- [ ] **Step 1: Write the failing test**

Add to `benchmarks/tests/test_latency.py`:

```python
def test_percentile_basic():
    from cairn_bench.latency import _percentile

    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(data, 50) == 30.0
    # p95 lands at/just below the top of a 5-element sample
    assert 40.0 <= _percentile(data, 95) <= 50.0


def test_time_calls_returns_two_positive_ms():
    from cairn_bench.latency import time_calls

    calls = [0.001] * 8  # 8 inputs; fn sleeps 1ms each
    p50, p95 = time_calls(lambda s: time.sleep(s), calls, warmup=2)
    assert p50 > 0 and p95 >= p50
```
(Add `import time` at the top of the test file if not present.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest benchmarks/tests/test_latency.py -k "percentile or time_calls" -v`
Expected: FAIL — `_percentile`/`time_calls` not defined.

- [ ] **Step 3: Implement**

Add to `benchmarks/cairn_bench/latency.py`:

```python
def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100) over a non-empty sample list."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def time_calls(fn, inputs, *, warmup: int = 2) -> tuple[float, float]:
    """Run `fn(x)` for each x in `inputs`; discard the first `warmup` calls;
    return (p50_ms, p95_ms) over the rest."""
    samples: list[float] = []
    for i, x in enumerate(inputs):
        t0 = time.perf_counter()
        fn(x)
        dt_ms = (time.perf_counter() - t0) * 1000.0
        if i >= warmup:
            samples.append(dt_ms)
    return _percentile(samples, 50), _percentile(samples, 95)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest benchmarks/tests/test_latency.py -k "percentile or time_calls" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/cairn_bench/latency.py benchmarks/tests/test_latency.py
git commit -m "feat(bench): p50/p95 timing helper"
```

---

## Task 3: Per-size measurement + runner + verdict + main()

**Files:**
- Modify: `benchmarks/cairn_bench/latency.py`
- Test: `benchmarks/tests/test_latency.py`

- [ ] **Step 1: Write the failing test**

Add to `benchmarks/tests/test_latency.py`:

```python
def test_measure_size_and_run_smoke(tmp_path, monkeypatch):
    import cairn_bench.latency as L

    # keep the smoke test fast and hermetic: build under tmp
    monkeypatch.chdir(tmp_path)
    results = L.run(sizes=[40], dim=8, n_queries=5, seed=3)
    assert len(results) == 1
    r = results[0]
    assert r.n_chunks == 40
    assert r.vec_p50 > 0 and r.vec_p95 >= 0
    assert r.hybrid_p50 > 0 and r.hybrid_p95 >= 0
    assert r.vec_pct >= 0


def test_verdict_crossover():
    from cairn_bench.latency import SizeResult, verdict

    below = SizeResult(1000, 1.0, 2.0, 5.0, 9.0, 0.22)
    above = SizeResult(50000, 1.0, 2.0, 60.0, 150.0, 0.013)
    assert "50000" in verdict([below, above], budget_ms=100.0)
    assert "no crossover" in verdict([below], budget_ms=100.0).lower()


def test_render_table_has_headers():
    from cairn_bench.latency import SizeResult, render_table

    out = render_table([SizeResult(40, 0.1, 0.2, 0.5, 0.9, 0.22)])
    assert "size" in out and "vec p95" in out and "hybrid p95" in out
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest benchmarks/tests/test_latency.py -k "measure_size_and_run or verdict or render_table" -v`
Expected: FAIL — `SizeResult`/`run`/`verdict`/`render_table` not defined.

- [ ] **Step 3: Implement**

Add to `benchmarks/cairn_bench/latency.py`:

```python
@dataclass
class SizeResult:
    n_chunks: int
    vec_p50: float
    vec_p95: float
    hybrid_p50: float
    hybrid_p95: float
    vec_pct: float  # vec_p95 / hybrid_p95 (share of end-to-end the vector arm represents)


def measure_size(
    path: str, n_chunks: int, *, dim: int, n_queries: int, seed: int
) -> SizeResult:
    build_synthetic_index(path, n_chunks, dim=dim, seed=seed)
    rng = np.random.default_rng(seed + 1)
    qvecs = [rng.standard_normal(dim).astype("float32").tolist() for _ in range(n_queries + 2)]
    qstrs = [" ".join(rng.choice(_WORDS, size=3)) for _ in range(n_queries + 2)]
    con = open_search(path)
    try:
        vec_p50, vec_p95 = time_calls(
            lambda q: vector_search(con, q, dim=dim, pool=200), qvecs, warmup=2
        )
        hyb_p50, hyb_p95 = time_calls(
            lambda qs: hybrid_search(con, qs[1], qs[0], dim=dim, limit=10, pool=200),
            list(zip(qvecs, qstrs, strict=True)),
            warmup=2,
        )
    finally:
        con.close()
    pct = (vec_p95 / hyb_p95) if hyb_p95 > 0 else 0.0
    return SizeResult(n_chunks, vec_p50, vec_p95, hyb_p50, hyb_p95, pct)


def run(sizes: list[int], *, dim: int = 768, n_queries: int = 50, seed: int = 0) -> list[SizeResult]:
    import tempfile
    from pathlib import Path

    results = []
    for n in sizes:
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "bench.duckdb")
            results.append(measure_size(path, n, dim=dim, n_queries=n_queries, seed=seed))
    return results


def render_table(results: list[SizeResult]) -> str:
    header = f"{'size':>8} | {'vec p50':>8} | {'vec p95':>8} | {'hybrid p50':>10} | {'hybrid p95':>10} | {'vec %':>6}"
    sep = "-" * len(header)
    lines = [header, sep]
    for r in results:
        lines.append(
            f"{r.n_chunks:>8} | {r.vec_p50:>8.2f} | {r.vec_p95:>8.2f} | "
            f"{r.hybrid_p50:>10.2f} | {r.hybrid_p95:>10.2f} | {r.vec_pct * 100:>5.0f}%"
        )
    return "\n".join(lines)


def verdict(results: list[SizeResult], *, budget_ms: float) -> str:
    for r in sorted(results, key=lambda x: x.n_chunks):
        if r.hybrid_p95 >= budget_ms:
            return (
                f"Crossover at {r.n_chunks} chunks: end-to-end p95 "
                f"{r.hybrid_p95:.1f}ms >= {budget_ms:.0f}ms budget — HNSW warranted above ~{r.n_chunks}."
            )
    biggest = max((r.n_chunks for r in results), default=0)
    return (
        f"No crossover <= {biggest} chunks: brute force stays under the "
        f"{budget_ms:.0f}ms p95 budget at all tested sizes."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="agentcairn retrieval-latency benchmark")
    ap.add_argument("--sizes", default="500,1000,10000,50000,100000",
                    help="comma-separated chunk counts")
    ap.add_argument("--queries", type=int, default=50)
    ap.add_argument("--dim", type=int, default=768)
    ap.add_argument("--budget-ms", type=float, default=100.0)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    sizes = [int(s) for s in args.sizes.split(",") if s.strip()]
    results = run(sizes, dim=args.dim, n_queries=args.queries, seed=args.seed)
    print(render_table(results))
    print()
    print(verdict(results, budget_ms=args.budget_ms))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest benchmarks/tests/test_latency.py -v`
Expected: PASS (all tasks' tests).

- [ ] **Step 5: Confirm the CLI entry runs**

Run: `uv run python -m cairn_bench.latency --sizes 100,500 --queries 10`
Expected: prints the table (2 rows) + a verdict line; exits 0.

- [ ] **Step 6: Full bench-suite + lint**

Run: `uv run pytest benchmarks/tests/ -q && uv run ruff check benchmarks && uv run ruff format --check benchmarks`
Expected: green. (Apply any ruff-format changes.)

- [ ] **Step 7: Commit**

```bash
git add benchmarks/cairn_bench/latency.py benchmarks/tests/test_latency.py
git commit -m "feat(bench): per-size measurement, runner, verdict, CLI entry"
```

---

## Task 4: Real run + record results in docs

**Files:**
- Modify: `benchmarks/README.md`, `CLAUDE.md`

- [ ] **Step 1: Run the full benchmark on this machine**

Run: `uv run python -m cairn_bench.latency`
(Full default sizes 500…100k. This builds a 100k×768 index — may take tens of seconds for the build step; that's the synthetic-data cost, not query latency.) Capture the printed table + verdict verbatim. If the 100k build is impractically slow on this machine, run `--sizes 500,1000,10000,50000` and note the omission explicitly.

- [ ] **Step 2: Record results in `benchmarks/README.md`**

Add a "## Retrieval latency" subsection containing: the command run, the captured table (as a fenced block or markdown table), and the verdict line. State the machine context (e.g. "measured on an M-series Mac, DuckDB 1.5") since latency is machine-dependent, and note it's a manual benchmark (not CI).

- [ ] **Step 3: Update `CLAUDE.md`**

Find the line flagging the HNSW threshold as unvalidated (search `grep -ni "hnsw\|in-memory\|threshold\|unvalidated" CLAUDE.md`). Replace/append with the measured conclusion — e.g. "Measured 2026-06-15: brute-force end-to-end p95 stays under 100ms up to N chunks (see benchmarks/README.md §Retrieval latency); HNSW deferred until vaults approach that — OR — crossover at N, build HNSW above it." Keep it one or two sentences, matching the file's density.

- [ ] **Step 4: Commit**

```bash
git add benchmarks/README.md CLAUDE.md
git commit -m "docs(bench): record retrieval-latency results + resolve HNSW threshold note"
```

---

## Final verification (before finishing the branch)

- [ ] `uv run pytest benchmarks/tests/ -q` green (and `uv run pytest -q` for the whole suite, to be safe).
- [ ] `uv run ruff check benchmarks && uv run ruff format --check benchmarks` clean.
- [ ] `uv run python -m cairn_bench.latency --sizes 100,500 --queries 10` prints a table + verdict (sanity).
- [ ] `benchmarks/README.md` and `CLAUDE.md` reflect the real measured numbers from Task 4.

---

## Self-Review (completed during planning)

- **Spec coverage:** §A builder → Task 1; §B timing → Task 2; §C measure_size + §D runner/report/verdict/main → Task 3; testing (smoke + determinism, no latency-value asserts) → Tasks 1-3 tests; the recorded result + CLAUDE threshold resolution → Task 4. Build-via-`open_index`/query-via-`open_search` split (the spec's corrected detail) is implemented in `build_synthetic_index` (build) and `measure_size` (query). Non-goals (no HNSW, no src/cairn change, no embedding/rerank timing, no CI gate) respected.
- **Type consistency:** `build_synthetic_index(path, n_chunks, *, dim, seed)`, `time_calls(fn, inputs, *, warmup)`, `_percentile(samples, pct)`, `measure_size(path, n_chunks, *, dim, n_queries, seed) -> SizeResult`, `run(sizes, *, dim, n_queries, seed)`, `SizeResult(n_chunks, vec_p50, vec_p95, hybrid_p50, hybrid_p95, vec_pct)`, `render_table`/`verdict` — names/signatures match across tasks and tests.
- **Placeholder scan:** no TBD/TODO; every code step is complete; Task 4 intentionally fills doc numbers from a real run (the only values not knowable at plan time), with an explicit fallback if 100k is too slow.
- **numpy** confirmed available under `uv run` (2.4.6) — no dependency change needed.
