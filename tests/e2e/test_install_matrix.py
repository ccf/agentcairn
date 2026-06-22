# SPDX-License-Identifier: Apache-2.0
import json

import pytest
from typer.testing import CliRunner

from cairn.cli import app
from cairn.hosts import get_host

runner = CliRunner()

_MCP_HOSTS = ["cursor", "claude-desktop", "vscode", "gemini", "opencode"]


@pytest.mark.parametrize("host_id", _MCP_HOSTS)
def test_install_writes_mcp_config(host_id, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    host = get_host(host_id)
    assert host is not None, f"unknown host: {host_id}"
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


def test_install_opencode_installs_plugin_and_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    host = get_host("opencode")
    assert host is not None
    vault = tmp_path / "vault"
    res = runner.invoke(app, ["install", "opencode", "--vault", str(vault)])
    assert res.exit_code == 0, res.output

    base = host.config_path().parent  # ~/.config/opencode
    plugin_file = base / "plugin" / "agentcairn.ts"
    assert plugin_file.exists(), f"plugin not written at {plugin_file}"
    plugin_text = plugin_file.read_text()
    assert str(vault) in plugin_text, "vault path not substituted in plugin"
    assert "__CAIRN_VAULT__" not in plugin_text, "placeholder not replaced"

    assert (base / "commands" / "recall.md").exists(), "recall.md not written"
    assert (base / "commands" / "remember.md").exists(), "remember.md not written"


_PLUGIN_HOSTS = ["claude-code", "codex", "antigravity"]


@pytest.mark.parametrize("host_id", _PLUGIN_HOSTS)
def test_install_plugin_host_prints_command(host_id, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # antigravity's `agy plugin install` takes a local directory (not a git repo),
    # so --print requires --source; use a dummy path that lets us see the rendered command.
    extra_args = []
    if host_id == "antigravity":
        source = str(tmp_path / "agentcairn-plugin")
        extra_args = ["--source", source]
    res = runner.invoke(app, ["install", host_id, "--print"] + extra_args)
    assert res.exit_code == 0, res.output
    if host_id == "antigravity":
        # plugin_add is ("plugin", "install", "{source}"); assert the cli AND that
        # the {source} substitution actually landed in the rendered command.
        assert "agy plugin install" in res.output, res.output
        assert source in res.output, f"antigravity: --source not substituted: {res.output!r}"
    else:
        # claude-code and codex hardcode "agentcairn@agentcairn" in their plugin_add argv.
        assert "agentcairn" in res.output, f"{host_id}: missing in output: {res.output!r}"
