# Layer B Semantic Judge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a semantic memory-worthiness judge (embedding-prototype default, opt-in LLM with full distillation) that separates durable memories from ephemeral chatter, combined 50/50 with the existing heuristic.

**Architecture:** New `src/cairn/ingest/judge.py` (Judgment/Judge protocol, `EmbeddingJudge`, `LLMJudge`, `resolve_judge`). The pipeline gains `ingest_transcripts()` (plural) that collects deduped candidates across all transcripts, judges them in ONE batch, then gates/distills/writes — `ingest_transcript()` (singular) stays API-compatible. `ExtractiveDistiller` consumes judgments (LLM title + `[context]` distilled / `[verbatim]` original) and gets the word-boundary title fix. The plugin SessionEnd hook detaches the sweep.

**Tech Stack:** Python 3.12, stdlib `urllib.request` for the Anthropic call (no new deps), existing `Embedder` protocol/FastEmbed, pytest. Spec: `docs/specs/2026-06-12-layer-b-semantic-judge-design.md`. Branch `feat/layer-b-judge` (exists, spec committed).

---

## File structure

```
src/cairn/ingest/judge.py     # CREATE: Judgment, Judge, EmbeddingJudge, LLMJudge, resolve_judge
src/cairn/config.py           # MODIFY: judge_config() env resolution
src/cairn/ingest/models.py    # MODIFY: Candidate +judgment/+importance; IngestReport +judge_tier/+judge_degraded
src/cairn/ingest/distill.py   # MODIFY: _truncate_title; judgment-aware ExtractiveDistiller
src/cairn/vault/write.py      # MODIFY: YAML width (no title folding)
src/cairn/ingest/pipeline.py  # MODIFY: ingest_transcripts() plural; combined score
src/cairn/ingest/__init__.py  # MODIFY: export new names
src/cairn/cli.py              # MODIFY: ingest/sweep use plural + resolve_judge; judge line in output
plugin/scripts/session-end.sh # MODIFY: detach the sweep
scripts/eval_judge.py         # CREATE: AUC/PR eval harness (offline)
tests/ingest/test_judge.py    # CREATE
tests/ingest/test_distill_judged.py  # CREATE
tests/ingest/test_pipeline.py # MODIFY: plural + combined-score tests
tests/test_cli.py             # MODIFY: judge output line test
tests/fixtures/judge_eval_synthetic.jsonl  # CREATE: synthetic labeled fixture (CI)
CHANGELOG.md / src/cairn/__init__.py       # MODIFY: 0.8.0
```

**Privacy note (spec amendment):** the repo is public, so the *real* labeled corpus is NOT committed — it lives at `~/.cache/agentcairn/judge_labels.jsonl` (local). CI uses the synthetic fixture; validation results are quoted in the PR. Task 9 amends the spec wording.

Run everything from the repo root with `uv run`. Pre-commit runs ruff + pytest: **run `git commit` as its own command, confirm it landed with `git log -1` (ruff reformat rejects the first attempt sometimes — re-add and re-commit), never pipe commit through `tail`.**

---

## Task 1: Judgment model + EmbeddingJudge

**Files:**
- Create: `src/cairn/ingest/judge.py`
- Create: `tests/ingest/test_judge.py`

- [ ] **Step 1: Write the failing tests** — create `tests/ingest/test_judge.py`:

```python
# tests/ingest/test_judge.py
# SPDX-License-Identifier: Apache-2.0
from cairn.ingest.judge import (
    _DURABLE_PROTOTYPES,
    _EPHEMERAL_PROTOTYPES,
    EmbeddingJudge,
    Judgment,
)


class StubEmbedder:
    """Maps durable-ish texts near axis-0, ephemeral-ish near axis-1.
    The FakeEmbedder's hash vectors are NOT semantic, so judge tests use this
    purpose-built stub: prototypes and candidates land on designed clusters."""

    model_id = "stub"
    dim = 2

    def _vec(self, text: str) -> list[float]:
        if text.startswith("D:") or text in _DURABLE_PROTOTYPES:
            return [1.0, 0.05]
        if text.startswith("E:") or text in _EPHEMERAL_PROTOTYPES:
            return [0.05, 1.0]
        return [0.5, 0.5]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


def test_judgment_defaults():
    j = Judgment(durability=0.7)
    assert j.title is None and j.distilled is None


def test_embedding_judge_separates_clusters():
    judge = EmbeddingJudge(StubEmbedder())
    out = judge.judge(["D: we decided to always rebase-merge", "E: check CI on PR #76"])
    assert len(out) == 2
    assert out[0].durability > 0.5 > out[1].durability
    # embedding tier never produces title/distilled
    assert out[0].title is None and out[0].distilled is None


def test_embedding_judge_durability_clamped_01():
    judge = EmbeddingJudge(StubEmbedder())
    for j in judge.judge(["D: a", "E: b", "neutral text"]):
        assert 0.0 <= j.durability <= 1.0


def test_embedding_judge_neutral_text_near_half():
    judge = EmbeddingJudge(StubEmbedder())
    (j,) = judge.judge(["neutral text"])
    assert 0.35 <= j.durability <= 0.65  # equidistant -> margin ~0 -> ~0.5


def test_embedding_judge_empty_input():
    assert EmbeddingJudge(StubEmbedder()).judge([]) == []
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_judge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.judge'`.

