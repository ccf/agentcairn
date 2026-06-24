# SPDX-License-Identifier: Apache-2.0
"""Validate the Claude Code plugin's static assets (no network)."""

import json
import os
import subprocess
import time
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


def _run_hook(script, stdin_obj, env_extra, cwd=None, first_run=False):
    env = {**os.environ, **env_extra}
    # The index is now vault-derived, so session-start.sh detects a first run via the
    # cache dir ($HOME/.cache/agentcairn/indexes), NOT the (now-ignored) $2 index arg.
    # Pin HOME to the test's tmp dir so the probe is deterministic instead of inheriting
    # the CI runner's real home, and pre-create the cache dir unless we're testing the
    # genuine first-run branch.
    home = Path(env["VAULT_ARG"]).parent
    env["HOME"] = str(home)
    # Claude Code exports the user's `vault_path` userConfig as this env var; the
    # scripts read it (falling back to the legacy $1 arg, then ~/agentcairn).
    env["CLAUDE_PLUGIN_OPTION_VAULT_PATH"] = env["VAULT_ARG"]
    if not first_run:
        (home / ".cache" / "agentcairn" / "indexes").mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        ["sh", str(PLUGIN / "scripts" / script), env["VAULT_ARG"], env["INDEX_ARG"]],
        input=json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
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
            "INDEX_ARG": str(tmp_path / "i.duckdb"),  # ignored; first-run is detected via cache dir
        },
        first_run=True,  # no cache dir → genuine first-run branch (warm detached, exit quietly)
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
    workdir = tmp_path / "work"  # isolated cwd: detect stray redirection files
    workdir.mkdir()
    r = _run_hook(
        "session-end.sh",
        {"hook_event_name": "SessionEnd", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),
        },
        cwd=workdir,
    )
    assert r.returncode == 0
    # The detached sweep must NOT re-parse the `agentcairn>=0.2` pin through an
    # inner shell: that turns `>=0.2` into a redirection, dropping the version
    # pin and creating a junk file named `=0.2` in the cwd.
    time.sleep(0.5)  # give the detached child time to (mis)behave
    assert not (workdir / "=0.2").exists(), "version pin re-parsed as a shell redirection"
    assert list(workdir.iterdir()) == [], "session-end.sh littered files into the cwd"


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


def test_hooks_do_not_hardfail_on_unset_vault_path():
    # Hooks must NOT interpolate ${user_config.vault_path} as a command arg:
    # Claude Code hard-fails that interpolation when the user never set the option
    # (fresh install — the schema `default` is not applied to it). The scripts
    # resolve the vault from $CLAUDE_PLUGIN_OPTION_VAULT_PATH (with a ~/agentcairn
    # fallback) instead, so a zero-config install just works.
    hooks = _json(PLUGIN / "hooks" / "hooks.json")
    blob = json.dumps(hooks)
    assert "${user_config.vault_path}" not in blob
    assert "index_path" not in blob  # the index is vault-derived; no index arg
    assert "session-start.sh" in blob and "session-end.sh" in blob


def test_precompact_hook_captures_long_sessions():
    # Capture must not wait for SessionEnd: long/resumed sessions compact
    # repeatedly, so PreCompact runs the same detached sweep at each boundary,
    # before context is discarded. Without it, the whole session goes uncaptured
    # until it formally ends (the 2026-06-19 dogfood gap).
    hooks = _json(PLUGIN / "hooks" / "hooks.json")["hooks"]
    assert "PreCompact" in hooks, "no PreCompact hook — long sessions won't be captured"
    blob = json.dumps(hooks["PreCompact"])
    assert "session-end.sh" in blob, "PreCompact should reuse the detached-sweep script"
    assert "${user_config.vault_path}" not in blob  # no hard-failing interpolation


def test_plugin_manifest_drops_index_path_and_bumps_version():
    man = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert "index_path" not in man["userConfig"]  # removed
    assert "vault_path" in man["userConfig"]  # still present
    assert man["version"] != "0.1.0"  # bumped so `claude plugin update` ships the fix


def test_mcp_manifests_have_no_cairn_index():
    """The index is vault-derived; no plugin MCP manifest may pin CAIRN_INDEX."""
    for rel in (".mcp.json", ".mcp.codex.json", "mcp_config.json"):
        data = _json(PLUGIN / rel)
        blob = json.dumps(data)
        assert "CAIRN_INDEX" not in blob, f"{rel} still pins CAIRN_INDEX"
        assert "CAIRN_VAULT" in blob, f"{rel} must still set CAIRN_VAULT"


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
