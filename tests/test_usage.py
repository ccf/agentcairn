# SPDX-License-Identifier: Apache-2.0
import json

from cairn import usage


def test_estimate_tokens():
    assert usage.estimate_tokens("") == 0
    assert usage.estimate_tokens(None) == 0
    assert usage.estimate_tokens("abcd") == 1
    assert usage.estimate_tokens("abcde") == 2  # ceil(5/4)


def test_record_appends_row(tmp_path, monkeypatch):
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    usage.record("recall", full=1000, recalled=120, k=5)
    rows = [json.loads(line) for line in led.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "recall"
    assert rows[0]["full"] == 1000
    assert rows[0]["recalled"] == 120
    assert rows[0]["k"] == 5
    assert rows[0]["v"] == 1


def test_record_noop_when_disabled(tmp_path, monkeypatch):
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.setenv("CAIRN_USAGE", "0")
    usage.record("recall", full=1000, recalled=120, k=5)
    assert not led.exists()


def test_record_best_effort_swallows_errors(tmp_path, monkeypatch):
    # Point the ledger at a path whose parent is a FILE, so mkdir/open fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(blocker / "nope" / "usage.jsonl"))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    # Must NOT raise.
    usage.record("recall", full=10, recalled=1, k=1)
