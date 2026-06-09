# SPDX-License-Identifier: Apache-2.0
"""Write adapter Notes to a directory as markdown via cairn.vault.write_note."""

from __future__ import annotations

from pathlib import Path

from cairn.vault import Note, write_note


def write_vault(notes: list[Note], vault_dir: Path) -> Path:
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    for note in notes:
        (vault_dir / f"{note.permalink}.md").write_text(write_note(note), encoding="utf-8")
    return vault_dir
