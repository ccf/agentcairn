# SPDX-License-Identifier: Apache-2.0
"""Registry of MCP hosts `cairn install` can configure."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Host:
    id: str
    label: str
    format: str  # "json" (an mcpServers/servers JSON config) | "codex-toml"
    path_template: str  # may start with ~ ; expanded by config_path()
    root_key: str = (
        "mcpServers"  # JSON top-level key holding the servers map (VS Code uses "servers")
    )

    def config_path(self) -> Path:
        return Path(self.path_template).expanduser()


def _claude_desktop_path() -> str:
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return "~/AppData/Roaming/Claude/claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


def _vscode_path() -> str:
    if sys.platform == "darwin":
        return "~/Library/Application Support/Code/User/mcp.json"
    if sys.platform.startswith("win"):
        return "~/AppData/Roaming/Code/User/mcp.json"
    return "~/.config/Code/User/mcp.json"


HOSTS: list[Host] = [
    Host("cursor", "Cursor", "json", "~/.cursor/mcp.json"),
    Host("claude-desktop", "Claude Desktop", "json", _claude_desktop_path()),
    Host("vscode", "VS Code", "json", _vscode_path(), root_key="servers"),
    Host("gemini", "Gemini CLI", "json", "~/.gemini/settings.json"),
    Host("antigravity", "Antigravity", "json", "~/.gemini/config/mcp_config.json"),
    Host("codex", "Codex CLI", "codex-toml", "~/.codex/config.toml"),
]

_BY_ID = {h.id: h for h in HOSTS}


def get_host(host_id: str) -> Host | None:
    return _BY_ID.get(host_id)


def detected_hosts() -> list[Host]:
    """Hosts whose config directory exists (the tool appears installed)."""
    return [h for h in HOSTS if h.config_path().parent.is_dir()]
