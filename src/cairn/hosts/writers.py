# SPDX-License-Identifier: Apache-2.0
"""Merge the agentcairn MCP entry into a host config — non-destructive, idempotent,
backup-first. With dry=True, render the would-be file content and write nothing."""

from __future__ import annotations

import json
from pathlib import Path

from cairn.hosts import Host
from cairn.hosts._io import atomic_write, backup


def write_json_mcp(
    path: Path, entry: dict, *, root_key: str = "mcpServers", dry: bool = False
) -> str:
    """Set <root_key>['agentcairn'] = entry in a JSON config, preserving all other
    content. `root_key` is the top-level servers map — "mcpServers" for most hosts,
    "servers" for VS Code. Returns the rendered content (dry) or a write summary."""
    data: dict = {}
    if path.exists():
        if not dry:
            backup(path)  # snapshot before we risk raising on a malformed config
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON ({e}); fix it or use --print") from e
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object; fix it or use --print")
    servers = data.setdefault(root_key, {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: '{root_key}' is not an object; fix it or use --print")
    servers["agentcairn"] = entry
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if dry:
        return rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(path, rendered)
    return f"wrote agentcairn → {path}"


def write_host(host: Host, entry: dict, *, dry: bool = False) -> str:
    """Dispatch to the right writer for the host's config format."""
    if host.format == "json":
        return write_json_mcp(host.config_path(), entry, root_key=host.root_key, dry=dry)
    raise ValueError(f"unknown host format: {host.format!r}")
