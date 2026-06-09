# SPDX-License-Identifier: Apache-2.0
import asyncio


def test_server_registers_all_tools():
    from cairn.mcp.server import build_server

    mcp = build_server(vault="/tmp/vault", index="/tmp/i.duckdb")
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"remember", "search", "recall", "build_context", "recent"} <= names
