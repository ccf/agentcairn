# SPDX-License-Identifier: Apache-2.0
import json

from cairn import usage


def test_estimate_tokens():
    assert usage.estimate_tokens("") == 0
    assert usage.estimate_tokens(None) == 0
    assert usage.estimate_tokens("abcd") == 1
    assert usage.estimate_tokens("abcde") == 2  # ceil(5/4)


def test_enabled_usage_false_in_config_file(tmp_path, monkeypatch):
    """usage = false in the config file disables tracking (no env var needed)."""
    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text("usage = false\n")
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    cfg._reset()
    assert usage.enabled() is False
    cfg._reset()


def test_enabled_env_zero_still_false(monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE", "0")
    assert usage.enabled() is False


def test_enabled_default_true(monkeypatch):
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    assert usage.enabled() is True


def test_enabled_junk_value_defaults_true(monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE", "maybe")
    assert usage.enabled() is True


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


def _seed(led, rows):
    led.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows))


def test_summarize_aggregates(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    _seed(
        led,
        [
            {
                "v": 1,
                "ts": "2026-06-01T00:00:00+00:00",
                "event": "recall",
                "k": 5,
                "full": 1000,
                "recalled": 100,
            },
            {
                "v": 1,
                "ts": "2026-06-03T00:00:00+00:00",
                "event": "recall",
                "k": 5,
                "full": 3000,
                "recalled": 200,
            },
        ],
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    s = usage.summarize()
    assert s["recalls"] == 2
    assert s["total_full"] == 4000
    assert s["total_recalled"] == 300
    assert s["total_saved"] == 3700
    assert round(s["lifetime_factor"], 4) == round(4000 / 300, 4)
    assert s["first_ts"] == "2026-06-01T00:00:00+00:00"
    assert s["last_ts"] == "2026-06-03T00:00:00+00:00"


def test_summarize_tolerates_garbage(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":800,"recalled":80}\n'
        "not json at all\n"
        '{"v":1,"event":"recall"}\n'  # missing full/recalled -> skipped
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    s = usage.summarize()
    assert s["recalls"] == 1
    assert s["total_saved"] == 720


def test_summarize_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "nope.jsonl"))
    s = usage.summarize()
    assert s["recalls"] == 0
    assert s["total_saved"] == 0


def test_oneline_empty_when_no_data():
    assert usage.oneline({"recalls": 0, "total_saved": 0, "lifetime_factor": 0.0}) == ""


def test_oneline_has_total_and_count():
    s = {"recalls": 318, "total_saved": 2_300_000, "lifetime_factor": 51.0}
    line = usage.oneline(s)
    assert "saved you" in line
    assert "318 recalls" in line
    assert "2.3M" in line


def test_benchmark_imports_shared_estimator():
    from cairn_bench import token_savings

    assert token_savings.estimate_tokens is usage.estimate_tokens
    for t in ["", "abc", "x" * 41, "hello world this is a test"]:
        assert token_savings.estimate_tokens(t) == usage.estimate_tokens(t)
