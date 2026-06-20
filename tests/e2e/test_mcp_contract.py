# SPDX-License-Identifier: Apache-2.0
import asyncio
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _seed_vault(tmp_path: Path) -> tuple[Path, Path]:
    """Create a minimal vault + a pre-built DuckDB index in tmp_path."""
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "note.md").write_text(
        "---\ntitle: Pin DuckDB to 1.1\npermalink: pin-duckdb\n---\nWe pinned DuckDB to 1.1.\n"
    )
    index_path = tmp_path / "index.duckdb"
    # Build the index so the server can open it at startup (tools.py:_open requires it).
    result = subprocess.run(
        [
            "uv",
            "run",
            "cairn",
            "reindex",
            str(vault),
            "--embedder",
            "fake",
            "--index",
            str(index_path),
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"cairn reindex failed (rc={result.returncode}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return vault, index_path


@pytest.mark.skipif(
    not os.environ.get("CAIRN_E2E"),
    reason="set CAIRN_E2E=1 for the stdio MCP contract test",
)
def test_mcp_stdio_contract(tmp_path):
    try:
        from mcp.client.stdio import stdio_client

        from mcp import ClientSession, StdioServerParameters
    except Exception as exc:
        pytest.skip(f"mcp client unavailable: {exc}")

    vault, index_path = _seed_vault(tmp_path)

    server_env = dict(
        os.environ,
        CAIRN_VAULT=str(vault),
        CAIRN_INDEX=str(index_path),
        CAIRN_EMBEDDER="fake",
    )
    params = StdioServerParameters(
        command="uv",
        args=["run", "agentcairn"],
        env=server_env,
        cwd=str(REPO),
    )

    async def _run():
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = {t.name for t in (await session.list_tools()).tools}
                assert {"recall", "search", "recent", "build_context", "remember"} <= tools, tools
                result = await session.call_tool("recall", {"query": "why is DuckDB pinned"})
                text = " ".join(getattr(c, "text", "") for c in result.content).lower()
                return text

    out = asyncio.run(_run())
    assert "duckdb" in out, f"recall did not surface the seeded note over stdio: {out!r}"
