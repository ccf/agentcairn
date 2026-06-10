# SPDX-License-Identifier: Apache-2.0
"""Validate the Claude Code plugin's static assets (no network)."""

import json
import os
import subprocess
from pathlib import Path

import pytest

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


def _run_hook(script, stdin_obj, env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.run(
        ["sh", str(PLUGIN / "scripts" / script), env["VAULT_ARG"], env["INDEX_ARG"]],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
    )


def test_session_start_emits_valid_json_with_memories(tmp_path, monkeypatch):
    # Stub `uvx` so `uvx --from ... cairn recent --json` returns canned notes — no network.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    stub.write_text(
        "#!/bin/sh\n"
        "# echo canned recent JSON regardless of args\n"
        'echo \'{"notes":[{"permalink":"a","title":"Fixed login","path":"a.md"}]}\'\n'
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()  # exists → init path is skipped
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),
        },
    )
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Fixed login" in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_empty_emits_nothing(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    stub.write_text("#!/bin/sh\necho '{\"notes\":[]}'\n")
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),
        },
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # no context when no memories


def test_session_end_runs_and_exits_zero(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    stub.write_text("#!/bin/sh\necho swept; exit 0\n")
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    r = _run_hook(
        "session-end.sh",
        {"hook_event_name": "SessionEnd", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),
        },
    )
    assert r.returncode == 0


def test_skill_has_valid_frontmatter():
    text = (PLUGIN / "skills" / "using-agentcairn-memory" / "SKILL.md").read_text()
    assert text.startswith("---")
    head = text.split("---", 2)[1]
    assert "name:" in head and "description:" in head


@pytest.mark.parametrize("cmd", ["recall", "remember", "memory", "ingest"])
def test_command_has_frontmatter(cmd):
    text = (PLUGIN / "commands" / f"{cmd}.md").read_text()
    assert text.startswith("---")
    assert "description:" in text.split("---", 2)[1]
