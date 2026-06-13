# src/cairn/ingest/consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Memory consolidation: collapse a new memory that semantically duplicates an
existing one, or mark an older memory superseded by a newer version of the same
evolving fact. LLM-classified above a cosine pre-gate; fail-safe (any uncertainty
or error -> DISTINCT, i.e. keep both). LLM tier only."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

_CONSOLIDATE_GATE = 0.88  # cosine below this -> no classify call (write normally).
# Validated on the real corpus (scripts/eval_consolidate.py); conservative on
# purpose — a higher gate means fewer chances to drop a distinct memory.


class ConsolidationVerdict(StrEnum):
    DISTINCT = "distinct"  # separate facts -> write both
    DUPLICATE = "duplicate"  # same fact, new adds nothing newer -> skip the new
    SUPERSEDES = "supersedes"  # new is a strictly NEWER version -> write new, mark old


@dataclass(frozen=True)
class Neighbor:
    permalink: str
    text: str  # the existing memory's distilled text (for the classify prompt)
    timestamp: str | None


class NeighborIndex(Protocol):
    def nearest(self, text: str) -> tuple[Neighbor, float] | None:
        """Closest existing memory to `text` and its cosine, or None if empty.
        Spans prior-sweep index notes AND this-sweep writes; embeds internally."""

    def add(self, permalink: str, text: str, timestamp: str | None) -> None:
        """Register a memory written this sweep so later candidates can match it."""


class Consolidator(Protocol):
    def classify(
        self, *, new_text: str, new_ts: str | None, neighbor: Neighbor
    ) -> ConsolidationVerdict: ...
