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

import json
import math
import urllib.request
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Judgment:
    durability: float  # 0..1 (semantic memory-worthiness)
    title: str | None = None  # LLM tier only
    distilled: str | None = None  # LLM tier only
    degraded: bool = False  # True if produced by an LLM-chunk fallback, not a real
    # LLM verdict — such a judgment must NOT gate by the LLM keep rule nor be cached
    # at the LLM tier (a transient failure would otherwise drop a turn forever).


class Judge(Protocol):
    def judge(
        self, texts: list[str], *, contexts: list[str | None] | None = None
    ) -> list[Judgment]: ...


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


_EMBED_BATCH = 64  # texts per embed() call — a first-run/rebuild can have ~1000
# pending candidates, and embedding them in one giant batch can OOM-kill the process.
_JUDGE_INPUT_CHARS = 2000  # judge the HEAD of each message: durability is evident
# early, and real corpora contain pasted blobs up to ~300KB that OOM the embedder
# (transformer memory scales with sequence length) and would explode LLM token cost.


def _judge_input(text: str) -> str:
    return text if len(text) <= _JUDGE_INPUT_CHARS else text[:_JUDGE_INPUT_CHARS]


class EmbeddingJudge:
    """Durability = clamp01(0.5 + gain * (mean_cos(durable) - mean_cos(ephemeral)))."""

    def __init__(self, embedder) -> None:  # embedder: cairn.embed.Embedder
        self._embedder = embedder
        self._durable_vecs = embedder.embed(list(_DURABLE_PROTOTYPES))
        self._ephemeral_vecs = embedder.embed(list(_EPHEMERAL_PROTOTYPES))

    def judge(
        self, texts: list[str], *, contexts: list[str | None] | None = None
    ) -> list[Judgment]:
        if not texts:
            return []
        out: list[Judgment] = []
        clipped = [_judge_input(t) for t in texts]
        for start in range(0, len(clipped), _EMBED_BATCH):
            for vec in self._embedder.embed(clipped[start : start + _EMBED_BATCH]):
                d = sum(_cos(vec, p) for p in self._durable_vecs) / len(self._durable_vecs)
                e = sum(_cos(vec, p) for p in self._ephemeral_vecs) / len(self._ephemeral_vecs)
                durability = max(0.0, min(1.0, 0.5 + _MARGIN_GAIN * (d - e)))
                out.append(Judgment(durability=durability))
        return out


_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_MAX_DISTILL_RATIO = 4  # distilled longer than 4x verbatim -> discarded
_BATCH_SIZE = 20  # texts per Messages call. Kept small because antecedent
# resolution (0.9.6) roughly doubles input size and lengthens each distillation,
# so a 40-item response sometimes omitted trailing items — fewer items per call
# keeps the JSON array complete (paired with tolerant parsing + 16k max_tokens).
_TIMEOUT_PER_MSG_S = 2.0  # the request timeout SCALES with the chunk size: a full
# 40-message batch takes ~30s on Sonnet, so a fixed small timeout (e.g. the old 10s
# default) would time out every batch and degrade to embedding silently. The
# configured judge_timeout is treated as a floor; the effective budget is at least
# this many seconds per message in the chunk.

_PROMPT = """You judge whether each numbered message from a developer's coding-agent \
session is a DURABLE memory (decision, preference, lesson, durable fact, strategic \
direction) or EPHEMERAL chatter (task coordination, status checks, one-off process \
instructions). For each, return durability in [0,1] (1 = clearly durable), a short \
descriptive title (<=70 chars), and a crisp 1-2 sentence distillation of the durable \
fact. For ephemeral messages use null title/distilled.

Some items include a "PRIOR ASSISTANT MESSAGE", provided only as context. Use it \
ONLY to resolve a referent that appears in the developer's message — e.g. "A", \
"option (i)", "all three", "that approach", "the second one". When you resolve such \
a referent, write the title and distillation so they stand alone (name what "A" \
was). If the developer's message carries no such referent, or is itself ephemeral, \
ignore the prior message entirely and judge the developer's message exactly as you \
would without it. Never manufacture a decision from a contentless acknowledgement \
("yes", "do it", "ok").
Return ONLY a JSON array: [{"i": <index>, "durability": <float>, "title": <str|null>, \
"distilled": <str|null>}, ...] with one entry per input, in order.
"""


