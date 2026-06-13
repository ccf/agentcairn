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
def _no_retry_backoff_sleep(monkeypatch):
    """The LLM judge retries failed chunks with a real backoff sleep; no test
    should actually wait. Retries still happen (count is asserted where it
    matters) — only the wait is neutralized."""
    monkeypatch.setattr(_judge, "_SLEEP", lambda _s: None)
