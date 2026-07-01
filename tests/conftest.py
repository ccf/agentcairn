# SPDX-License-Identifier: Apache-2.0
import pytest

import cairn.config as _cfg
import cairn.ingest.judge as _judge


@pytest.fixture(autouse=True)
def _isolated_cairn_config(tmp_path, monkeypatch):
    """No test may read the developer's real ~/.agentcairn/config.toml."""
    monkeypatch.setenv("CAIRN_CONFIG", str(tmp_path / "cairn-test-config.toml"))
    _cfg._reset()
    yield
    _cfg._reset()


@pytest.fixture(autouse=True)
def _isolated_usage_ledger(tmp_path, monkeypatch):
    """No test may write to the developer's real ~/.cache/agentcairn/usage.jsonl.

    `usage.record` (recall / savings / recall-hook tests) otherwise appends
    tiny-index test recalls to the real ledger, inflating the personal savings
    stat. `cairn_env` reads CAIRN_USAGE_PATH from the live environment, so a
    setenv here redirects the ledger for the whole test."""
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "usage.jsonl"))


@pytest.fixture(autouse=True)
def _no_retry_backoff_sleep(monkeypatch):
    """The LLM judge retries failed chunks with a real backoff sleep; no test
    should actually wait. Retries still happen (count is asserted where it
    matters) — only the wait is neutralized."""
    monkeypatch.setattr(_judge, "_SLEEP", lambda _s: None)
