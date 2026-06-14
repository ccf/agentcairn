# SPDX-License-Identifier: Apache-2.0
"""Plugin-host support for `cairn install`: install the agentcairn plugin via the
host's own CLI (codex/claude), and migrate a host away from a previously-written
raw MCP config block so the bundled plugin MCP isn't double-registered."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import tomlkit

from cairn.hosts import Host
from cairn.hosts._io import atomic_write, backup


def _commands(host: Host, source: str) -> list[list[str]]:
    """The argv lists to run: marketplace-add then plugin-add, with {source}
    substituted. cli is guaranteed non-None for plugin hosts."""
    out: list[list[str]] = []
    for tmpl in (host.marketplace_add, host.plugin_add):
        if tmpl is None:
            continue
        out.append([host.cli] + [a.replace("{source}", source) for a in tmpl])
    return out


def install_plugin(host: Host, *, source: str, dry: bool = False) -> str:
    """Install the agentcairn plugin into a plugin host via its CLI. With dry=True,
    return the commands (the `--print` view) and run nothing. Raises ValueError if
    the host CLI is not on PATH (real run only)."""
    cmds = _commands(host, source)
    rendered = "\n".join(" ".join(c) for c in cmds)
    if dry:
        return rendered
    if host.cli is None or shutil.which(host.cli) is None:
        raise ValueError(
            f"'{host.cli}' not found on PATH; install {host.label} first, "
            f"or run `cairn install {host.id} --print` to see the commands"
        )
    results: list[str] = []
    for argv in cmds:
        r = subprocess.run(argv, check=False, capture_output=True, text=True)
        if r.returncode != 0:
            tail = (r.stderr or r.stdout or "").strip().splitlines()
            detail = tail[-1] if tail else f"exit {r.returncode}"
            raise ValueError(f"`{' '.join(argv)}` failed: {detail}")
        results.append(f"$ {' '.join(argv)}  →  ok")
    return "\n".join(results)


def migrate_codex_mcp_block(path: Path, *, dry: bool = False) -> str | None:
    """Remove a stale [mcp_servers.agentcairn] table from a Codex config.toml so the
    bundled plugin MCP isn't double-registered. Backup-first; preserves everything
    else (tomlkit). Returns a note if it removed the block, else None (no-op)."""
    if not path.exists():
        return None
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except Exception as e:
        raise ValueError(f"{path} is not valid TOML ({e}); fix it or use --print") from e
    servers = doc.get("mcp_servers")
    if not isinstance(servers, dict) or "agentcairn" not in servers:
        return None
    if dry:
        return f"would remove [mcp_servers.agentcairn] from {path}"
    backup(path)
    del servers["agentcairn"]
    atomic_write(path, tomlkit.dumps(doc))
    return f"removed stale [mcp_servers.agentcairn] from {path}"


def migrate_antigravity_mcp_block(path: Path, *, dry: bool = False) -> str | None:
    """Remove a stale mcpServers.agentcairn entry from a JSON mcp_config.json so the
    bundled plugin MCP isn't double-registered. Backup-first; preserves everything
    else. Returns a note if it removed the entry, else None (no-op)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON ({e}); fix it or use --print") from e
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict) or "agentcairn" not in servers:
        return None
    if dry:
        return f"would remove mcpServers.agentcairn from {path}"
    backup(path)
    del servers["agentcairn"]
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return f"removed stale mcpServers.agentcairn from {path}"
