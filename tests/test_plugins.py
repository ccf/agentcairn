# tests/test_plugins.py
# SPDX-License-Identifier: Apache-2.0
import tomllib

import pytest

from cairn.hosts import get_host
from cairn.hosts.plugins import install_plugin, migrate_codex_mcp_block


def test_install_plugin_dry_emits_commands():
    out = install_plugin(get_host("codex"), source="ccf/agentcairn", dry=True)
    assert "codex plugin marketplace add ccf/agentcairn" in out
    assert "codex plugin add agentcairn@agentcairn" in out


def test_install_plugin_claude_dry_uses_install_at_marketplace():
    out = install_plugin(get_host("claude-code"), source="ccf/agentcairn", dry=True)
    assert "claude plugin marketplace add ccf/agentcairn" in out
    assert "claude plugin install agentcairn@agentcairn" in out


def test_install_plugin_errors_when_cli_absent(monkeypatch):
    import cairn.hosts.plugins as pl

    monkeypatch.setattr(pl.shutil, "which", lambda c: None)
    with pytest.raises(ValueError, match="codex"):
        install_plugin(get_host("codex"), source="ccf/agentcairn", dry=False)


def test_install_plugin_runs_commands_in_order(monkeypatch):
    import cairn.hosts.plugins as pl

    monkeypatch.setattr(pl.shutil, "which", lambda c: "/usr/bin/codex")
    calls = []

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def _run(argv, **kw):
        calls.append(argv)
        return _R()

    monkeypatch.setattr(pl.subprocess, "run", _run)
    install_plugin(get_host("codex"), source="ccf/agentcairn", dry=False)
    assert calls[0] == ["codex", "plugin", "marketplace", "add", "ccf/agentcairn"]
    assert calls[1] == ["codex", "plugin", "add", "agentcairn@agentcairn"]


def test_install_plugin_raises_on_nonzero(monkeypatch):
    import cairn.hosts.plugins as pl

    monkeypatch.setattr(pl.shutil, "which", lambda c: "/usr/bin/codex")

    class _R:
        returncode = 1
        stdout = ""
        stderr = "boom: marketplace unreachable"

    monkeypatch.setattr(pl.subprocess, "run", lambda argv, **kw: _R())
    with pytest.raises(ValueError, match="boom"):
        install_plugin(get_host("codex"), source="ccf/agentcairn", dry=False)


def test_migrate_codex_removes_block_preserving_rest(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(
        '# my codex config\nmodel = "gpt-5"\n\n'
        '[mcp_servers.other]\ncommand = "npx"\n\n'
        '[mcp_servers.agentcairn]\ncommand = "uvx"\nargs = ["agentcairn"]\n'
    )
    note = migrate_codex_mcp_block(p, dry=False)
    assert note is not None
    doc = tomllib.loads(p.read_text())
    assert "agentcairn" not in doc.get("mcp_servers", {})
    assert doc["mcp_servers"]["other"] == {"command": "npx"}
    assert "# my codex config" in p.read_text()
    assert p.with_name("config.toml.bak").exists()


def test_migrate_codex_noop_when_absent(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('model = "gpt-5"\n')
    assert migrate_codex_mcp_block(p, dry=False) is None
    assert not p.with_name("config.toml.bak").exists()


def test_migrate_codex_missing_file_is_noop(tmp_path):
    assert migrate_codex_mcp_block(tmp_path / "nope.toml", dry=False) is None


def test_install_plugin_antigravity_single_command():
    out = install_plugin(get_host("antigravity"), source="ccf/agentcairn", dry=True)
    assert out == "agy plugin install ccf/agentcairn"


def test_migrate_antigravity_removes_entry_preserving_rest(tmp_path):
    import json as _j

    from cairn.hosts.plugins import migrate_antigravity_mcp_block

    p = tmp_path / "mcp_config.json"
    p.write_text(
        _j.dumps(
            {
                "theme": "dark",
                "mcpServers": {"other": {"command": "x"}, "agentcairn": {"command": "uvx"}},
            }
        )
    )
    note = migrate_antigravity_mcp_block(p, dry=False)
    assert note is not None
    data = _j.loads(p.read_text())
    assert "agentcairn" not in data["mcpServers"]
    assert data["mcpServers"]["other"] == {"command": "x"}
    assert data["theme"] == "dark"
    assert p.with_name("mcp_config.json.bak").exists()


def test_migrate_antigravity_noop_and_missing(tmp_path):
    import json as _j

    from cairn.hosts.plugins import migrate_antigravity_mcp_block

    assert migrate_antigravity_mcp_block(tmp_path / "nope.json", dry=False) is None
    p = tmp_path / "mcp_config.json"
    p.write_text(_j.dumps({"mcpServers": {"other": {"command": "x"}}}))
    assert migrate_antigravity_mcp_block(p, dry=False) is None
    assert not p.with_name("mcp_config.json.bak").exists()
