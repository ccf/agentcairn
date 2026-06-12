# SPDX-License-Identifier: Apache-2.0
import pytest

import cairn.config as _cfg


@pytest.fixture(autouse=True)
def _isolated_cairn_config(tmp_path, monkeypatch):
    """No test may read the developer's real ~/.agentcairn/config.toml."""
    monkeypatch.setenv("CAIRN_CONFIG", str(tmp_path / "cairn-test-config.toml"))
    _cfg._reset()
    yield
    _cfg._reset()
