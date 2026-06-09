# SPDX-License-Identifier: Apache-2.0
"""Build a scoped, throwaway DuckDB index from adapter Notes and return a read-only
search connection + the corpus chunk count (for pool sizing)."""

from __future__ import annotations

from pathlib import Path

from cairn.index import open_index, reconcile
from cairn.search import open_search
from cairn.vault import Note
from cairn_bench.vaultize import write_vault


def build_scoped_index(notes: list[Note], work_dir: Path, embedder) -> tuple[object, int]:
    work_dir = Path(work_dir)
    vault = write_vault(notes, work_dir / "vault")
    idx = work_dir / "index.duckdb"
    wcon = open_index(str(idx), dim=embedder.dim, model_id=embedder.model_id)
    try:
        reconcile(wcon, str(vault), embedder)
        chunk_count = wcon.execute("SELECT count(*) FROM chunks").fetchone()[0]
    finally:
        wcon.close()  # release the write lock before opening read-only
    return open_search(str(idx)), int(chunk_count)
