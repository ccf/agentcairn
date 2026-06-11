# SPDX-License-Identifier: Apache-2.0
"""Merge the agentcairn MCP entry into a host config — non-destructive, idempotent,
backup-first. With dry=True, render the would-be file content and write nothing."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import tomlkit

from cairn.hosts import Host


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def _atomic_write(path: Path, text: str) -> None:
    """Write text to a temp file in the same dir, then atomically rename into place,
    so a crash/disk-full mid-write can never corrupt the existing config."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_json_mcp(path: Path, entry: dict, *, dry: bool = False) -> str:
    """Set mcpServers['agentcairn'] = entry in a JSON config, preserving all other
    content. Returns the rendered content (dry) or a write summary."""
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON ({e}); fix it or use --print") from e
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object; fix it or use --print")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcpServers' is not an object; fix it or use --print")
    servers["agentcairn"] = entry
    rendered = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if dry:
        return rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    _atomic_write(path, rendered)
    return f"wrote agentcairn → {path}"


def write_host(host: Host, entry: dict, *, dry: bool = False) -> str:
    """Dispatch to the right writer for the host's config format."""
    if host.format == "mcpServers":
        return write_json_mcp(host.config_path(), entry, dry=dry)
    if host.format == "codex-toml":
        return write_codex_toml(host.config_path(), entry, dry=dry)
    raise ValueError(f"unknown host format: {host.format!r}")


def write_codex_toml(path: Path, entry: dict, *, dry: bool = False) -> str:
    """Set [mcp_servers.agentcairn] (+ .env) in a Codex TOML config, preserving all
    other tables and comments (tomlkit round-trips)."""
    doc = tomlkit.document()
    if path.exists():
        try:
            doc = tomlkit.parse(path.read_text(encoding="utf-8"))
        except Exception as e:  # tomlkit raises ParseError/ValueError variants
            raise ValueError(f"{path} is not valid TOML ({e}); fix it or use --print") from e
    servers = doc.get("mcp_servers")
    if servers is None:
        servers = tomlkit.table(is_super_table=True)
        doc["mcp_servers"] = servers
    ac = tomlkit.table()
    ac["command"] = entry["command"]
    ac["args"] = entry["args"]
    env = tomlkit.table()
    for k, v in entry["env"].items():
        env[k] = v
    ac["env"] = env
    servers["agentcairn"] = ac
    rendered = tomlkit.dumps(doc)
    if dry:
        return rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    _atomic_write(path, rendered)
    return f"wrote [mcp_servers.agentcairn] → {path}"
