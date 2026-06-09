# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

from cairn_bench.adapters import locomo


def test_adversarial_excluded_from_retrieval_queries(locomo_samples):
    _notes, queries = locomo.adapt(locomo_samples[0])
    # category 5 (adversarial) contributes NO retrieval query -> excluded from both
    # numerator and denominator of any macro-average (the Zep denominator bug).
    assert all(q.category != 5 for q in queries)
    # the fixture has exactly one cat-5 item, so 5 qa -> 4 retrieval queries
    assert len(queries) == 4
