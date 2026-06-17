# SPDX-License-Identifier: Apache-2.0
"""The canonical agentcairn MCP server entry, shared by every host writer and --print."""

from __future__ import annotations


def mcp_entry(vault: str) -> dict:
    """The MCP server config agentcairn writes into a host: `uvx agentcairn` with
    CAIRN_VAULT. The index is derived from the vault, so CAIRN_INDEX is not pinned."""
    return {"command": "uvx", "args": ["agentcairn"], "env": {"CAIRN_VAULT": vault}}
