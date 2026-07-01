# SPDX-License-Identifier: Apache-2.0
"""The test suite must never touch the developer's real usage ledger.

Regression guard: for months every test that triggered `usage.record` (recall,
savings, recall-hook tests) appended to the real ~/.cache/agentcairn/usage.jsonl,
inflating the personal savings stat with thousands of tiny-index test recalls.
The autouse `_isolated_usage_ledger` fixture redirects the ledger to tmp.
"""

from pathlib import Path

from cairn import usage


def test_ledger_path_is_not_the_real_home_ledger():
    real = Path.home() / ".cache" / "agentcairn" / "usage.jsonl"
    assert usage.ledger_path() != real, (
        "tests must not resolve to the developer's real usage ledger"
    )


def test_record_writes_to_the_isolated_ledger_only():
    real = Path.home() / ".cache" / "agentcairn" / "usage.jsonl"
    before = real.read_text() if real.exists() else None

    usage.record("recall", full=123, recalled=45, k=3)

    p = usage.ledger_path()
    assert p != real
    assert p.exists() and p.read_text().strip(), "record must write to the isolated ledger"
    after = real.read_text() if real.exists() else None
    assert after == before, "record must not touch the real home ledger"
