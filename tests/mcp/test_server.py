# SPDX-License-Identifier: Apache-2.0
import asyncio


def test_server_registers_all_tools():
    from cairn.mcp.server import build_server

    mcp = build_server(vault="/tmp/vault", index="/tmp/i.duckdb")
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"remember", "search", "recall", "build_context", "recent"} <= names


def test_search_and_recall_expose_project_and_scope():
    # Provenance-aware recall: the MCP-exposed search/recall handlers must surface
    # `project`/`scope` so agents can target a repo or hard-scope — not just rely
    # on the server process cwd.
    from cairn.mcp.server import build_server

    mcp = build_server(vault="/tmp/vault", index="/tmp/i.duckdb")
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    for name in ("search", "recall"):
        props = tools[name].inputSchema["properties"]
        assert "project" in props, f"{name} missing project param"
        assert "scope" in props, f"{name} missing scope param"


# ---------------------------------------------------------------------------
# Fix 2+3: resolve_config honors env vars and applies defaults
# ---------------------------------------------------------------------------


def test_resolve_config_index_from_env(monkeypatch):
    """CAIRN_INDEX env var is used when no explicit index is given."""
    from cairn.mcp.server import resolve_config

    monkeypatch.setenv("CAIRN_INDEX", "/tmp/x.duckdb")
    monkeypatch.delenv("CAIRN_EMBEDDER", raising=False)
    _, index, _ = resolve_config(index=None)
    assert index == "/tmp/x.duckdb"


def test_resolve_config_explicit_index_wins(monkeypatch):
    """Explicit index= argument beats CAIRN_INDEX env var."""
    from cairn.mcp.server import resolve_config

    monkeypatch.setenv("CAIRN_INDEX", "/tmp/env.duckdb")
    _, index, _ = resolve_config(index="/a/explicit.duckdb")
    assert index == "/a/explicit.duckdb"


def test_resolve_config_index_default(monkeypatch):
    """Falls back to vault-derived index (default_index(~/agentcairn)) when nothing is set."""
    from cairn import paths
    from cairn.mcp.server import resolve_config

    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    monkeypatch.delenv("CAIRN_VAULT", raising=False)
    monkeypatch.delenv("CAIRN_EMBEDDER", raising=False)
    _, index, _ = resolve_config(index=None)
    # No CAIRN_VAULT → vault defaults to ~/agentcairn; index derives from that vault.
    from pathlib import Path

    expected = str(paths.default_index(Path.home() / "agentcairn"))
    assert index == expected


def test_resolve_config_expands_tilde(monkeypatch):
    """Leading ~ in CAIRN_VAULT/CAIRN_INDEX env is expanded to an absolute path
    (plugin user_config defaults arrive as literal "~/agentcairn")."""
    from pathlib import Path

    from cairn.mcp.server import resolve_config

    monkeypatch.setenv("CAIRN_VAULT", "~/agentcairn")
    monkeypatch.setenv("CAIRN_INDEX", "~/.cache/agentcairn/index.duckdb")
    vault, index, _ = resolve_config()
    assert vault == str(Path.home() / "agentcairn")
    assert index == str(Path.home() / ".cache" / "agentcairn" / "index.duckdb")
    assert "~" not in vault
    assert "~" not in index


def test_resolve_config_embedder_defaults_fastembed(monkeypatch):
    """Embedder defaults to 'fastembed' when CAIRN_EMBEDDER is absent."""
    from cairn.mcp.server import resolve_config

    monkeypatch.delenv("CAIRN_EMBEDDER", raising=False)
    _, _, embedder = resolve_config()
    assert embedder == "fastembed"


def test_resolve_config_embedder_from_env(monkeypatch):
    """CAIRN_EMBEDDER env var is respected."""
    from cairn.mcp.server import resolve_config

    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    _, _, embedder = resolve_config()
    assert embedder == "fake"


def test_resolve_config_embedder_explicit_wins(monkeypatch):
    """Explicit embedder= argument beats CAIRN_EMBEDDER env var."""
    from cairn.mcp.server import resolve_config

    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    _, _, embedder = resolve_config(embedder="none")
    assert embedder == "none"


# ---------------------------------------------------------------------------
# Task 5: resolve_config derives index from the resolved vault
# ---------------------------------------------------------------------------


def test_resolve_config_derives_index_from_vault(monkeypatch, tmp_path):
    from cairn import paths
    from cairn.mcp.server import resolve_config

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "v"))
    vault, index, _ = resolve_config()
    assert index == str(paths.default_index(tmp_path / "v"))


def test_resolve_config_index_env_still_wins(monkeypatch, tmp_path):
    from cairn.mcp.server import resolve_config

    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "v"))
    monkeypatch.setenv("CAIRN_INDEX", str(tmp_path / "explicit.duckdb"))
    _, index, _ = resolve_config()
    assert index == str(tmp_path / "explicit.duckdb")


def _tool_rerank_default(mcp, name):
    tools = asyncio.run(mcp.list_tools())
    t = next(t for t in tools if t.name == name)
    return t.inputSchema["properties"]["rerank"]["default"]


def test_server_rerank_default_on(monkeypatch):
    from cairn.mcp.server import build_server

    monkeypatch.delenv("CAIRN_RERANK", raising=False)
    mcp = build_server(index="/tmp/i.duckdb")
    assert _tool_rerank_default(mcp, "search") is True
    assert _tool_rerank_default(mcp, "recall") is True


def test_server_rerank_env_off(monkeypatch):
    from cairn.mcp.server import build_server

    monkeypatch.setenv("CAIRN_RERANK", "0")
    mcp = build_server(index="/tmp/i.duckdb")
    assert _tool_rerank_default(mcp, "search") is False
    assert _tool_rerank_default(mcp, "recall") is False
