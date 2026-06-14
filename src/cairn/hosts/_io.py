# SPDX-License-Identifier: Apache-2.0
"""Filesystem helpers shared by the host config writers and the plugin installer:
back up a file before risky edits, and write atomically (temp + rename)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def backup(path: Path) -> None:
    """Copy path to path + '.bak' if it exists (snapshot before a risky edit)."""
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def atomic_write(path: Path, text: str) -> None:
    """Write text to a temp file in the same dir, then atomically rename into place,
    so a crash/disk-full mid-write can never corrupt the existing file."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