- [ ] **Step 3: Create `src/cairn/ingest/judge.py`:**

```python
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
import urllib.error
import urllib.request
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
    "Key architectural decision: the markdown vault is the source of truth, the index is disposable.",
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
```

- [ ] **Step 4: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_judge.py -q` → 5 passed.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/judge.py tests/ingest/test_judge.py && git commit -m "feat(judge): Judgment model + EmbeddingJudge (prototype cosine margin)"
```
Confirm with `git log --oneline -1` that the commit landed (re-add + re-commit if ruff reformatted).

---

## Task 2: LLMJudge + resolve_judge + config

**Files:**
- Modify: `src/cairn/ingest/judge.py`, `src/cairn/config.py`
- Test: `tests/ingest/test_judge.py`

- [ ] **Step 1: Add `judge_config` to `src/cairn/config.py`** (append at the end):

```python
_DEFAULT_JUDGE_MODEL = "claude-haiku-4-5"
_DEFAULT_JUDGE_TIMEOUT = 10.0


def judge_config(env: Mapping[str, str] | None = None) -> tuple[str, str, float]:
    """Resolve (mode, model, timeout) for the Layer-B judge.
    mode ← CAIRN_JUDGE: 'anthropic' | 'embedding' | 'none' (default 'embedding').
    model ← CAIRN_JUDGE_MODEL; timeout ← CAIRN_JUDGE_TIMEOUT seconds."""
    if env is None:
        env = os.environ
    mode = (env.get("CAIRN_JUDGE") or "embedding").strip().lower()
    model = env.get("CAIRN_JUDGE_MODEL") or _DEFAULT_JUDGE_MODEL
    try:
        timeout = float(env.get("CAIRN_JUDGE_TIMEOUT") or _DEFAULT_JUDGE_TIMEOUT)
    except ValueError:
        timeout = _DEFAULT_JUDGE_TIMEOUT
    return mode, model, timeout
```

- [ ] **Step 2: Append the failing tests** to `tests/ingest/test_judge.py`:

```python
def test_llm_judge_parses_batched_response(monkeypatch):
    import cairn.ingest.judge as jmod

    def fake_request(payload, api_key, timeout):
        # assert the batch shape: one request, all texts numbered
        body = payload["messages"][0]["content"]
        assert "[0]" in body and "[1]" in body
        return {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '[{"i": 0, "durability": 0.9, "title": "Rebase-merge convention",'
                        ' "distilled": "Always rebase-merge approved PRs."},'
                        ' {"i": 1, "durability": 0.1, "title": null, "distilled": null}]'
                    ),
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    out = judge.judge(["we always rebase-merge", "check the CI please now"])
    assert out[0].durability == 0.9 and out[0].title == "Rebase-merge convention"
    assert out[0].distilled == "Always rebase-merge approved PRs."
    assert out[1].durability == 0.1 and out[1].title is None


def test_llm_judge_degrades_on_error(monkeypatch):
    import cairn.ingest.judge as jmod

    def boom(payload, api_key, timeout):
        raise TimeoutError("slow")

    monkeypatch.setattr(jmod, "_anthropic_request", boom)
    fallback = EmbeddingJudge(StubEmbedder())
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=1.0, fallback=fallback)
    out = judge.judge(["D: decision text here"])
    assert len(out) == 1 and out[0].durability > 0.5  # fallback judged it
    assert judge.degraded == 1


def test_llm_judge_degrades_on_malformed_json(monkeypatch):
    import cairn.ingest.judge as jmod

    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {"content": [{"type": "text", "text": "not json"}]},
    )
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=1.0, fallback=EmbeddingJudge(StubEmbedder()))
    out = judge.judge(["D: decision"])
    assert len(out) == 1 and judge.degraded == 1


def test_llm_judge_discards_overlong_distillation(monkeypatch):
    import cairn.ingest.judge as jmod

    text = "short decision"
    monkeypatch.setattr(
        jmod,
        "_anthropic_request",
        lambda payload, api_key, timeout: {
            "content": [
                {"type": "text", "text": json.dumps([{"i": 0, "durability": 0.8, "title": "T", "distilled": "x" * 500}])}
            ]
        },
    )
    import json

    judge = jmod.LLMJudge(api_key="k", model="m", timeout=1.0)
    (j,) = judge.judge([text])
    assert j.durability == 0.8 and j.distilled is None  # >4x verbatim length -> discarded


def test_resolve_judge_modes(monkeypatch):
    from cairn.ingest.judge import LLMJudge, resolve_judge

    # none -> None
    assert resolve_judge(env={"CAIRN_JUDGE": "none"}, embedder=StubEmbedder()) is None
    # embedding (default) -> EmbeddingJudge
    j = resolve_judge(env={}, embedder=StubEmbedder())
    assert isinstance(j, EmbeddingJudge)
    # anthropic without key -> degrades to embedding
    j2 = resolve_judge(env={"CAIRN_JUDGE": "anthropic"}, embedder=StubEmbedder())
    assert isinstance(j2, EmbeddingJudge)
    # anthropic with key -> LLMJudge with embedding fallback
    j3 = resolve_judge(
        env={"CAIRN_JUDGE": "anthropic", "ANTHROPIC_API_KEY": "k"}, embedder=StubEmbedder()
    )
    assert isinstance(j3, LLMJudge)


def test_resolve_judge_no_embedder_is_none():
    from cairn.ingest.judge import resolve_judge

    def broken_loader():
        raise RuntimeError("no model")

    assert resolve_judge(env={}, embedder_loader=broken_loader) is None
```

