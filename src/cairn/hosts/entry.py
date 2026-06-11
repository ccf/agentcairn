# SPDX-License-Identifier: Apache-2.0
"""The canonical agentcairn MCP server entry, shared by every host writer and --print."""

from __future__ import annotations


def mcp_entry(vault: str, index: str) -> dict:
    """The MCP server config agentcairn writes into a host: `uvx agentcairn` with
    CAIRN_VAULT/CAIRN_INDEX. `vault`/`index` should already be absolute paths."""
    return {
        "command": "uvx",
        "args": ["agentcairn"],
        "env": {"CAIRN_VAULT": vault, "CAIRN_INDEX": index},
    }
