# SPDX-License-Identifier: Apache-2.0
"""Filesystem helpers shared by host config writers and plugin installers."""

from __future__ import annotations

import shutil
from pathlib import Path

from cairn.storage import atomic_write_text


def backup(path: Path) -> None:
    """Copy path to path + '.bak' if it exists (snapshot before a risky edit)."""
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def atomic_write(path: Path, text: str) -> None:
    """Secure atomic write that preserves user-managed config symlinks."""
    target = path.resolve() if path.is_symlink() else path
    atomic_write_text(target, text)
