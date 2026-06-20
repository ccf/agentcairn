# SPDX-License-Identifier: Apache-2.0
import json

import pytest
from typer.testing import CliRunner

from cairn.cli import app
from cairn.hosts import get_host

runner = CliRunner()

_MCP_HOSTS = ["cursor", "claude-desktop", "vscode", "gemini"]


@pytest.mark.parametrize("host_id", _MCP_HOSTS)
def test_install_writes_mcp_config(host_id, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    host = get_host(host_id)
    vault = tmp_path / "vault"
    res = runner.invoke(app, ["install", host_id, "--vault", str(vault)])
    assert res.exit_code == 0, res.output

    cfg_path = host.config_path()  # expands ~ against $HOME
    assert cfg_path.exists(), f"{host_id}: no config written at {cfg_path}"
    data = json.loads(cfg_path.read_text())
    root_key = getattr(host, "root_key", None) or "mcpServers"
    servers = data[root_key]
    assert "agentcairn" in servers, f"{host_id}: no agentcairn entry"
    blob = json.dumps(servers["agentcairn"])
    assert "CAIRN_VAULT" in blob
    assert "CAIRN_INDEX" not in blob


_PLUGIN_HOSTS = ["claude-code", "codex", "antigravity"]


@pytest.mark.parametrize("host_id", _PLUGIN_HOSTS)
def test_install_plugin_host_prints_command(host_id, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # antigravity's `agy plugin install` takes a local directory (not a git repo),
    # so --print requires --source; use a dummy path that lets us see the rendered command.
    extra_args = []
    if host_id == "antigravity":
        extra_args = ["--source", str(tmp_path / "agentcairn-plugin")]
    res = runner.invoke(app, ["install", host_id, "--print"] + extra_args)
    assert res.exit_code == 0, res.output
    # claude-code and codex always include "agentcairn@agentcairn" in their plugin_add argv.
    # antigravity's plugin_add is ("plugin", "install", "{source}"), so we check for "agy".
    expected = "agentcairn" if host_id != "antigravity" else "agy"
    assert expected in res.output, f"{host_id}: expected {expected!r} in output: {res.output!r}"
