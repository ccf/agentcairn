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
from dataclasses import dataclass
from pathlib import Path
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

    def judge(self, texts: list[str]) -> list[Judgment]:
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
_BATCH_SIZE = 40  # texts per Messages call: large batches risk output truncation

_PROMPT = """You judge whether each numbered message from a developer's coding-agent \
session is a DURABLE memory (decision, preference, lesson, durable fact, strategic \
direction) or EPHEMERAL chatter (task coordination, status checks, one-off process \
instructions). For each, return durability in [0,1] (1 = clearly durable), a short \
descriptive title (<=70 chars), and a crisp 1-2 sentence distillation of the durable \
fact. For ephemeral messages use null title/distilled.
Return ONLY a JSON array: [{"i": <index>, "durability": <float>, "title": <str|null>, \
"distilled": <str|null>}, ...] with one entry per input, in order.

Messages:
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

    def judge(self, texts: list[str]) -> list[Judgment]:
        if not texts:
            return []
        out: list[Judgment] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            chunk = texts[start : start + _BATCH_SIZE]
            try:
                out.extend(self._judge_llm(chunk))
            except Exception:
                self.degraded += len(chunk)
                if self._fallback is not None:
                    out.extend(self._fallback.judge(chunk))
                else:
                    out.extend(Judgment(durability=0.5) for _ in chunk)
        return out

    def _judge_llm(self, texts: list[str]) -> list[Judgment]:
        numbered = "\n".join(f"[{i}] {_judge_input(t)}" for i, t in enumerate(texts))
        payload = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": _PROMPT + numbered}],
        }
        resp = _anthropic_request(payload, self._api_key, self._timeout)
        raw = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").removeprefix("json").strip()
        items = json.loads(raw)
        by_i = {int(it["i"]): it for it in items}
        out: list[Judgment] = []
        for i, text in enumerate(texts):
            it = by_i.get(i)
            if it is None:
                raise ValueError(f"missing judgment for index {i}")
            durability = max(0.0, min(1.0, float(it["durability"])))
            title = it.get("title") or None
            distilled = it.get("distilled") or None
            if distilled and len(distilled) > _MAX_DISTILL_RATIO * max(len(text), 1):
                distilled = None
            if title and len(title) > 120:
                title = None
            out.append(Judgment(durability=durability, title=title, distilled=distilled))
        return out


class JudgedCache:
    """hash -> durability for already-judged-but-not-written candidates, so the
    LLM never re-judges the same text across runs. JSONL beside the dedup ledger.
    (Written candidates are dedup-ledgered and never reconsidered; this cache
    covers the gated-out ones, which stay pending forever.)"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._mem: dict[str, float] = {}
        if self.path.exists():
            for ln in self.path.read_text(encoding="utf-8").splitlines():
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                    self._mem[str(obj["h"])] = float(obj["d"])
                except Exception:
                    continue  # tolerate torn/corrupt lines — it's a rebuildable cache

    def get(self, h: str) -> float | None:
        return self._mem.get(h)

    def put(self, h: str, durability: float) -> None:
        if self._mem.get(h) == durability:
            return  # idempotent: no duplicate appends across runs
        self._mem[h] = durability
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"h": h, "d": durability}) + "\n")


def resolve_judge(
    *,
    env: dict | None = None,
    embedder=None,
    embedder_loader=None,
) -> Judge | None:
    """Resolve the judge tier from env (spec: anthropic -> embedding -> none).
    `embedder`/`embedder_loader` are injection seams; default loads FastEmbed."""
    import os as _os

    from cairn.config import judge_config

    e = env if env is not None else dict(_os.environ)
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
