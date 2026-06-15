# SPDX-License-Identifier: Apache-2.0
"""Registry of MCP hosts `cairn install` can configure."""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Host:
    id: str
    label: str
    format: str  # "json" (mcpServers/servers JSON config); plugin hosts use kind="plugin"
    path_template: str  # may start with ~ ; expanded by config_path()
    root_key: str = (
        "mcpServers"  # JSON top-level key holding the servers map (VS Code uses "servers")
    )
    # What marks this host as "installed" for detection. Defaults to the config file's
    # parent dir; override when that parent is shared with another host (e.g. Gemini CLI
    # and Antigravity both live under ~/.gemini, so Gemini CLI keys off its actual file).
    detect_template: str | None = None
    kind: str = "mcp"  # "mcp" (write a config file) | "plugin" (install via host CLI)
    cli: str | None = None  # plugin hosts: the host's CLI binary (e.g. "codex", "claude")
    marketplace_add: tuple[str, ...] | None = None  # argv after the cli; "{source}" is substituted
    plugin_add: tuple[str, ...] | None = None  # argv after the cli to install the plugin
    # mcp hosts that also accept a SKILL.md (e.g. Cursor's ~/.cursor/skills); the
    # install command writes the using-agentcairn-memory skill there too.
    skill_dir: str | None = None

    def config_path(self) -> Path:
        return Path(self.path_template).expanduser()

    def detect_path(self) -> Path:
        if self.detect_template is not None:
            return Path(self.detect_template).expanduser()
        return self.config_path().parent

    def detect(self) -> bool:
        """Is this host present? MCP hosts: their detect path exists. Plugin
        hosts: their CLI is on PATH."""
        if self.kind == "plugin":
            return self.cli is not None and shutil.which(self.cli) is not None
        return self.detect_path().exists()


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
    Host("cursor", "Cursor", "json", "~/.cursor/mcp.json", skill_dir="~/.cursor/skills"),
    Host("claude-desktop", "Claude Desktop", "json", _claude_desktop_path()),
    Host("vscode", "VS Code", "json", _vscode_path(), root_key="servers"),
    # Gemini CLI shares ~/.gemini with Antigravity; key detection off its actual
    # settings.json so an Antigravity-only ~/.gemini/config/ doesn't falsely match it.
    Host(
        "gemini",
        "Gemini CLI",
        "json",
        "~/.gemini/settings.json",
        detect_template="~/.gemini/settings.json",
    ),
    Host(
        "antigravity",
        "Antigravity",
        "plugin",  # format unused for plugin hosts; benign placeholder
        "~/.gemini/config/mcp_config.json",  # used only by the stale-MCP migration
        kind="plugin",
        cli="agy",
        marketplace_add=None,  # `agy plugin install` is a single step
        plugin_add=("plugin", "install", "{source}"),
    ),
    Host(
        "codex",
        "Codex CLI",
        "plugin",  # format is unused for plugin hosts; keep a benign value
        "~/.codex/config.toml",  # used only by the stale-MCP migration
        kind="plugin",
        cli="codex",
        marketplace_add=("plugin", "marketplace", "add", "{source}"),
        plugin_add=("plugin", "add", "agentcairn@agentcairn"),
    ),
    Host(
        "claude-code",
        "Claude Code",
        "plugin",
        "~/.claude",  # detect() ignores this for plugin hosts; CLI presence wins
        kind="plugin",
        cli="claude",
        marketplace_add=("plugin", "marketplace", "add", "{source}"),
        plugin_add=("plugin", "install", "agentcairn@agentcairn"),
    ),
]

_BY_ID = {h.id: h for h in HOSTS}


def get_host(host_id: str) -> Host | None:
    return _BY_ID.get(host_id)


def detected_hosts() -> list[Host]:
    """Hosts that appear present (MCP: config dir exists; plugin: CLI on PATH)."""
    return [h for h in HOSTS if h.detect()]
