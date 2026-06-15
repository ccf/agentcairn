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
    """Build a temp DuckDB index of `n_chunks` synthetic notes/chunks/embeddings,
    generated entirely SERVER-SIDE over range(n) so the random float32 vectors never
    cross the Python boundary (per-row binds cost ~40ms/row → 100k build would take >1hr).
    Build via open_index (writable), then close — queries use open_search (rrf macro + fts)."""
    con = open_index(path, dim=dim, model_id="bench")
    try:
        # reproducible: setseed wants a value in [-1, 1]
        con.execute("SELECT setseed(?)", [((seed % 1000) / 1000.0)])
        phrase = " ".join(_WORDS[:6])
        con.execute(
            "INSERT INTO chunk_embeddings "
            f"SELECT 'c' || i, [random()::FLOAT FOR _x IN range({dim})] "
            "FROM range(?) t(i)",
            [n_chunks],
        )
        con.execute(
            "INSERT INTO notes (permalink, path, title, type, content_hash, mtime, "
            "valid_from, valid_until, superseded_by, project, harness) "
            "SELECT 'c'||i, '/bench/c'||i||'.md', 'note '||i, 'memory', 'c'||i, 0.0, "
            "NULL, NULL, NULL, NULL, NULL FROM range(?) t(i)",
            [n_chunks],
        )
        con.execute(
            "INSERT INTO chunks SELECT 'c'||i, 'c'||i, '', 0, ? FROM range(?) t(i)",
            [phrase, n_chunks],
        )
        build_fts(con)
    finally:
        con.close()


def _percentile(samples: list[float], pct: float) -> float:
    """Nearest-rank percentile (pct in 0..100) over a non-empty sample list."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def time_calls(fn, inputs, *, warmup: int = 3) -> tuple[float, float]:
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


@dataclass
class SizeResult:
    n_chunks: int
    bind_p95: float  # no-op query-vector bind (fixed, size-independent floor)
    vec_p50: float
    vec_p95: float
    hybrid_p50: float
    hybrid_p95: float
    scan_p95: float  # max(0, vec_p95 - bind_p95): the HNSW-addressable cost


def measure_size(path: str, n_chunks: int, *, dim: int, n_queries: int, seed: int) -> SizeResult:
    build_synthetic_index(path, n_chunks, dim=dim, seed=seed)
    rng = np.random.default_rng(seed + 1)
    qvecs = [rng.standard_normal(dim).astype("float32").tolist() for _ in range(n_queries + 2)]
    qstrs = [" ".join(rng.choice(_WORDS, size=3)) for _ in range(n_queries + 2)]
    con = open_search(path)
    try:
        _bind_p50, bind_p95 = time_calls(
            lambda q: con.execute(f"SELECT ?::FLOAT[{dim}]", [q]).fetchall(), qvecs, warmup=3
        )
        vec_p50, vec_p95 = time_calls(
            lambda q: vector_search(con, q, dim=dim, pool=200), qvecs, warmup=3
        )
        hyb_p50, hyb_p95 = time_calls(
            lambda qs: hybrid_search(con, qs[1], qs[0], dim=dim, limit=10, pool=200),
            list(zip(qvecs, qstrs, strict=True)),
            warmup=3,
        )
    finally:
        con.close()
    scan_p95 = max(0.0, vec_p95 - bind_p95)
    return SizeResult(n_chunks, bind_p95, vec_p50, vec_p95, hyb_p50, hyb_p95, scan_p95)


def run(
    sizes: list[int], *, dim: int = 768, n_queries: int = 50, seed: int = 0
) -> list[SizeResult]:
    import tempfile
    from pathlib import Path

    results = []
    for n in sizes:
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / "bench.duckdb")
            results.append(measure_size(path, n, dim=dim, n_queries=n_queries, seed=seed))
    return results


def render_table(results: list[SizeResult]) -> str:
    header = (
        f"{'size':>8} | {'bind p95':>8} | {'vec p95':>8} | {'scan p95':>8} | "
        f"{'hybrid p50':>10} | {'hybrid p95':>10}"
    )
    sep = "-" * len(header)
    lines = [header, sep]
    for r in results:
        lines.append(
            f"{r.n_chunks:>8} | {r.bind_p95:>8.2f} | {r.vec_p95:>8.2f} | {r.scan_p95:>8.2f} | "
            f"{r.hybrid_p50:>10.2f} | {r.hybrid_p95:>10.2f}"
        )
    return "\n".join(lines)


def verdict(results: list[SizeResult], *, budget_ms: float) -> str:
    for r in sorted(results, key=lambda x: x.n_chunks):
        if r.scan_p95 >= budget_ms:
            return (
                f"Scan crossover at {r.n_chunks} chunks: cosine scan p95 "
                f"{r.scan_p95:.1f}ms >= {budget_ms:.0f}ms budget — "
                f"HNSW warranted above ~{r.n_chunks}."
            )
    biggest = max((r.n_chunks for r in results), default=0)
    bind_floor = max((r.bind_p95 for r in results), default=0.0)
    return (
        f"No scan crossover <= {biggest} chunks: cosine scan stays under "
        f"{budget_ms:.0f}ms; recall latency is dominated by the fixed "
        f"~{bind_floor:.0f}ms query-vector bind, which HNSW does not address."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="agentcairn retrieval-latency benchmark")
    ap.add_argument(
        "--sizes",
        default="500,1000,10000,50000,100000",
        help="comma-separated chunk counts",
    )
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
