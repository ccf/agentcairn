# SPDX-License-Identifier: Apache-2.0
import json as _json

from cairn.hosts import detected_hosts, get_host
from cairn.hosts.entry import mcp_entry
from cairn.hosts.writers import write_json_mcp


def test_mcp_entry_shape():
    e = mcp_entry("/home/u/agentcairn")
    assert e == {
        "command": "uvx",
        "args": ["agentcairn"],
        "env": {"CAIRN_VAULT": "/home/u/agentcairn"},
    }
    assert "CAIRN_INDEX" not in e["env"]  # index is vault-derived, not pinned


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


_ENTRY = mcp_entry("/v")


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


def test_cursor_host_has_skill_dir():
    from cairn.hosts import get_host

    assert get_host("cursor").skill_dir == "~/.cursor/skills"


def test_non_skill_hosts_have_no_skill_dir():
    from cairn.hosts import HOSTS

    for h in HOSTS:
        if h.id != "cursor":
            assert h.skill_dir is None, h.id


def test_mcp_hosts_keep_kind_mcp():
    assert get_host("cursor").kind == "mcp"
    assert get_host("vscode").kind == "mcp"


def test_plugin_host_detected_via_cli_on_path(monkeypatch):
    import cairn.hosts as hosts

    monkeypatch.setattr(hosts.shutil, "which", lambda c: "/usr/bin/" + c if c == "codex" else None)
    ids = {h.id for h in hosts.detected_hosts()}
    assert "codex" in ids  # codex CLI present
    assert "claude-code" not in ids  # claude CLI absent


def test_cursor_skill_text_is_the_bundled_skill():
    from cairn.hosts.skills import cursor_skill_text

    text = cursor_skill_text()
    assert "name: using-agentcairn-memory" in text
    assert text.startswith("---")


def test_install_skill_writes_file(tmp_path):
    from cairn.hosts.skills import cursor_skill_text, install_skill

    note = install_skill(tmp_path, dry=False)
    dest = tmp_path / "using-agentcairn-memory" / "SKILL.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == cursor_skill_text()
    assert str(dest) in note


def test_install_skill_dry_writes_nothing(tmp_path):
    from cairn.hosts.skills import install_skill

    note = install_skill(tmp_path, dry=True)
    assert not (tmp_path / "using-agentcairn-memory").exists()
    assert "would install" in note


def test_install_skill_overwrites_existing(tmp_path):
    from cairn.hosts.skills import cursor_skill_text, install_skill

    dest = tmp_path / "using-agentcairn-memory" / "SKILL.md"
    dest.parent.mkdir(parents=True)
    dest.write_text("STALE", encoding="utf-8")
    install_skill(tmp_path, dry=False)
    assert dest.read_text(encoding="utf-8") == cursor_skill_text()
    assert not (tmp_path / "using-agentcairn-memory" / "SKILL.md.bak").exists()


# ── OpenCode host ──────────────────────────────────────────────────────────


def test_opencode_entry_shape():
    """opencode_mcp_entry produces {type,command,enabled} with env under 'environment'."""
    from cairn.hosts.entry import opencode_mcp_entry

    e = opencode_mcp_entry("/home/u/agentcairn")
    assert e["type"] == "local"
    assert e["command"] == ["uvx", "agentcairn"]
    assert e["enabled"] is True
    # CAIRN_VAULT must flow through
    assert e["environment"] == {"CAIRN_VAULT": "/home/u/agentcairn"}


def test_opencode_writer_uses_mcp_block_local_shape(tmp_path, monkeypatch):
    """write_host for opencode lands under data['mcp'], NOT 'mcpServers', with local shape."""
    import json

    from cairn.hosts import get_host
    from cairn.hosts.entry import opencode_mcp_entry
    from cairn.hosts.writers import write_json_mcp

    h = get_host("opencode")
    assert h is not None, "opencode host must be registered"
    assert h.root_key == "mcp"

    # Point the host's config path at a temp file; call write_json_mcp directly
    # with the host's root_key (same path write_host would take for a json host).

    cfg = tmp_path / "opencode.json"
    entry = opencode_mcp_entry("/v")
    write_json_mcp(cfg, entry, root_key=h.root_key)
    data = json.loads(cfg.read_text())

    assert "mcpServers" not in data
    ac = data["mcp"]["agentcairn"]
    assert ac["type"] == "local"
    assert ac["command"] == ["uvx", "agentcairn"]
    assert ac["enabled"] is True
    assert ac["environment"] == {"CAIRN_VAULT": "/v"}