def _anthropic_request(payload: dict, api_key: str, timeout: float) -> dict:
    """Single POST to the Anthropic Messages API (stdlib only; seam for tests)."""
    req = urllib.request.Request(
        _ANTHROPIC_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
        return json.loads(resp.read().decode("utf-8"))


class LLMJudge:
    """Batched Messages calls (chunks of _BATCH_SIZE) judging all candidates; a
    chunk failure degrades ONLY that chunk to the fallback judge (or neutral 0.5
    judgments if none) and counts in .degraded."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        fallback: Judge | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._fallback = fallback
        self.degraded = 0  # candidates that fell back a tier

    def judge(
        self, texts: list[str], *, contexts: list[str | None] | None = None
    ) -> list[Judgment]:
        if not texts:
            return []
        out: list[Judgment] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            chunk = texts[start : start + _BATCH_SIZE]
            chunk_ctx = contexts[start : start + _BATCH_SIZE] if contexts is not None else None
            try:
                out.extend(self._judge_llm(chunk, chunk_ctx))
            except Exception:
                self.degraded += len(chunk)
                # The fallback itself may fail (e.g. embedder dies mid-run); that
                # must degrade THIS chunk to neutral, not nuke earlier chunks'
                # successful results by escaping judge().
                fell_back: list[Judgment] | None = None
                if self._fallback is not None:
                    try:
                        fell_back = self._fallback.judge(chunk)
                    except Exception:
                        fell_back = None
                if fell_back is None:
                    fell_back = [Judgment(durability=0.5) for _ in chunk]
                # Mark every fallback verdict degraded so the pipeline gates it by
                # the fallback's rule (not the LLM keep rule) and never caches it
                # at the LLM tier — a real LLM verdict must replace it next run.
                out.extend(replace(j, degraded=True) for j in fell_back)
        return out

    def _judge_llm(
        self, texts: list[str], contexts: list[str | None] | None = None
    ) -> list[Judgment]:
        lines: list[str] = []
        for i, t in enumerate(texts):
            ctx = contexts[i] if contexts is not None else None
            if ctx:
                lines.append(
                    f"[{i}] PRIOR ASSISTANT MESSAGE (context only): {_judge_input(ctx)}\n"
                    f"    DEVELOPER MESSAGE: {_judge_input(t)}"
                )
            else:
                lines.append(f"[{i}] {_judge_input(t)}")
        numbered = "\n".join(lines)
        payload = {
            "model": self._model,
            "max_tokens": 16384,
            "system": _PROMPT,
            "messages": [{"role": "user", "content": numbered}],
        }
        # Scale the timeout with the batch so a too-low configured value can't
        # spuriously time out a full chunk (the floor is the configured timeout).
        timeout = max(self._timeout, _TIMEOUT_PER_MSG_S * len(texts))
        resp = _anthropic_request(payload, self._api_key, timeout)
        raw = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        items = json.loads(raw)  # malformed/truncated JSON raises -> whole chunk degrades
        by_i: dict[int, dict] = {}
        for it in items:
            try:
                by_i[int(it["i"])] = it  # an item with no/garbled "i" is unusable -> skip
            except (KeyError, ValueError, TypeError):
                continue
        out: list[Judgment | None] = [None] * len(texts)
        missing: list[int] = []
        for i, text in enumerate(texts):
            it = by_i.get(i)
            if it is None:
                # The model returned valid JSON but omitted this index (happens on
                # large batches). Degrade ONLY this item, not the whole chunk.
                missing.append(i)
                continue
            try:
                durability = max(0.0, min(1.0, float(it["durability"])))
                title = it.get("title") or None
                distilled = it.get("distilled") or None
                # The distillation summarizes the user turn AND (when present) the
                # antecedent it resolves against, so bound its length on the larger
                # of the two — a terse turn like "lock A" legitimately yields a
                # longer, self-contained fact once its referent is resolved.
                ctx = contexts[i] if contexts is not None else None
                base_len = max(len(text), len(ctx) if ctx else 0)
                if distilled and len(distilled) > _MAX_DISTILL_RATIO * max(base_len, 1):
                    distilled = None
                if title and len(title) > 120:
                    title = None
                out[i] = Judgment(durability=durability, title=title, distilled=distilled)
            except (KeyError, ValueError, TypeError):
                # A malformed individual item (missing/non-numeric durability, etc.)
                # degrades only this index, not the whole chunk. Only a top-level
                # invalid/truncated JSON (json.loads above) degrades the chunk.
                missing.append(i)
        if missing:
            # Fill omitted indices from the fallback judge (or neutral), marked
            # degraded so they gate by the fallback rule and re-judge next run —
            # one missing item must not nuke the whole batch's good verdicts.
            self.degraded += len(missing)
            fb_texts = [texts[i] for i in missing]
            fb: list[Judgment] | None = None
            if self._fallback is not None:
                try:
                    fb = self._fallback.judge(fb_texts)
                except Exception:
                    fb = None
            if fb is None:
                fb = [Judgment(durability=0.5) for _ in fb_texts]
            for k, i in enumerate(missing):
                out[i] = replace(fb[k], degraded=True)
        return [j for j in out if j is not None]


# Bump when a change to the judge (prompt, model defaults, output handling, or a
# degradation/caching bug fix) means previously-cached verdicts can no longer be
# trusted. Rows from an older version — and legacy rows with no version at all —
# are discarded on load, so the candidate is re-judged instead of reusing stale
# data. v2: invalidate the silent-timeout era (judge_timeout=10 degraded every
# batch and cached embedding-fallback verdicts as tier "llm"; see 0.9.4).
# v3: the prompt now resolves referents from a prior-assistant block (0.9.6).
_JUDGE_CACHE_VERSION = 3


class JudgedCache:
    """hash -> Judgment for already-judged-but-not-written candidates, so the
    LLM never re-judges the same text across runs. JSONL beside the dedup ledger.
    (Written candidates are dedup-ledgered and never reconsidered; this cache
    covers the gated-out ones, which stay pending forever.) The FULL judgment is
    cached — title/distilled included — so a candidate that later passes the gate
    (e.g. a lower threshold) still gets the LLM distillation format. Each row is
    stamped with _JUDGE_CACHE_VERSION; stale-version rows are dropped on load."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._mem: dict[str, tuple[Judgment, str]] = {}  # h -> (judgment, tier)
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                    # Discard verdicts from a prior judge version (incl. legacy
                    # rows with no "v"): re-judge rather than trust stale data.
                    if obj.get("v") != _JUDGE_CACHE_VERSION:
                        continue
                    j = Judgment(
                        durability=float(obj["d"]),
                        title=obj.get("t") or None,
                        distilled=obj.get("s") or None,
                    )
                    self._mem[str(obj["h"])] = (j, obj.get("tier") or "embedding")
                except Exception:
                    continue  # tolerate torn/corrupt lines — it's a rebuildable cache

    def get(self, h: str) -> tuple[Judgment, str] | None:
        """Return (judgment, tier) for a cached hash, or None."""
        return self._mem.get(h)

    def put(self, h: str, judgment: Judgment, tier: str = "embedding") -> None:
        if self._mem.get(h) == (judgment, tier):
            return  # idempotent: no duplicate appends across runs
        self._mem[h] = (judgment, tier)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        row: dict = {"h": h, "d": judgment.durability, "tier": tier, "v": _JUDGE_CACHE_VERSION}
        if judgment.title:
            row["t"] = judgment.title
        if judgment.distilled:
            row["s"] = judgment.distilled
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")


_TIER_RANK = {"none": 0, "embedding": 1, "llm": 2}


def tier_at_least(cached_tier: str, current_tier: str) -> bool:
    """Is a cached verdict good enough to reuse on a run at `current_tier`?
    An llm entry is reusable on an embedding run; an embedding entry is NOT
    reusable on an llm run (the LLM must re-judge for distillation)."""
    return _TIER_RANK.get(cached_tier, 0) >= _TIER_RANK.get(current_tier, 0)


def resolve_judge(
    *,
    env: dict | None = None,
    embedder=None,
    embedder_loader=None,
) -> Judge | None:
    """Resolve the judge tier from env (spec: anthropic -> embedding -> none).
    `embedder`/`embedder_loader` are injection seams; default loads FastEmbed."""
    from cairn.config import cairn_env, judge_config

    e = env if env is not None else dict(cairn_env())
    mode, model, timeout = judge_config(e)
    if mode in ("none", "off", "0", "false", "no"):
        return None

    def _load_embedding_judge() -> EmbeddingJudge | None:
        nonlocal embedder
        try:
            if embedder is None:
                if embedder_loader is not None:
                    embedder = embedder_loader()
                else:
                    from cairn.embed import get_embedder

                    embedder = get_embedder("fastembed")
            return EmbeddingJudge(embedder)
        except Exception:
            return None

    emb_judge = _load_embedding_judge()
    if mode == "anthropic":
        key = e.get("ANTHROPIC_API_KEY")
        if key:
            return LLMJudge(api_key=key, model=model, timeout=timeout, fallback=emb_judge)
        return emb_judge  # no key -> degrade
    return emb_judge  # "embedding" / default
