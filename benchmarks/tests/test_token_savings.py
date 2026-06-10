# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from cairn_bench.build import build_scoped_index
from cairn_bench.token_savings import (
    estimate_tokens,
    full_haystack_tokens,
    summarize,
    to_markdown,
)

from cairn.embed import FakeEmbedder

# recalled_tokens() uses the default rerank=True path (downloads the cross-encoder),
# so it is exercised by the manual LoCoMo/LongMemEval runs, not the offline suite.


def test_estimate_tokens_heuristic():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    assert estimate_tokens("abcd") == 1  # 4 chars -> 1
    assert estimate_tokens("abcde") == 2  # ceil(5/4)
    assert estimate_tokens("a" * 400) == 100


def test_summarize_reduction_factor():
    rows = [{"full": 1000, "recalled": 100}, {"full": 2000, "recalled": 100}]
    s = summarize(rows)
    assert s["queries"] == 2
    assert s["mean_full"] == 1500
    assert s["mean_recalled"] == 100
    assert s["mean_factor"] == 15.0  # mean of 10x and 20x
    assert s["median_factor"] == 15.0


def test_summarize_skips_zero_recalled():
    rows = [{"full": 1000, "recalled": 0}, {"full": 1000, "recalled": 200}]
    s = summarize(rows)
    assert s["mean_factor"] == 5.0  # only the recalled>0 row contributes a factor


def test_to_markdown_reports_reduction_and_caveat():
    md = to_markdown([{"full": 1000, "recalled": 100}], k=10)
    assert "context reduction" in md.lower()
    assert "10.0×" in md
    assert "estimate" in md.lower()  # the honesty caveat is present


def test_to_markdown_empty():
    assert "No queries" in to_markdown([], k=10)


def test_full_haystack_tokens_matches_chunk_text(locomo_samples, tmp_path):
    from cairn_bench.adapters import locomo

    notes, _queries = locomo.adapt(locomo_samples[0])
    con, _chunks = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        full = full_haystack_tokens(con)
        chunk_texts = con.execute("SELECT text FROM chunks").fetchall()
        expected = sum(estimate_tokens(r[0]) for r in chunk_texts)
    finally:
        con.close()
    assert full == expected
    assert full > 0
