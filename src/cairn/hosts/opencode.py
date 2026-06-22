# SPDX-License-Identifier: Apache-2.0
"""Install the OpenCode ambient plugin (agentcairn.ts) and slash commands
(recall.md, remember.md) into the OpenCode config directory.

Assets ship as package data under cairn/assets/opencode/ so a pip-installed
cairn can write them without the repo integrations/ dir being present."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from cairn.hosts._io import atomic_write


def _opencode_asset(rel: str) -> str:
    """Read a bundled opencode asset by its path relative to cairn/assets/opencode/."""
    res = importlib.resources.files("cairn") / "assets" / "opencode" / rel
    return res.read_text(encoding="utf-8")


def install_opencode_plugin(opencode_cfg_dir: Path, *, dry: bool = False) -> str:
    """Copy the agentcairn plugin + slash commands into the OpenCode config directory.

    ``opencode_cfg_dir`` is the directory that contains opencode.json (i.e.
    ``~/.config/opencode/``).  The function creates:
      <opencode_cfg_dir>/plugin/agentcairn.ts
      <opencode_cfg_dir>/commands/recall.md
      <opencode_cfg_dir>/commands/remember.md

    Idempotent: overwriting our own files is safe.  dry=True returns a
    descriptive note and writes nothing.
    """
    plugin_dest = opencode_cfg_dir / "plugin" / "agentcairn.ts"
    recall_dest = opencode_cfg_dir / "commands" / "recall.md"
    remember_dest = opencode_cfg_dir / "commands" / "remember.md"

    if dry:
        return (
            f"would install opencode plugin → {plugin_dest}\n"
            f"  would install command → {recall_dest}\n"
            f"  would install command → {remember_dest}"
        )

    plugin_dest.parent.mkdir(parents=True, exist_ok=True)
    recall_dest.parent.mkdir(parents=True, exist_ok=True)

    atomic_write(plugin_dest, _opencode_asset("agentcairn.ts"))
    atomic_write(recall_dest, _opencode_asset("commands/recall.md"))
    atomic_write(remember_dest, _opencode_asset("commands/remember.md"))

    return (
        f"installed opencode plugin → {plugin_dest}\n"
        f"  installed command → {recall_dest}\n"
        f"  installed command → {remember_dest}"
    )
