# SPDX-License-Identifier: Apache-2.0
"""Install the using-agentcairn-memory SKILL.md into a skill-aware MCP host
(e.g. Cursor's ~/.cursor/skills). The skill body ships as package data under
cairn/assets/ so a pip-installed cairn can write it without the repo plugin/ dir."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from cairn.hosts._io import atomic_write


def cursor_skill_text() -> str:
    """The bundled using-agentcairn-memory SKILL.md, read from package data."""
    res = importlib.resources.files("cairn") / "assets" / "using-agentcairn-memory" / "SKILL.md"
    return res.read_text(encoding="utf-8")


def install_skill(skill_root: Path, *, dry: bool = False) -> str:
    """Write the agentcairn memory skill to <skill_root>/using-agentcairn-memory/SKILL.md.

    Idempotent (agentcairn's own file; overwrites/refreshes, no backup). dry=True
    returns a note and writes nothing."""
    dest = skill_root / "using-agentcairn-memory" / "SKILL.md"
    if dry:
        return f"would install skill → {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(dest, cursor_skill_text())
    return f"installed skill → {dest}"
