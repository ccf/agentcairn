# src/cairn/ingest/consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Memory consolidation: collapse a new memory that semantically duplicates an
existing one, or mark an older memory superseded by a newer version of the same
evolving fact. LLM-classified above a cosine pre-gate; fail-safe (any uncertainty
or error -> DISTINCT, i.e. keep both). LLM tier only."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from cairn.ingest.judge import _anthropic_request

_CONSOLIDATE_GATE = 0.88  # cosine below this -> no classify call (write normally).
# Validated on the real corpus (scripts/eval_consolidate.py); conservative on
# purpose — a higher gate means fewer chances to drop a distinct memory.

_CONTEXT_RE = re.compile(r"^- \[context\] (.+)$", re.MULTILINE)


def extract_context(body: str) -> str | None:
    """The distilled fact from a derived-note body (`- [context] <text> #ingested`),
    used as the consolidation similarity signal — the `[verbatim]` turn is excluded
    because it makes notes cluster by conversational genre. Returns None if the note
    has no `[context]` line."""
    m = _CONTEXT_RE.search(body)
    if not m:
        return None
    text = m.group(1).strip().removesuffix("#ingested").rstrip()
    return text or None


class ConsolidationVerdict(StrEnum):
    DISTINCT = "distinct"  # separate facts -> write both
    DUPLICATE = "duplicate"  # same fact, new adds nothing newer -> skip the new
    SUPERSEDES = "supersedes"  # new is a strictly NEWER version -> write new, mark old


@dataclass(frozen=True)
class Neighbor:
    permalink: str
    text: str  # the existing memory's distilled text (for the classify prompt)
    timestamp: str | None
    path: str | None = None


class NeighborIndex(Protocol):
    def nearest(self, text: str) -> tuple[Neighbor, float] | None:
        """Closest existing memory to `text` and its cosine, or None if empty.
        Spans prior-sweep index notes AND this-sweep writes; embeds internally."""

    def add(
        self, permalink: str, text: str, timestamp: str | None, path: str | None = None
    ) -> None:
        """Register a memory written this sweep so later candidates can match it."""

    def note_superseded(self, permalink: str) -> None:
        """Flag a this-sweep note as superseded so it is not returned as a neighbor
        again (prior-index notes are excluded via SQL)."""


class Consolidator(Protocol):
    def classify(
        self, *, new_text: str, new_ts: str | None, neighbor: Neighbor
    ) -> ConsolidationVerdict: ...


_PROMPT = """You compare a NEW developer memory against the most similar EXISTING \
memory and classify their relationship. Respond with ONLY a JSON object \
{"relation": "<value>"} where value is one of:
- "duplicate": they state the same fact and the NEW one adds nothing newer — \
either the same value, or the NEW one is an OLDER/equal version of an evolving \
fact (e.g. EXISTING says "scaled to 4GB" and NEW says "scaled to 2GB": the new is \
stale, answer "duplicate" so the existing newer note is kept).
- "supersedes": the NEW one is a strictly NEWER version of the SAME evolving fact \
(e.g. an updated count, status, or decision that replaces the old value).
- "distinct": they are different facts, or you are unsure.
Use the timestamps to judge recency. When in doubt, answer "distinct"."""


class LLMConsolidator:
    """Classifies a (new, neighbor) memory pair via one Messages call. Any error,
    unparseable response, or unknown relation -> DISTINCT (keep both)."""

    def __init__(self, *, api_key: str, model: str, timeout: float) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def classify(
        self, *, new_text: str, new_ts: str | None, neighbor: Neighbor
    ) -> ConsolidationVerdict:
        body = (
            f"NEW (timestamp {new_ts}):\n{new_text}\n\n"
            f"EXISTING (timestamp {neighbor.timestamp}):\n{neighbor.text}"
        )
        payload = {
            "model": self._model,
            "max_tokens": 256,
            "system": _PROMPT,
            "messages": [{"role": "user", "content": body}],
        }
        try:
            resp = _anthropic_request(payload, self._api_key, self._timeout)
            raw = "".join(
                b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"
            ).strip()
            if raw.startswith("```"):
                raw = raw.strip("`").removeprefix("json").strip()
            obj, _ = json.JSONDecoder().raw_decode(raw)
            return ConsolidationVerdict(obj["relation"])
        except Exception:
            return ConsolidationVerdict.DISTINCT  # fail-safe: keep both


def resolve_consolidator(*, env: dict | None = None) -> Consolidator | None:
    """LLMConsolidator when CAIRN_JUDGE=anthropic with a key AND consolidation is
    enabled; else None (no consolidation)."""
    from cairn.config import cairn_env, judge_config, resolve_consolidate

    e = env if env is not None else dict(cairn_env())
    if not resolve_consolidate(e):
        return None
    mode, model, timeout = judge_config(e)
    if mode != "anthropic":
        return None
    key = e.get("ANTHROPIC_API_KEY")
    if not key:
        return None
    return LLMConsolidator(api_key=key, model=model, timeout=timeout)
