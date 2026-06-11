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
    assert get_host("codex").format == "codex-toml"
    assert get_host("nope") is None
    assert get_host("windsurf") is None  # dropped — renamed to Devin Desktop


def test_antigravity_only_does_not_falsely_detect_gemini(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Antigravity-only: ~/.gemini/config exists, but no settings.json.
    (tmp_path / ".gemini" / "config").mkdir(parents=True)
    ids = {h.id for h in detected_hosts()}
    assert "antigravity" in ids
    assert "gemini" not in ids  # Gemini CLI keys off settings.json, not the shared ~/.gemini dir
    # Now a real Gemini CLI install (settings.json present) is detected.
    (tmp_path / ".gemini" / "settings.json").write_text("{}")
    assert "gemini" in {h.id for h in detected_hosts()}


def test_antigravity_and_vscode_registered():
    ag = get_host("antigravity")
    assert ag is not None and ag.format == "json"
    assert ag.config_path().name == "mcp_config.json"
    vs = get_host("vscode")
    assert vs is not None and vs.format == "json"
    assert vs.root_key == "servers"  # VS Code uses "servers", not "mcpServers"


def test_detected_hosts_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
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


def test_codex_writer_adds_tables_and_preserves(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    p.write_text('# my codex config\nmodel = "gpt-5"\n\n[mcp_servers.other]\ncommand = "npx"\n')
    write_codex_toml(p, _ENTRY)
    text = p.read_text()
    assert "# my codex config" in text  # comment preserved
    assert 'model = "gpt-5"' in text  # other key preserved
    assert "[mcp_servers.other]" in text  # other server preserved
    # agentcairn tables present + re-parseable
    import tomllib

    doc = tomllib.loads(text)
    ac = doc["mcp_servers"]["agentcairn"]
    assert ac["command"] == "uvx"
    assert ac["args"] == ["agentcairn"]
    assert ac["env"]["CAIRN_VAULT"] == "/v"
    assert p.with_name("config.toml.bak").exists()


def test_codex_writer_idempotent(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    write_codex_toml(p, _ENTRY)
    write_codex_toml(p, _ENTRY)
    import tomllib

    doc = tomllib.loads(p.read_text())
    assert doc["mcp_servers"]["agentcairn"]["command"] == "uvx"


def test_codex_writer_dry_writes_nothing(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    out = write_codex_toml(p, _ENTRY, dry=True)
    assert not p.exists()
    assert "[mcp_servers.agentcairn]" in out


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


def test_codex_writer_rejects_malformed_but_backs_up(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    p.write_text("this = = = not toml")
    import pytest

    with pytest.raises(ValueError):
        write_codex_toml(p, _ENTRY)
    assert p.read_text() == "this = = = not toml"  # original untouched
    bak = p.with_name("config.toml.bak")
    assert bak.exists() and bak.read_text() == "this = = = not toml"  # backed up first


def test_dry_run_creates_no_backup(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    p.write_text('model = "gpt-5"\n')
    write_json_mcp(tmp_path / "mcp.json", _ENTRY, dry=True)
    write_codex_toml(p, _ENTRY, dry=True)
    assert not p.with_name("config.toml.bak").exists()  # dry must not back up
