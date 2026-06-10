# SPDX-License-Identifier: Apache-2.0
"""Validate the Claude Code plugin's static assets (no network)."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
PLUGIN = ROOT / "plugin"


def _json(p):
    return json.loads(Path(p).read_text())


def test_marketplace_lists_the_plugin():
    mkt = _json(ROOT / ".claude-plugin" / "marketplace.json")
    names = {p["name"] for p in mkt["plugins"]}
    assert "agentcairn" in names


def test_plugin_manifest_valid():
    man = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert man["name"] == "agentcairn"
    assert "vault_path" in man["userConfig"]


def test_mcp_config_wires_uvx_agentcairn():
    mcp = _json(PLUGIN / ".mcp.json")
    srv = mcp["mcpServers"]["agentcairn"]
    assert srv["command"] == "uvx"
    assert srv["args"] == ["agentcairn"]
    assert srv["env"]["CAIRN_VAULT"] == "${user_config.vault_path}"
