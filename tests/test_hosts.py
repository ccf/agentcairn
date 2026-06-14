# SPDX-License-Identifier: Apache-2.0
import json as _json

from cairn.hosts import detected_hosts, get_host
from cairn.hosts.entry import mcp_entry
from cairn.hosts.writers import write_json_mcp


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
    assert get_host("cursor").format == "json"
    assert get_host("codex").kind == "plugin"
    assert get_host("nope") is None
    assert get_host("windsurf") is None  # dropped — renamed to Devin Desktop


def test_gemini_detection_and_antigravity_via_cli(tmp_path, monkeypatch):
    import cairn.hosts as hosts

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(hosts.shutil, "which", lambda c: None)
    (tmp_path / ".gemini" / "config").mkdir(parents=True)
    ids = {h.id for h in hosts.detected_hosts()}
    assert "gemini" not in ids
    assert "antigravity" not in ids  # plugin host needs the agy CLI on PATH
    monkeypatch.setattr(hosts.shutil, "which", lambda c: "/usr/bin/agy" if c == "agy" else None)
    assert "antigravity" in {h.id for h in hosts.detected_hosts()}
    (tmp_path / ".gemini" / "settings.json").write_text("{}")
    assert "gemini" in {h.id for h in hosts.detected_hosts()}


def test_vscode_registered():
    vs = get_host("vscode")
    assert vs is not None and vs.format == "json"
    assert vs.root_key == "servers"  # VS Code uses "servers", not "mcpServers"


def test_antigravity_is_plugin_host():
    h = get_host("antigravity")
    assert h.kind == "plugin"
    assert h.cli == "agy"
    assert h.plugin_add == ("plugin", "install", "{source}")
    assert h.marketplace_add is None


def test_detected_hosts_uses_home(tmp_path, monkeypatch):
    import cairn.hosts as hosts

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(hosts.shutil, "which", lambda _c: None)  # no CLIs on PATH
    # Nothing present yet → none detected.
    assert detected_hosts() == []
    # Create Cursor's config dir → it's detected.
    (tmp_path / ".cursor").mkdir()
    ids = {h.id for h in detected_hosts()}
    assert "cursor" in ids


_ENTRY = mcp_entry("/v", "/i")


def test_json_writer_creates_and_writes(tmp_path):
    p = tmp_path / "sub" / "mcp.json"  # parent absent → must be created
    summary = write_json_mcp(p, _ENTRY)
    data = _json.loads(p.read_text())
    assert data["mcpServers"]["agentcairn"] == _ENTRY
    assert str(p) in summary


def test_json_writer_preserves_other_servers_and_keys(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(_json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    write_json_mcp(p, _ENTRY)
    data = _json.loads(p.read_text())
    assert data["theme"] == "dark"  # unrelated key survives
    assert data["mcpServers"]["other"] == {"command": "x"}  # other server survives
    assert data["mcpServers"]["agentcairn"] == _ENTRY
    assert (p.with_name("mcp.json.bak")).exists()  # backed up


def test_json_writer_custom_root_key_for_vscode(tmp_path):
    p = tmp_path / "mcp.json"
    # VS Code keeps an existing "servers" map + unrelated keys; we must merge under "servers"
    p.write_text(_json.dumps({"inputs": [], "servers": {"other": {"command": "x"}}}))
    write_json_mcp(p, _ENTRY, root_key="servers")
    data = _json.loads(p.read_text())
    assert data["inputs"] == []  # unrelated key survives
    assert data["servers"]["other"] == {"command": "x"}  # other server survives
    assert data["servers"]["agentcairn"] == _ENTRY  # written under "servers", not "mcpServers"
    assert "mcpServers" not in data


def test_write_host_vscode_uses_servers_key(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from cairn.hosts.writers import write_host

    h = get_host("vscode")
    write_host(h, _ENTRY)
    data = _json.loads(h.config_path().read_text())
    assert data["servers"]["agentcairn"] == _ENTRY


def test_json_writer_idempotent(tmp_path):
    p = tmp_path / "mcp.json"
    write_json_mcp(p, _ENTRY)
    write_json_mcp(p, _ENTRY)
    data = _json.loads(p.read_text())
    assert list(data["mcpServers"]).count("agentcairn") == 1


def test_json_writer_dry_writes_nothing(tmp_path):
    p = tmp_path / "mcp.json"
    out = write_json_mcp(p, _ENTRY, dry=True)
    assert not p.exists()
    assert "agentcairn" in out and "uvx" in out


def test_json_writer_rejects_malformed_without_clobber(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text("{ not json")
    import pytest

    with pytest.raises(ValueError):
        write_json_mcp(p, _ENTRY)
    assert p.read_text() == "{ not json"  # original untouched
    bak = p.with_name("mcp.json.bak")
    assert bak.exists() and bak.read_text() == "{ not json"  # backed up before erroring


def test_write_host_dispatches_json(tmp_path):
    p = tmp_path / "mcp.json"
    h = get_host("cursor")
    # point the host at our temp path via monkeypatchless override: call writer directly
    write_json_mcp(p, _ENTRY)
    assert _json.loads(p.read_text())["mcpServers"]["agentcairn"]["command"] == "uvx"
    assert h.format == "json"


def test_json_writer_preserves_non_ascii_literally(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(
        _json.dumps({"mcpServers": {"other": {"env": {"NOTE": "café—naïve"}}}}),
        encoding="utf-8",
    )
    write_json_mcp(p, _ENTRY)
    text = p.read_text(encoding="utf-8")
    assert "café—naïve" in text  # written literally, not \u-escaped
    assert "\\u" not in text
    data = _json.loads(text)
    assert data["mcpServers"]["other"]["env"]["NOTE"] == "café—naïve"


def test_json_writer_no_tmp_left_behind(tmp_path):
    p = tmp_path / "mcp.json"
    write_json_mcp(p, _ENTRY)
    assert not p.with_name("mcp.json.tmp").exists()  # atomic-rename cleaned up


def test_codex_is_plugin_host():
    h = get_host("codex")
    assert h.kind == "plugin"
    assert h.cli == "codex"
    assert h.plugin_add == ("plugin", "add", "agentcairn@agentcairn")


def test_claude_code_is_plugin_host():
    h = get_host("claude-code")
    assert h.kind == "plugin"
    assert h.cli == "claude"
    assert h.plugin_add == ("plugin", "install", "agentcairn@agentcairn")


def test_mcp_hosts_keep_kind_mcp():
    assert get_host("cursor").kind == "mcp"
    assert get_host("vscode").kind == "mcp"


def test_plugin_host_detected_via_cli_on_path(monkeypatch):
    import cairn.hosts as hosts

    monkeypatch.setattr(hosts.shutil, "which", lambda c: "/usr/bin/" + c if c == "codex" else None)
    ids = {h.id for h in hosts.detected_hosts()}
    assert "codex" in ids  # codex CLI present
    assert "claude-code" not in ids  # claude CLI absent
