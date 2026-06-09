# src/cairn/ingest/importance.py
# SPDX-License-Identifier: Apache-2.0
"""Heuristic importance gate. Deterministic and explainable — keeps the vault from
flooding (spec §9). The agent-loop reflective distiller (Plan 5) can later refine
or override these scores; v1 is a transparent baseline."""

from __future__ import annotations

import re

KEEP_THRESHOLD = 0.5

# High-signal markers: decisions, preferences, corrections, lessons, durable facts.
_SIGNAL_MARKERS = [
    "decid",
    "prefer",
    "instead",
    "actually",
    "remember",
    "always",
    "never",
    "root cause",
    "the bug",
    "because",
    "lesson",
    "gotcha",
    "footgun",
    "todo",
    "important",
    "must ",
    "should ",
    "convention",
    "do not",
    "don't",
]
_TRIVIAL = {
    "ok",
    "okay",
    "thanks",
    "thank you",
    "yes",
    "no",
    "yep",
    "nope",
    "sure",
    "sounds good",
    "lgtm",
    "got it",
    "great",
    "perfect",
    "done",
    "cool",
}
_WORD_RE = re.compile(r"\w+")


def score(text: str) -> float:
    """Return an importance score in [0, 1]."""
    stripped = text.strip()
    low = stripped.lower().rstrip("!.")
    if low in _TRIVIAL:
        return 0.0
    words = _WORD_RE.findall(stripped)
    n = len(words)
    if n < 4:
        return 0.0

    s = 0.0
    # length signal: saturates around ~40 words
    s += min(n / 40.0, 1.0) * 0.4
    # marker signal: each distinct marker adds, capped
    hits = sum(1 for m in _SIGNAL_MARKERS if m in low)
    s += min(hits * 0.25, 0.6)
    return min(s, 1.0)


def is_important(text: str, *, threshold: float = KEEP_THRESHOLD) -> bool:
    return score(text) >= threshold
