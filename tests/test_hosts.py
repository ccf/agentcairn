# SPDX-License-Identifier: Apache-2.0
from cairn.hosts import detected_hosts, get_host
from cairn.hosts.entry import mcp_entry


def test_mcp_entry_shape():
    e = mcp_entry("/home/u/agentcairn", "/home/u/.cache/agentcairn/index.duckdb")
    assert e == {
        "command": "uvx",
        "args": ["agentcairn"],
        "env": {
            "CAIRN_VAULT": "/home/u/agentcairn",
            "CAIRN_INDEX": "/home/u/.cache/agentcairn/index.duckdb",
        },
    }


def test_get_host_known_and_unknown():
    assert get_host("cursor").format == "mcpServers"
    assert get_host("codex").format == "codex-toml"
    assert get_host("nope") is None


def test_detected_hosts_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Nothing present yet → none detected.
    assert detected_hosts() == []
    # Create Cursor's config dir → it's detected.
    (tmp_path / ".cursor").mkdir()
    ids = {h.id for h in detected_hosts()}
    assert "cursor" in ids
