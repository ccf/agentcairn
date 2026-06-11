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
    format: str  # "mcpServers" (JSON) | "codex-toml"
    path_template: str  # may start with ~ ; expanded by config_path()

    def config_path(self) -> Path:
        return Path(self.path_template).expanduser()


def _claude_desktop_path() -> str:
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return "~/AppData/Roaming/Claude/claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


HOSTS: list[Host] = [
    Host("cursor", "Cursor", "mcpServers", "~/.cursor/mcp.json"),
    Host("claude-desktop", "Claude Desktop", "mcpServers", _claude_desktop_path()),
    Host("windsurf", "Windsurf", "mcpServers", "~/.codeium/windsurf/mcp_config.json"),
    Host("gemini", "Gemini CLI", "mcpServers", "~/.gemini/settings.json"),
    Host("codex", "Codex CLI", "codex-toml", "~/.codex/config.toml"),
]

_BY_ID = {h.id: h for h in HOSTS}


def get_host(host_id: str) -> Host | None:
    return _BY_ID.get(host_id)


def detected_hosts() -> list[Host]:
    """Hosts whose config directory exists (the tool appears installed)."""
    return [h for h in HOSTS if h.config_path().parent.is_dir()]
