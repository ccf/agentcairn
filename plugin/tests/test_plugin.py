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


def test_marketplace_has_required_owner():
    # Claude Code's marketplace schema requires a top-level `owner` object with a
    # name — without it, `claude plugin marketplace add` fails to parse and the
    # plugin is uninstallable for everyone.
    mkt = _json(ROOT / ".claude-plugin" / "marketplace.json")
    assert isinstance(mkt.get("owner"), dict)
    assert mkt["owner"].get("name")


def test_plugin_manifest_valid():
    man = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert man["name"] == "agentcairn"
    assert "vault_path" in man["userConfig"]


def test_plugin_manifest_does_not_reexplicit_autodiscovered_hooks():
    # Claude Code AUTO-DISCOVERS plugin/hooks/hooks.json. The manifest's `hooks`
    # field is only for ADDITIONAL hook files; pointing it back at the standard
    # hooks/hooks.json triggers a "Duplicate hooks file" load failure (the plugin
    # shows "failed to load"). So the manifest must not reference it, and the file
    # must still exist for auto-discovery. (mcpServers IS referenced explicitly —
    # MCP config has no auto-discovery, so that one is correct.)
    man = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert man.get("hooks") != "./hooks/hooks.json"
    assert (PLUGIN / "hooks" / "hooks.json").exists()


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
    vault.mkdir()
    (tmp_path / "i.duckdb").write_text("")  # index exists → digest path (not first-run)
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
    # savings --oneline prints nothing; recent returns empty notes list.
    stub.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "savings" ]; then exit 0; fi\n'
        '  if [ "$a" = "recent" ]; then echo \'{"notes":[]}\'; exit 0; fi\n'
        "done\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    (tmp_path / "i.duckdb").write_text("")  # index exists but recall returns no notes
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


def test_session_start_first_run_no_index_exits_quietly(tmp_path):
    # First-ever run: no index file yet. The hook must NOT block on a cold `uvx`
    # install for an empty digest — it exits 0 immediately with no output and
    # warms the cache in the background. A stub `uvx` that would hang if called
    # synchronously proves the digest path is skipped.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    # If the hook blocked on this synchronously, the test would hang; it must not.
    stub.write_text("#!/bin/sh\nsleep 30\n")
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"  # does NOT exist yet
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),  # absent → first-run branch
        },
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # nothing to surface on first run


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


@pytest.mark.parametrize("cmd", ["recall", "remember", "memory", "ingest", "savings"])
def test_command_has_frontmatter(cmd):
    text = (PLUGIN / "commands" / f"{cmd}.md").read_text()
    assert text.startswith("---")
    assert "description:" in text.split("---", 2)[1]


def test_session_start_includes_savings_line(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    # Stub uvx: `cairn savings --oneline` -> a savings line; `cairn recent --json` -> one note.
    note_json = '{"notes":[{"permalink":"a","title":"Note A","path":"a.md"}]}'
    stub.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "savings" ]; then\n'
        '    echo "SAVED 1.2M tokens across 9 recalls"; exit 0\n'
        "  fi\n"
        '  if [ "$a" = "recent" ]; then\n'
        f"    echo '{note_json}'; exit 0\n"
        "  fi\n"
        "done\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    (tmp_path / "i.duckdb").write_text("")  # index exists -> digest path
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
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SAVED 1.2M tokens across 9 recalls" in ctx
    assert "Note A" in ctx  # the recent digest is still present


def test_session_start_first_run_warms_models():
    text = (PLUGIN / "scripts" / "session-start.sh").read_text()
    # The first-run detached job pre-warms the models (after vault init) so the
    # first sweep/recall isn't slow.
    assert "$CAIRN warm" in text


def test_session_start_no_savings_line_when_empty(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    # savings --oneline prints nothing (no data); recent returns one note.
    note_json = '{"notes":[{"permalink":"a","title":"Note A","path":"a.md"}]}'
    stub.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "savings" ]; then exit 0; fi\n'
        '  if [ "$a" = "recent" ]; then\n'
        f"    echo '{note_json}'; exit 0\n"
        "  fi\n"
        "done\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    (tmp_path / "i.duckdb").write_text("")
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
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SAVED" not in ctx
    assert "Note A" in ctx
