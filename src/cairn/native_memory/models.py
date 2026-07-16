# SPDX-License-Identifier: Apache-2.0
"""Value types for importing host-native, model-authored memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from cairn.vault import Note


@dataclass(frozen=True)
class NativeMemoryDocument:
    """One source Markdown file captured as a read-only snapshot."""

    path: Path
    relative_path: str
    text: str
    modified_at: str


@dataclass(frozen=True)
class NativeMemoryDiscovery:
    """A successfully scanned native-memory scope.

    ``scope_id`` is opaque and stable for the selected project. An existing but
    empty ``documents`` tuple is meaningful: prior imports in this scope may be
    expired. A missing or unreadable root must fail before constructing this type.
    """

    source: str
    root: Path
    scope_id: str
    project: str | None
    project_root: Path
    documents: tuple[NativeMemoryDocument, ...]


class NativeMemorySource(Protocol):
    name: str

    def discover(self, project_root: Path) -> NativeMemoryDiscovery: ...


@dataclass
class NativeMemoryAction:
    """One planned vault mutation (or an explicit unchanged source)."""

    kind: str  # added | updated | repaired | unchanged | expired
    relative_path: str
    source_id: str
    source_hash: str | None
    version: int
    destination: Path | None
    note: Note | None = None
    prior_path: Path | None = None
    prior_permalink: str | None = None
    stale_paths: tuple[Path, ...] = ()
    redactions: int = 0


@dataclass
class NativeMemoryPlan:
    discovery: NativeMemoryDiscovery
    manifest_path: Path
    registry_path: Path
    actions: list[NativeMemoryAction] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)

    @property
    def discovered(self) -> int:
        return len(self.discovery.documents)

    def count(self, kind: str) -> int:
        return sum(action.kind == kind for action in self.actions)

    @property
    def redactions(self) -> int:
        return sum(action.redactions for action in self.actions)

    def to_dict(self, *, applied: bool) -> dict:
        return {
            "source": self.discovery.source,
            "source_root": str(self.discovery.root),
            "project": self.discovery.project,
            "project_root": str(self.discovery.project_root),
            "applied": applied,
            "discovered": self.discovered,
            "added": self.count("added"),
            "updated": self.count("updated"),
            "repaired": self.count("repaired"),
            "unchanged": self.count("unchanged"),
            "expired": self.count("expired"),
            "redactions": self.redactions,
            "actions": [
                {
                    "action": action.kind,
                    "path": action.relative_path,
                    "version": action.version,
                }
                for action in self.actions
            ],
        }


@dataclass
class NativeMemoryReport:
    discovered: int = 0
    added: int = 0
    updated: int = 0
    repaired: int = 0
    unchanged: int = 0
    expired: int = 0
    redactions: int = 0
    written: list[Path] = field(default_factory=list)

    @classmethod
    def from_plan(cls, plan: NativeMemoryPlan) -> NativeMemoryReport:
        return cls(
            discovered=plan.discovered,
            added=plan.count("added"),
            updated=plan.count("updated"),
            repaired=plan.count("repaired"),
            unchanged=plan.count("unchanged"),
            expired=plan.count("expired"),
            redactions=plan.redactions,
        )
