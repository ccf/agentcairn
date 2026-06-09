# src/cairn/ingest/dedup.py
# SPDX-License-Identifier: Apache-2.0
"""SHA-256 dedup ledger so re-ingest is idempotent. The ledger is a rebuildable
cache (newline-delimited hex hashes) kept on local disk, never inside the vault."""

from __future__ import annotations

import hashlib
from pathlib import Path


def content_hash(text: str) -> str:
    """SHA-256 hex of the (already redacted) candidate text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class DedupLedger:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._seen: set[str] = set()
        if self.path.exists():
            self._seen = {ln.strip() for ln in self.path.read_text().splitlines() if ln.strip()}

    def seen(self, h: str) -> bool:
        return h in self._seen

    def add(self, h: str) -> None:
        if h in self._seen:
            return
        self._seen.add(h)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(h + "\n")
