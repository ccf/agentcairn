# tests/test_plugin_assets.py
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def test_codex_manifest_valid_and_pointers_resolve():
    m = _load(PLUGIN / ".codex-plugin" / "plugin.json")
    assert m["name"] == "agentcairn"
    assert m["mcpServers"] == "./.mcp.codex.json"
    assert m["hooks"] == "./hooks/hooks.codex.json"
    assert m["skills"] == "./skills/"
    assert (PLUGIN / ".mcp.codex.json").is_file()
    assert (PLUGIN / "hooks" / "hooks.codex.json").is_file()
    assert (PLUGIN / "skills").is_dir()
    assert m["interface"]["displayName"] == "agentcairn"


def test_codex_mcp_is_bare_map_with_vault_env():
    mcp = _load(PLUGIN / ".mcp.codex.json")
    assert "mcpServers" not in mcp
    ac = mcp["agentcairn"]
    assert ac["command"] == "uvx" and ac["args"] == ["agentcairn"]
    assert ac["env"]["CAIRN_VAULT"] == "~/agentcairn"
    assert ac["env"]["CAIRN_INDEX"] == "~/.cache/agentcairn/index.duckdb"


def test_codex_hooks_reference_existing_scripts():
    h = _load(PLUGIN / "hooks" / "hooks.codex.json")
    starts = h["hooks"]["SessionStart"][0]["hooks"][0]["args"]
    ends = h["hooks"]["SessionEnd"][0]["hooks"][0]["args"]
    assert starts == ["${PLUGIN_ROOT}/scripts/session-start.sh"]
    assert ends == ["${PLUGIN_ROOT}/scripts/session-end.sh"]
    assert (PLUGIN / "scripts" / "session-start.sh").is_file()
    assert (PLUGIN / "scripts" / "session-end.sh").is_file()


def test_codex_marketplace_lists_plugin_with_local_source():
    mk = _load(ROOT / ".agents" / "plugins" / "marketplace.json")
    plug = mk["plugins"][0]
    assert plug["name"] == "agentcairn"
    assert plug["source"] == {"source": "local", "path": "./plugin"}
    assert (ROOT / "plugin").is_dir()


def test_antigravity_manifest_valid():
    m = _load(PLUGIN / "plugin.json")
    assert m["name"] == "agentcairn"
    assert "version" in m and "description" in m
    assert (PLUGIN / "skills").is_dir()


def test_antigravity_mcp_config_is_wrapper_with_vault_env():
    mcp = _load(PLUGIN / "mcp_config.json")
    ac = mcp["mcpServers"]["agentcairn"]
    assert ac["command"] == "uvx" and ac["args"] == ["agentcairn"]
    assert ac["env"]["CAIRN_VAULT"] == "~/agentcairn"
    assert ac["env"]["CAIRN_INDEX"] == "~/.cache/agentcairn/index.duckdb"


def test_bundled_cursor_skill_matches_plugin_copy():
    # The CLI installs this package-data copy into ~/.cursor/skills; it must stay
    # byte-identical to the canonical plugin/ copy so the two never drift.
    pkg_copy = ROOT / "src" / "cairn" / "assets" / "using-agentcairn-memory" / "SKILL.md"
    plugin_copy = PLUGIN / "skills" / "using-agentcairn-memory" / "SKILL.md"
    assert pkg_copy.read_bytes() == plugin_copy.read_bytes()
