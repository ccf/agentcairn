# SPDX-License-Identifier: Apache-2.0
"""The canonical agentcairn MCP server entry, shared by every host writer and --print."""

from __future__ import annotations


def mcp_entry(vault: str) -> dict:
    """The MCP server config agentcairn writes into a host: `uvx agentcairn` with
    CAIRN_VAULT. The index is derived from the vault, so CAIRN_INDEX is not pinned."""
    return {"command": "uvx", "args": ["agentcairn"], "env": {"CAIRN_VAULT": vault}}


def opencode_mcp_entry(vault: str) -> dict:
    """OpenCode MCP entry shape: {type,command,enabled} with 'environment' for env vars.

    OpenCode uses `mcp.<name>` (not `mcpServers`) with per-server shape
    {"type":"local","command":[...],"enabled":true,"environment":{...}}.
    The command invocation is identical to the standard entry; only the wrapper differs.
    OpenCode uses the key 'environment' (not 'env') for environment variables.
    """
    std = mcp_entry(vault)
    cmd = [std["command"], *std.get("args", [])]
    out: dict = {"type": "local", "command": cmd, "enabled": True}
    if std.get("env"):
        out["environment"] = std["env"]
    return out