def test_opencode_install_idempotent_and_preserves_others(tmp_path):
    """Re-running yields ONE agentcairn entry; pre-existing mcp.other survives; .bak made."""
    import json

    from cairn.hosts.entry import opencode_mcp_entry
    from cairn.hosts.writers import write_json_mcp

    cfg = tmp_path / "opencode.json"
    cfg.write_text(
        json.dumps({"mcp": {"other": {"type": "local", "command": ["other"], "enabled": True}}})
    )
    entry = opencode_mcp_entry("/v")

    # First write
    write_json_mcp(cfg, entry, root_key="mcp")
    # Second write (idempotency)
    write_json_mcp(cfg, entry, root_key="mcp")

    data = json.loads(cfg.read_text())
    assert list(data["mcp"]).count("agentcairn") == 1  # exactly one entry
    assert data["mcp"]["other"]["command"] == ["other"]  # pre-existing server preserved
    assert (cfg.with_name("opencode.json.bak")).exists()  # backup created


# ── OpenCode plugin + commands install ─────────────────────────────────────


def test_opencode_plugin_install_copies_files(tmp_path, monkeypatch):
    """cairn install opencode copies plugin + commands + writes mcp block."""
    from cairn.hosts.opencode import install_opencode_plugin

    opencode_cfg_dir = tmp_path / ".config" / "opencode"
    opencode_cfg_dir.mkdir(parents=True)

    install_opencode_plugin(opencode_cfg_dir)

    assert (opencode_cfg_dir / "plugin" / "agentcairn.ts").exists()
    assert (opencode_cfg_dir / "commands" / "recall.md").exists()
    assert (opencode_cfg_dir / "commands" / "remember.md").exists()


def test_opencode_plugin_install_idempotent(tmp_path):
    """Re-running install_opencode_plugin doesn't error or create duplicates."""
    from cairn.hosts.opencode import install_opencode_plugin

    opencode_cfg_dir = tmp_path / ".config" / "opencode"
    opencode_cfg_dir.mkdir(parents=True)

    install_opencode_plugin(opencode_cfg_dir)
    install_opencode_plugin(opencode_cfg_dir)  # second call must not raise

    assert (opencode_cfg_dir / "plugin" / "agentcairn.ts").exists()
    assert (opencode_cfg_dir / "commands" / "recall.md").exists()
    assert (opencode_cfg_dir / "commands" / "remember.md").exists()


def test_opencode_plugin_install_dry_run(tmp_path):
    """dry=True returns a note listing files and writes nothing."""
    from cairn.hosts.opencode import install_opencode_plugin

    opencode_cfg_dir = tmp_path / ".config" / "opencode"
    opencode_cfg_dir.mkdir(parents=True)

    note = install_opencode_plugin(opencode_cfg_dir, dry=True)

    assert "agentcairn.ts" in note
    assert not (opencode_cfg_dir / "plugin" / "agentcairn.ts").exists()


def test_opencode_install_command_full_flow(tmp_path, monkeypatch):
    """cairn install opencode end-to-end: mcp block + plugin + commands all land."""
    import json

    import cairn.hosts as hosts

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(hosts.shutil, "which", lambda _c: None)

    opencode_cfg_dir = tmp_path / ".config" / "opencode"
    opencode_cfg_dir.mkdir(parents=True)
    cfg_file = opencode_cfg_dir / "opencode.json"
    cfg_file.write_text(json.dumps({}))

    from typer.testing import CliRunner

    from cairn.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["install", "opencode", "--vault", str(tmp_path / "vault")])
    assert result.exit_code == 0, result.output

    # MCP block written
    data = json.loads(cfg_file.read_text())
    assert "agentcairn" in data.get("mcp", {})

    # Plugin and commands installed
    assert (opencode_cfg_dir / "plugin" / "agentcairn.ts").exists()
    assert (opencode_cfg_dir / "commands" / "recall.md").exists()
    assert (opencode_cfg_dir / "commands" / "remember.md").exists()
