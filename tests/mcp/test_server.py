# SPDX-License-Identifier: Apache-2.0
import asyncio


def test_server_registers_all_tools():
    from cairn.mcp.server import build_server

    mcp = build_server(vault="/tmp/vault", index="/tmp/i.duckdb")
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"remember", "search", "recall", "build_context", "recent"} <= names


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
    """Falls back to ~/.cache/agentcairn/index.duckdb when nothing is set."""
    from pathlib import Path

    from cairn.mcp.server import resolve_config

    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    monkeypatch.delenv("CAIRN_EMBEDDER", raising=False)
    _, index, _ = resolve_config(index=None)
    expected = str(Path.home() / ".cache" / "agentcairn" / "index.duckdb")
    assert index == expected


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
