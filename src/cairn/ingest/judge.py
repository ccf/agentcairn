# src/cairn/ingest/judge.py
# SPDX-License-Identifier: Apache-2.0
"""Layer B: semantic memory-worthiness judging of structurally-authored turns.

Three tiers behind one interface (spec 2026-06-12):
- LLMJudge   (CAIRN_JUDGE=anthropic + key): durability + title + distilled body.
- EmbeddingJudge (default when an embedder loads): durability only, via cosine
  margin against curated durable/ephemeral prototype sets. Local, free, no key.
- None: heuristic-only floor (today's behavior).
Every failure degrades one tier silently; ingestion never blocks on a model."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Judgment:
    durability: float  # 0..1 (semantic memory-worthiness)
    title: str | None = None  # LLM tier only
    distilled: str | None = None  # LLM tier only


class Judge(Protocol):
    def judge(self, texts: list[str]) -> list[Judgment]: ...


# Curated prototypes (tuned against the 2026-06 real-corpus eval; see
# scripts/eval_judge.py). Durable = decisions, preferences, lessons, pivots.
# Ephemeral = task coordination, status checks, deploy chatter.
_DURABLE_PROTOTYPES: tuple[str, ...] = (
    "We decided to always rebase-merge approved PRs and delete the branch after.",
    "I prefer clarifying questions as plain text, not popups.",
    "Lesson learned: never trust role==user to mean a human wrote the message.",
    "The root cause was the entropy regex including slashes, so paths matched as tokens.",
    "Here's an idea for a pivot: reshape the product around developer memory.",
    "Important convention: design specs go in docs/specs, plans in docs/plans.",
    "We should keep the vault global by default; project scoping is an opt-in feature.",
    "Key architectural decision: the markdown vault is the source of truth, the index is disposable.",  # noqa: E501
    "Gotcha: the pre-commit hook rejects the first commit when ruff reformats files.",
    "My strategy preference: speed matters most for capturing the spread; do not deprioritize it.",
)
_EPHEMERAL_PROTOTYPES: tuple[str, ...] = (
    "Check CI status on PR #76 and merge it if green.",
    "I reopened and merged pr12, go ahead and make a quick pass.",
    "Watch the pull request and fix anything the bot flags.",
    "Did we actually push the website fix as a PR? I don't see it.",
    "Production branch is set to main and build watch paths is set to *.",
    "Let's upgrade the backend to 0.9.26, we're still running 0.9.24.",
    "Push and open the PR, then run the sweep again.",
    "The deploy finished, restart the soak watch for another 15 minutes.",
    "Run the test suite again and paste the output.",
    "Rebase the branch on main and re-trigger the workflow.",
)

_MARGIN_GAIN = 2.5  # maps small cosine margins onto a useful 0..1 spread


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


class EmbeddingJudge:
    """Durability = clamp01(0.5 + gain * (mean_cos(durable) - mean_cos(ephemeral)))."""

    def __init__(self, embedder) -> None:  # embedder: cairn.embed.Embedder
        self._embedder = embedder
        self._durable_vecs = embedder.embed(list(_DURABLE_PROTOTYPES))
        self._ephemeral_vecs = embedder.embed(list(_EPHEMERAL_PROTOTYPES))

    def judge(self, texts: list[str]) -> list[Judgment]:
        if not texts:
            return []
        out: list[Judgment] = []
        for vec in self._embedder.embed(texts):
            d = sum(_cos(vec, p) for p in self._durable_vecs) / len(self._durable_vecs)
            e = sum(_cos(vec, p) for p in self._ephemeral_vecs) / len(self._ephemeral_vecs)
            durability = max(0.0, min(1.0, 0.5 + _MARGIN_GAIN * (d - e)))
            out.append(Judgment(durability=durability))
        return out
