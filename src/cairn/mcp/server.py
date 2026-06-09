# SPDX-License-Identifier: Apache-2.0
"""FastMCP server exposing agentcairn's memory tools. Reads config from env so
`uvx agentcairn` / MCP clients can point it at a vault + index. Thin wrapper —
real logic lives in cairn.mcp.tools."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from cairn.mcp import tools

_DEFAULT_INDEX = str(Path.home() / ".cache" / "agentcairn" / "index.duckdb")


def build_server(*, vault: str | None = None, index: str | None = None) -> FastMCP:
    vault = vault or os.environ.get("CAIRN_VAULT")
    index = index or os.environ.get("CAIRN_INDEX") or _DEFAULT_INDEX
    mcp = FastMCP("agentcairn")

    @mcp.tool()
    def search(query: str, k: int = 10, rerank: bool = False) -> dict:
        """Hybrid search over memory; returns a compact id+snippet index."""
        return tools.search_tool(index, query, k=k, rerank=rerank)

    @mcp.tool()
    def recall(query: str, k: int = 5) -> dict:
        """Search then hydrate the top-k notes' full text."""
        return tools.recall_tool(index, query, k=k)

    @mcp.tool()
    def build_context(permalink: str) -> dict:
        """Return a note plus its 1-hop linked neighbors."""
        return tools.build_context_tool(index, permalink)

    @mcp.tool()
    def recent(n: int = 10) -> dict:
        """List the most-recently-modified notes."""
        return tools.recent_tool(index, n=n)

    @mcp.tool()
    def remember(text: str, title: str | None = None, tags: list[str] | None = None) -> dict:
        """Persist a distilled memory (redacted, non-lossy) into the vault."""
        if not vault:
            raise ValueError("remember requires CAIRN_VAULT (or --vault) to be set")
        return tools.remember_tool(vault, text, title=title, tags=tags)

    return mcp


def main() -> None:  # pragma: no cover - stdio entrypoint
    build_server().run()


if __name__ == "__main__":  # pragma: no cover
    main()