NOTE: `import json` placement in `test_llm_judge_discards_overlong_distillation` is shown mid-function in the draft above — put `import json` at the TOP of the test file instead (module level) and drop the inline import. Adjust when writing.

- [ ] **Step 3: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_judge.py -q`
Expected: FAIL — `cannot import name 'LLMJudge'` (and resolve_judge).

- [ ] **Step 4: Append to `src/cairn/ingest/judge.py`:**

```python
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
_MAX_DISTILL_RATIO = 4  # distilled longer than 4x verbatim -> discarded

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
    """One batched Messages call judging all candidates; any failure degrades to
    the fallback judge (or neutral 0.5 judgments if none) and counts in .degraded."""

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
        try:
            return self._judge_llm(texts)
        except Exception:
            self.degraded += len(texts)
            if self._fallback is not None:
                return self._fallback.judge(texts)
            return [Judgment(durability=0.5) for _ in texts]

    def _judge_llm(self, texts: list[str]) -> list[Judgment]:
        numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(texts))
        payload = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": _PROMPT + numbered}],
        }
        resp = _anthropic_request(payload, self._api_key, self._timeout)
        raw = "".join(b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text")
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.strip("`").lstrip("json").strip()
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
    if mode in ("none", "off", "0", "false"):
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
```

- [ ] **Step 5: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_judge.py -q` → all pass (11).
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/judge.py src/cairn/config.py tests/ingest/test_judge.py && git commit -m "feat(judge): LLMJudge (batched anthropic, silent degradation) + resolve_judge"
```
Confirm with `git log --oneline -1`.

---

## Task 3: Title fix (all tiers)

**Files:**
- Modify: `src/cairn/ingest/distill.py`, `src/cairn/vault/write.py`
- Test: `tests/ingest/test_distill_judged.py` (create)

- [ ] **Step 1: Create `tests/ingest/test_distill_judged.py` with the title tests:**

```python
# tests/ingest/test_distill_judged.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.ingest.distill import ExtractiveDistiller, _truncate_title
from cairn.ingest.models import Candidate


def _cand(text: str, **kw) -> Candidate:
    return Candidate(
        text=text,
        session_id="s",
        cwd="/Users/x/proj",
        git_branch="main",
        timestamp="2026-06-12T00:00:00Z",
        source_path=Path("/tmp/s.jsonl"),
        **kw,
    )


def test_truncate_title_word_boundary():
    text = (
        "yes, but also Google's PageSpeed Insights claims our robots.txt is malformed. "
        "Can you look into that?"
    )
    t = _truncate_title(text)
    assert len(t) <= 80
    assert not t.rstrip("…").endswith(" Ca")  # no mid-word fragment
    assert t.endswith("…")


def test_truncate_title_short_text_unchanged():
    assert _truncate_title("short title") == "short title"


def test_long_title_does_not_fold_in_yaml(tmp_path):
    from cairn.ingest.distill import write_derived_note

    text = "a" * 30 + " " + "b" * 30 + " " + "c" * 30  # forces near-80 title
    note = ExtractiveDistiller().distill(_cand(text))
    p = write_derived_note(note, tmp_path)
    raw = p.read_text()
    title_lines = [ln for ln in raw.splitlines() if ln.startswith("title:")]
    assert len(title_lines) == 1
    # the line AFTER title: must be a new key, not a folded continuation
    lines = raw.splitlines()
    idx = lines.index(title_lines[0])
    assert lines[idx + 1].split(":")[0] in {"type", "permalink", "tags", "created", "source", "importance"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_distill_judged.py -q`
Expected: FAIL — `cannot import name '_truncate_title'`.

- [ ] **Step 3: In `src/cairn/ingest/distill.py`**, add below `_slugify`:

```python
def _truncate_title(text: str, limit: int = 80) -> str:
    """First line, cut at a word boundary with an ellipsis — never mid-word
    (the '…malformed. Ca' bug)."""
    first = text.strip().splitlines()[0].strip()
    if len(first) <= limit:
        return first
    cut = first[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.") + "…"
```

and change the title line in `ExtractiveDistiller.distill` from
`title = candidate.text.strip().splitlines()[0][:80]` to
`title = _truncate_title(candidate.text)`.

- [ ] **Step 4: In `src/cairn/vault/write.py`**, change the dumps call to prevent width-folding:

```python
    text = frontmatter.dumps(post, sort_keys=False, width=4096)
```

(`python-frontmatter` forwards kwargs to the YAML handler's `yaml.dump`; `width=4096` stops long plain scalars folding across lines.)

- [ ] **Step 5: Run full suite + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass (vault round-trip tests must stay green).
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/distill.py src/cairn/vault/write.py tests/ingest/test_distill_judged.py && git commit -m "fix(distill): word-boundary title truncation + no YAML title folding"
```
Confirm with `git log --oneline -1`.

---

## Task 4: Judgment-aware Candidate + Distiller

**Files:**
- Modify: `src/cairn/ingest/models.py`, `src/cairn/ingest/distill.py`
- Test: `tests/ingest/test_distill_judged.py`

- [ ] **Step 1: Append the failing tests** to `tests/ingest/test_distill_judged.py`:

```python
def test_distill_with_llm_judgment_writes_distilled_plus_verbatim():
    from cairn.ingest.judge import Judgment

    cand = _cand(
        "we should always run the corpus replay before changing redaction",
        judgment=Judgment(
            durability=0.9,
            title="Corpus replay before redaction changes",
            distilled="Always run the corpus replay before changing redaction.",
        ),
        importance=0.83,
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.frontmatter["title"] == "Corpus replay before redaction changes"
    assert note.frontmatter["importance"] == 0.83
    assert "- [context] Always run the corpus replay before changing redaction. #ingested" in note.body
    assert "- [verbatim] we should always run the corpus replay" in note.body


def test_distill_without_judgment_keeps_verbatim_format():
    cand = _cand("we decided to always do the thing")
    note = ExtractiveDistiller().distill(cand)
    assert note.body.startswith("- [context] we decided to always do the thing")
    assert "[verbatim]" not in note.body


def test_dedup_identity_unchanged_by_judgment():
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.judge import Judgment

    text = "we decided to always do the thing"
    plain = ExtractiveDistiller().distill(_cand(text))
    judged = ExtractiveDistiller().distill(
        _cand(text, judgment=Judgment(durability=0.9, title="T", distilled="D."), importance=0.9)
    )
    # permalink (slug + content hash) is derived from the VERBATIM text only
    assert plain.permalink == judged.permalink
    assert content_hash(text) in plain.permalink or plain.permalink.endswith(content_hash(text)[:8])
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_distill_judged.py -q`
Expected: FAIL — `Candidate` has no `judgment` field.

- [ ] **Step 3: In `src/cairn/ingest/models.py`**, extend `Candidate` (append after `project`):

```python
    judgment: "Judgment | None" = None  # Layer-B verdict (set by the pipeline)
    importance: float | None = None  # combined score (heuristic x judge); distiller uses it
```

and add the import at the top of `models.py`:

```python
from cairn.ingest.judge import Judgment
```

(no cycle: `judge.py` imports nothing from `models.py`). Use the plain name (not the quoted string) in the field annotation if the import is at module level: `judgment: Judgment | None = None`.

- [ ] **Step 4: In `src/cairn/ingest/distill.py`**, replace `ExtractiveDistiller.distill` with:

```python
    def distill(self, candidate: Candidate) -> Note:
        h = content_hash(candidate.text)
        slug = f"{_slugify(candidate.text)}-{h[:8]}"
        j = candidate.judgment
        title = (j.title if j and j.title else None) or _truncate_title(candidate.text)
        imp = candidate.importance if candidate.importance is not None else score(candidate.text)
        frontmatter = {
            "title": title,
            "type": "memory",
            "permalink": slug,
            "tags": ["ingested"],
            "created": candidate.timestamp,
            "source": f"memory://session/{candidate.session_id}",
            "importance": round(imp, 3),
        }
        verbatim = candidate.text.strip()
        if j and j.distilled:
            body = f"- [context] {j.distilled.strip()} #ingested\n- [verbatim] {verbatim}\n"
        else:
            body = f"- [context] {verbatim} #ingested\n"
        return Note(permalink=slug, frontmatter=frontmatter, body=body)
```

(Slug/hash stay derived from `candidate.text` — the verbatim — so identity is stable under LLM nondeterminism.)

- [ ] **Step 5: Run full suite + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/models.py src/cairn/ingest/distill.py tests/ingest/test_distill_judged.py && git commit -m "feat(distill): judgment-aware notes — LLM title + [context] distilled + [verbatim]"
```
Confirm with `git log --oneline -1`.

---

## Task 5: Pipeline — one judge call per run + combined score

**Files:**
- Modify: `src/cairn/ingest/pipeline.py`, `src/cairn/ingest/models.py`, `src/cairn/ingest/__init__.py`
- Test: `tests/ingest/test_pipeline.py`

- [ ] **Step 1: Extend `IngestReport` in `src/cairn/ingest/models.py`** — add two fields after `event_kinds` and surface them in `to_dict`:

```python
    judge_tier: str = "none"  # "llm" | "embedding" | "none"
    judge_degraded: int = 0  # candidates that fell back a tier
```

and in `to_dict()` add:

```python
            "judge_tier": self.judge_tier,
            "judge_degraded": self.judge_degraded,
```

- [ ] **Step 2: Append the failing tests** to `tests/ingest/test_pipeline.py`:

```python
def test_ingest_transcripts_judges_once_and_gates_by_combined_score(tmp_path):
    """One judge call per run across transcripts; combined = 0.5*heuristic+0.5*durability."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    calls = []

    class SpyJudge:
        def judge(self, texts):
            calls.append(list(texts))
            # first candidate durable, second ephemeral
            return [
                Judgment(durability=1.0, title="Durable decision", distilled="The decision.")
                if "decided" in t
                else Judgment(durability=0.0)
                for t in texts
            ]

    t1 = Transcript(
        session_id="s1", cwd="/Users/x/p", git_branch="main", path=tmp_path / "s1.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, "We decided to always rebase-merge approved PRs because it is important.")],
    )
    t2 = Transcript(
        session_id="s2", cwd="/Users/x/p", git_branch="main", path=tmp_path / "s2.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, "Check the CI status on PR #76 and merge it if everything is green because we should ship.")],
    )
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts([t1, t2], vault_root=vault, ledger=ledger, judge=SpyJudge())

    assert len(calls) == 1 and len(calls[0]) == 2  # ONE batched call for both transcripts
    # durable: 0.5*h + 0.5*1.0 >= 0.5 -> written; ephemeral: 0.5*h + 0.5*0 < 0.5 -> gated
    assert len(report.written) == 1
    assert report.gated_out >= 1
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "- [context] The decision. #ingested" in blob
    assert "- [verbatim] We decided" in blob
    assert "CI status" not in blob


def test_ingest_transcripts_without_judge_matches_legacy_behavior(tmp_path):
    from cairn.ingest.pipeline import ingest_transcripts

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts([_transcript(tmp_path)], vault_root=vault, ledger=ledger, judge=None)
    assert report.judge_tier == "none"
    assert len(report.written) == 1  # same as today's singular behavior


def test_ingest_transcript_singular_still_works(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcript(_transcript(tmp_path), vault_root=vault, ledger=ledger)
    assert len(report.written) == 1  # unchanged public API


def test_report_judge_tier_recorded(tmp_path):
    from cairn.ingest.judge import EmbeddingJudge
    from cairn.ingest.pipeline import ingest_transcripts
    from tests.ingest.test_judge import StubEmbedder

    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    report = ingest_transcripts(
        [_transcript(tmp_path)], vault_root=vault, ledger=ledger, judge=EmbeddingJudge(StubEmbedder())
    )
    assert report.judge_tier == "embedding"
```

- [ ] **Step 3: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_pipeline.py -q`
Expected: FAIL — `cannot import name 'ingest_transcripts'`.

- [ ] **Step 4: Restructure `src/cairn/ingest/pipeline.py`.** Replace `ingest_transcript` with the pair (keep `select_candidates` as-is; add imports `from dataclasses import replace` already present, plus `from cairn.ingest.importance import score` and `from cairn.ingest.judge import Judge, LLMJudge`):

```python
def _judge_tier_name(judge: Judge | None) -> str:
    if judge is None:
        return "none"
    from cairn.ingest.judge import EmbeddingJudge, LLMJudge

    if isinstance(judge, LLMJudge):
        return "llm"
    if isinstance(judge, EmbeddingJudge):
        return "embedding"
    return type(judge).__name__.lower()


def ingest_transcripts(
    transcripts: list[Transcript],
    *,
    vault_root: Path,
    ledger: DedupLedger,
    threshold: float = KEEP_THRESHOLD,
    judge: Judge | None = None,
    distiller: Distiller | None = None,
    subdir: str = "memories",
    dry_run: bool = False,
) -> IngestReport:
    """Ingest a batch of transcripts with ONE judge call across all new candidates.
    Order per spec: redact -> dedup -> judge (batched) -> combined gate -> distill -> write."""
    distiller = distiller or ExtractiveDistiller()
    report = IngestReport()
    report.judge_tier = _judge_tier_name(judge)
    kind_totals: Counter = Counter()

    # Phase A: collect redacted, deduped candidates across all transcripts.
    pending: list[tuple[Candidate, str]] = []  # (candidate, content hash)
    seen_this_run: set[str] = set()
    for transcript in transcripts:
        kind_totals.update(
            transcript.kind_counts or Counter(e.kind.value for e in transcript.events)
        )
        candidates = select_candidates(transcript)
        report.authored += len(candidates)
        for cand in candidates:
            red = redact(cand.text)
            report.redactions += red.count
            cand = replace(cand, text=red.text)
            h = content_hash(cand.text)
            if ledger.seen(h) or h in seen_this_run:
                report.deduped += 1
                continue
            seen_this_run.add(h)
            pending.append((cand, h))
    report.event_kinds = dict(kind_totals)

    # Phase B: ONE batched judge call (never raises; LLM degrades internally).
    judgments = judge.judge([c.text for c, _ in pending]) if judge and pending else []
    if judge is not None and hasattr(judge, "degraded"):
        report.judge_degraded = judge.degraded

    # Phase C: combined gate -> distill -> write.
    for idx, (cand, h) in enumerate(pending):
        heuristic = score(cand.text)
        if judgments:
            j = judgments[idx]
            combined = max(0.0, min(1.0, 0.5 * heuristic + 0.5 * j.durability))
            cand = replace(cand, judgment=j, importance=combined)
        else:
            combined = heuristic
            cand = replace(cand, importance=combined)
        if combined < threshold:
            report.gated_out += 1
            continue
        report.candidates += 1
        note = distiller.distill(cand)
        if dry_run:
            continue
        path = write_derived_note(note, vault_root, subdir=subdir)
        ledger.add(h)
        report.written.append(path)
    return report


def ingest_transcript(
    transcript: Transcript,
    *,
    vault_root: Path,
    ledger: DedupLedger,
    threshold: float = KEEP_THRESHOLD,
    distiller: Distiller | None = None,
    subdir: str = "memories",
    dry_run: bool = False,
) -> IngestReport:
    """Single-transcript wrapper (kept for API compatibility; judge-less)."""
    return ingest_transcripts(
        [transcript],
        vault_root=vault_root,
        ledger=ledger,
        threshold=threshold,
        judge=None,
        distiller=distiller,
        subdir=subdir,
        dry_run=dry_run,
    )
```

NOTE: `is_important` is no longer used — remove its import; the gate is now the explicit `combined < threshold` comparison (identical semantics for tier 0 since `combined == heuristic`).

- [ ] **Step 5: Export `ingest_transcripts`, `Judgment`, `resolve_judge`** from `src/cairn/ingest/__init__.py` (add to imports and `__all__`):

```python
from cairn.ingest.judge import Judgment, resolve_judge
from cairn.ingest.pipeline import ingest_transcript, ingest_transcripts
```

- [ ] **Step 6: Run full suite + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass (existing pipeline invariants: redact-first, dedup-before-gate, dry-run untouched ledger).
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/pipeline.py src/cairn/ingest/models.py src/cairn/ingest/__init__.py tests/ingest/test_pipeline.py && git commit -m "feat(pipeline): ingest_transcripts — one judge call per run, combined gate"
```
Confirm with `git log --oneline -1`.

---

## Task 6: CLI wiring + plugin hook detach

**Files:**
- Modify: `src/cairn/cli.py`, `plugin/scripts/session-end.sh`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Append the failing test** to `tests/test_cli.py`:

```python
def test_ingest_reports_judge_tier(tmp_path):
    import json as _j

    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "t.jsonl").write_text(
        _j.dumps({"type": "user", "sessionId": "s", "cwd": "/Users/x/proj",
                  "message": {"role": "user", "content": "we decided to always rebase-merge the branch"}})
        + "\n"
    )
    vault = tmp_path / "vault"
    r = runner.invoke(
        app,
        ["ingest", "--vault", str(vault), "--transcripts-dir", str(tmp_path / "projects"),
         "--ledger", str(tmp_path / "led.sha256")],
        env={"CAIRN_JUDGE": "none"},
    )
    assert r.exit_code == 0, r.output
    assert "judge: none" in r.output
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k judge_tier -q`
Expected: FAIL — output lacks "judge:".

- [ ] **Step 3: Rewire `cli.py`'s `ingest` command.** Replace the per-transcript loop (the `totals`/`kinds` block from Task SI-5) with a single batched call:

```python
    from cairn.ingest.judge import resolve_judge
    from cairn.ingest.pipeline import ingest_transcripts

    transcripts = [parse_transcript(tp) for tp in paths]
    judge = resolve_judge()
    rep = ingest_transcripts(
        transcripts,
        vault_root=vault,
        ledger=led,
        threshold=threshold,
        judge=judge,
        dry_run=dry_run,
    )
    prefix = "[dry-run] " if dry_run else ""
    typer.echo(
        f"{prefix}{rep.authored} authored · {rep.candidates} candidates · "
        f"{rep.redactions} redactions · {rep.deduped} deduped · "
        f"{rep.gated_out} gated · {len(rep.written)} written · judge: {rep.judge_tier}"
        + (f" ({rep.judge_degraded} degraded)" if rep.judge_degraded else "")
    )
    skipped = {k: v for k, v in rep.event_kinds.items() if k != "authored_user"}
    if skipped:
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]))
        typer.echo(f"  skipped (non-authored): {breakdown}")
```

Make the **same change in `sweep`** (its loop currently sums `written`): build `transcripts`, call `ingest_transcripts(... judge=resolve_judge() ...)`, use `len(rep.written)` for the summary line.

- [ ] **Step 4: Detach the sweep in `plugin/scripts/session-end.sh`** — replace the sweep line:

```sh
# Detach: the sweep (and any LLM judge call inside it) must never block session
# teardown. nohup + & + disown-equivalent; logs discarded by design.
nohup sh -c "$CAIRN sweep --vault \"$VAULT\" --index \"$INDEX\" ${CWD:+--project \"$CWD\"}" >/dev/null 2>&1 &
exit 0
```

(Keep the `init` line synchronous above it — it's fast and idempotent.)

- [ ] **Step 5: Run full suite + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass (existing ingest/sweep CLI tests adapt: same output fields plus the judge suffix — update any failing assertions to match the new line, e.g. `"1 written"` still appears).
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/cli.py plugin/scripts/session-end.sh tests/test_cli.py && git commit -m "feat(cli): batched judge wiring in ingest/sweep; plugin sweep detached"
```
Confirm with `git log --oneline -1`.

---

## Task 7: Eval harness + synthetic fixture

**Files:**
- Create: `scripts/eval_judge.py`, `tests/fixtures/judge_eval_synthetic.jsonl`, `tests/ingest/test_eval_harness.py`

- [ ] **Step 1: Create `tests/fixtures/judge_eval_synthetic.jsonl`** (synthetic — the real labeled corpus stays local/uncommitted for privacy):

```jsonl
{"text": "We decided to always rebase-merge approved PRs and delete the branch.", "label": "durable"}
{"text": "Key lesson: never trust role==user to mean a human wrote it.", "label": "durable"}
{"text": "I prefer plain-text clarifying questions over popups.", "label": "durable"}
{"text": "The root cause was the regex including slashes in the token class.", "label": "durable"}
{"text": "Check CI on PR #76 and merge if green.", "label": "ephemeral"}
{"text": "I reopened and merged pr12, make a quick pass.", "label": "ephemeral"}
{"text": "Production branch is set to main, build watch paths are *.", "label": "ephemeral"}
{"text": "Run the test suite again and paste the output.", "label": "ephemeral"}
```

- [ ] **Step 2: Create `scripts/eval_judge.py`:**

```python
#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Layer-B validation harness: score a labeled corpus (durable/ephemeral) with the
EmbeddingJudge and the importance heuristic; report AUC + precision/recall at the
0.5 gate. Offline, no keys. The REAL labeled corpus lives locally (privacy):
  ~/.cache/agentcairn/judge_labels.jsonl   # {"text": ..., "label": "durable"|"ephemeral"}
Usage:
  uv run python scripts/eval_judge.py [--labels PATH] [--embedder fastembed|fake]
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def auc(labels: list[int], scores: list[float]) -> float:
    """Rank-based AUC (probability a positive outranks a negative); ties count half."""
    pos = [s for s, y in zip(scores, labels, strict=True) if y == 1]
    neg = [s for s, y in zip(scores, labels, strict=True) if y == 0]
    if not pos or not neg:
        return float("nan")
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def pr_at(labels: list[int], scores: list[float], threshold: float) -> tuple[float, float]:
    tp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y == 1)
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if s >= threshold and y == 0)
    fn = sum(1 for s, y in zip(scores, labels, strict=True) if s < threshold and y == 1)
    precision = tp / (tp + fp) if tp + fp else float("nan")
    recall = tp / (tp + fn) if tp + fn else float("nan")
    return precision, recall


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", default=str(Path.home() / ".cache/agentcairn/judge_labels.jsonl"))
    ap.add_argument("--embedder", default="fastembed")
    ap.add_argument("--threshold", type=float, default=0.5)
    args = ap.parse_args()

    rows = [json.loads(ln) for ln in Path(args.labels).read_text().splitlines() if ln.strip()]
    texts = [r["text"] for r in rows]
    labels = [1 if r["label"] == "durable" else 0 for r in rows]

    from cairn.embed import get_embedder
    from cairn.ingest.importance import score as heuristic_score
    from cairn.ingest.judge import EmbeddingJudge

    judge = EmbeddingJudge(get_embedder(args.embedder))
    durability = [j.durability for j in judge.judge(texts)]
    heuristic = [heuristic_score(t) for t in texts]
    combined = [max(0.0, min(1.0, 0.5 * h + 0.5 * d)) for h, d in zip(heuristic, durability, strict=True)]

    for name, scores in [("heuristic", heuristic), ("embedding", durability), ("combined", combined)]:
        p, r = pr_at(labels, scores, args.threshold)
        print(f"{name:10s} AUC={auc(labels, scores):.3f}  P@{args.threshold}={p:.3f}  R@{args.threshold}={r:.3f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Create `tests/ingest/test_eval_harness.py`:**

```python
# tests/ingest/test_eval_harness.py
# SPDX-License-Identifier: Apache-2.0
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from eval_judge import auc, pr_at  # noqa: E402


def test_auc_perfect_separation():
    assert auc([1, 1, 0, 0], [0.9, 0.8, 0.2, 0.1]) == 1.0


def test_auc_random_is_half():
    assert auc([1, 0], [0.5, 0.5]) == 0.5


def test_pr_at_threshold():
    p, r = pr_at([1, 1, 0], [0.9, 0.4, 0.6], 0.5)
    assert p == 0.5 and r == 0.5
```

- [ ] **Step 4: Run + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_eval_harness.py -q` → 3 passed.
Sanity (offline, fake embedder, synthetic fixture):
`uv run python scripts/eval_judge.py --labels tests/fixtures/judge_eval_synthetic.jsonl --embedder fake` → prints three AUC lines (values arbitrary with the fake embedder; must not crash).
```bash
cd /Users/ccf/git/agentcairn && git add scripts/eval_judge.py tests/fixtures/judge_eval_synthetic.jsonl tests/ingest/test_eval_harness.py && git commit -m "feat(eval): Layer-B judge validation harness (AUC/PR, offline)"
```
Confirm with `git log --oneline -1`.

---

## Task 8: Validation gate (manual, local — controller executes)

**Files:** none committed (results go in the PR description; real labels stay local).

- [ ] **Step 1: Build the local labeled corpus.** Dump the candidate texts:
  - durable/ephemeral-label the **69 current vault notes** (verbatim text from each note body) and **~100 sampled gated-out authored turns** (`uv run cairn ingest --vault /tmp/x --dry-run` doesn't emit texts — instead parse transcripts directly: `parse_transcript` each, `select_candidates`, redact, take texts). Write `~/.cache/agentcairn/judge_labels.jsonl` with `{"text": ..., "label": ...}` rows. Labeling is human/controller judgment per the spec's durable/ephemeral definitions.
- [ ] **Step 2: Run the harness with the real embedder:** `uv run python scripts/eval_judge.py` (fastembed downloads the model if needed).
- [ ] **Step 3: Decision gate (spec):** Tier-1 default stands **only if** `combined` (and/or `embedding`) AUC beats `heuristic` AUC on this corpus. If not: change `judge_config`'s default mode from `"embedding"` to `"none"` in `src/cairn/config.py` (one-line change + test update) and note the result.
- [ ] **Step 4: Record the three AUC/P/R lines verbatim in the PR description.**

---

## Task 9: Docs + 0.8.0

**Files:**
- Modify: `CHANGELOG.md`, `src/cairn/__init__.py`, `docs/specs/2026-06-12-layer-b-semantic-judge-design.md`, `README.md`

- [ ] **Step 1: Amend the spec's validation section** — replace the sentence
  "The labeling file and eval script are committed (offline, no keys) so the gate is re-runnable when prototypes or embedders change."
  with:
  "The eval script and a **synthetic** fixture are committed (offline, no keys); the **real labeled corpus is NOT committed** — the repo is public and the labels are the user's actual memory texts. It lives at `~/.cache/agentcairn/judge_labels.jsonl`; results are quoted in the PR."

- [ ] **Step 2: CHANGELOG** — insert under `## [Unreleased]`:

```markdown
## [0.8.0] - 2026-06-12

### Added
- **Layer B: semantic memory-worthiness judge.** Authored turns are now judged for durability (decision/preference/lesson vs ephemeral task chatter) and the score combines 50/50 with the importance heuristic at the same 0.5 gate. Default tier: a local **embedding-prototype judge** (cosine margin against curated exemplar sets, using the shipped FastEmbed model — no key, no new deps). Opt-in tier: `CAIRN_JUDGE=anthropic` (+`ANTHROPIC_API_KEY`) enables an **LLM judge** that additionally writes a descriptive title and a crisp distilled restatement — notes then carry `[context] <distilled>` plus the full `[verbatim]` original (non-lossy; enables future re-distillation). One batched LLM call per ingest run with a hard timeout (`CAIRN_JUDGE_TIMEOUT`, default 10s); any failure silently degrades a tier and is reported. `cairn ingest`/`sweep` report the judge tier; the plugin's SessionEnd sweep now runs detached so session close never waits.

### Fixed
- Note titles truncate at a word boundary with an ellipsis (no more mid-word "…Ca" fragments) and no longer fold across YAML lines.
```

Update link refs: `[Unreleased]` → `v0.8.0...HEAD`, add `[0.8.0]: https://github.com/ccf/agentcairn/compare/v0.7.2...v0.8.0`.

- [ ] **Step 3: Bump** `src/cairn/__init__.py` → `__version__ = "0.8.0"`.

- [ ] **Step 4: README** — in the "How it works" capture sentence, extend "redacts → dedups → importance-gates → distills" to "redacts → dedups → **judges (semantic durability; optional LLM distillation via `CAIRN_JUDGE=anthropic`)** → gates → distills".

- [ ] **Step 5: Full suite + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass.
```bash
cd /Users/ccf/git/agentcairn && git add CHANGELOG.md src/cairn/__init__.py docs/specs/2026-06-12-layer-b-semantic-judge-design.md README.md && git commit -m "chore(release): 0.8.0 — Layer B semantic judge"
```
Confirm with `git log --oneline -1`.

---

## Self-review (against the spec)

- **§ Judgment/Judge/tiers**: Tasks 1–2 (EmbeddingJudge prototypes + margin; LLMJudge batched + degradation; resolve_judge env matrix). ✓
- **§ Score combination + same threshold**: Task 5 Phase C (`0.5*h + 0.5*d`, `KEEP_THRESHOLD`). ✓
- **§ Note format ([context] distilled + [verbatim]; dedup on verbatim)**: Task 4 + identity test. ✓
- **§ Title fix all tiers (word boundary + YAML width)**: Task 3. ✓
- **§ One batched call per run; judge after dedup, before gate**: Task 5 Phase A/B ordering; CLI batches across transcripts (Task 6). ✓
- **§ Detached hook**: Task 6 Step 4. ✓
- **§ Report judge_tier/judge_degraded + CLI surface**: Tasks 5–6. ✓
- **§ Error handling (timeout/malformed→degrade; overlong distill discarded; no-embedder→none)**: Task 2 tests. ✓
- **§ Validation gate**: Tasks 7 (harness) + 8 (execution + default-flip decision); privacy amendment Task 9. ✓
- **§ Rollout 0.8.0, additive**: Task 9. ✓
- **§ Out of scope** (redistill, agent-as-judge, other providers, reflect): none added. ✓

**Type/name consistency:** `Judgment{durability,title,distilled}`; `Judge.judge(texts)->list[Judgment]`; `EmbeddingJudge(embedder)`; `LLMJudge(api_key=,model=,timeout=,fallback=)` with `.degraded`; `resolve_judge(env=,embedder=,embedder_loader=)`; `judge_config()->(mode,model,timeout)`; `ingest_transcripts(transcripts,*,vault_root,ledger,threshold,judge,distiller,subdir,dry_run)`; `Candidate.judgment/.importance`; `IngestReport.judge_tier/.judge_degraded`; `_truncate_title(text,limit=80)`. Used identically across tasks. No placeholders.
