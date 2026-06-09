# SPDX-License-Identifier: Apache-2.0
"""Shared value types for the benchmark harness."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Query:
    qid: str
    question: str
    answer: str
    gold_sessions: set[str] = field(default_factory=set)  # note permalinks
    gold_turns: set[str] = field(default_factory=set)  # turn ids (in heading_path)
    category: str | int | None = None  # question_type / LoCoMo category
    is_abstention: bool = False
    meta: dict = field(default_factory=dict)  # question_date, session dates, etc.
