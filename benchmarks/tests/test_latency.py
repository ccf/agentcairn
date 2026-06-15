# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import time

from cairn_bench.latency import build_synthetic_index

from cairn.search import open_search
from cairn.search.engine import vector_search


def test_build_synthetic_index_populates(tmp_path):
    path = str(tmp_path / "bench.duckdb")
    build_synthetic_index(path, n_chunks=40, dim=8, seed=1)
    con = open_search(path)
    try:
        assert con.execute("SELECT count(*) FROM chunk_embeddings").fetchone()[0] == 40
        assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 40
        assert con.execute("SELECT count(*) FROM notes").fetchone()[0] == 40
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


def test_percentile_basic():
    from cairn_bench.latency import _percentile

    data = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert _percentile(data, 50) == 30.0
    assert 40.0 <= _percentile(data, 95) <= 50.0


def test_time_calls_returns_two_positive_ms():
    from cairn_bench.latency import time_calls

    calls = [0.001] * 8  # 8 inputs; fn sleeps 1ms each
    p50, p95 = time_calls(lambda s: time.sleep(s), calls, warmup=2)
    assert p50 > 0 and p95 >= p50


def test_measure_size_and_run_smoke(tmp_path, monkeypatch):
    import cairn_bench.latency as L

    monkeypatch.chdir(tmp_path)
    results = L.run(sizes=[40], dim=8, n_queries=5, seed=3)
    assert len(results) == 1
    r = results[0]
    assert r.n_chunks == 40
    assert r.bind_p95 >= 0
    assert r.vec_p95 >= 0
    assert r.hybrid_p50 > 0
    assert r.scan_p95 >= 0


def test_verdict_crossover():
    from cairn_bench.latency import SizeResult, verdict

    # SizeResult(n_chunks, bind_p95, vec_p50, vec_p95, hybrid_p50, hybrid_p95, scan_p95)
    below = SizeResult(1000, 5.0, 6.0, 7.0, 8.0, 9.0, 2.0)
    above = SizeResult(50000, 5.0, 200.0, 205.0, 1.0, 210.0, 200.0)
    assert "50000" in verdict([below, above], budget_ms=100.0)
    assert "no scan crossover" in verdict([below], budget_ms=100.0).lower()


def test_render_table_has_headers():
    from cairn_bench.latency import SizeResult, render_table

    out = render_table([SizeResult(40, 0.1, 0.15, 0.2, 0.5, 0.9, 0.05)])
    assert "bind p95" in out and "scan p95" in out and "hybrid p95" in out
