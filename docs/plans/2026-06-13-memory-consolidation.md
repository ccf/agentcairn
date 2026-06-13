# Memory Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** During LLM-tier ingest, skip a memory that semantically duplicates an existing one, and mark an older memory `superseded_by` a newer version of the same evolving fact — fail-safe, so a wrong call never drops a *distinct* memory.

**Architecture:** A consolidation step in `ingest_transcripts` Phase C, post-distill/pre-write. Two injected, optional dependencies keep the pipeline decoupled from DuckDB/embeddings: a `Consolidator` (LLM classifier, cosine pre-gated) and a **text-based** `NeighborIndex` (`nearest(text)`/`add(...)`, embedding owned internally) spanning prior-index notes ∪ this-sweep writes. Verdicts: `DUPLICATE`→skip+ledger, `SUPERSEDES`→write new + mark old, `DISTINCT`→write. Active only when `judge_tier=="llm"`, both deps present, not dry-run; any error → `DISTINCT`.

**Tech Stack:** Python 3.12, `uv`, pytest, DuckDB (`array_cosine_similarity`), Anthropic Messages API (reusing `judge._anthropic_request`). Spec: `docs/specs/2026-06-13-memory-consolidation-design.md`.

**Conventions:**
- Tests: `uv run pytest`. Pre-commit runs ruff + ruff-format + pytest; **ruff-format reformats on the first commit attempt and aborts it** — when that happens, `git add -A` and re-run the same `git commit`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Refinement vs spec: `NeighborIndex` is **text-based** (`nearest(text)`, `add(permalink, text, ts)`) — it embeds internally — so the pipeline needs no embedder. This realizes the spec's decoupling intent; the spec's "batch-embed in the pipeline" becomes per-call embedding inside the CLI's `NeighborIndex`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cairn/ingest/consolidate.py` (new) | verdict enum, `Neighbor`, `NeighborIndex`/`Consolidator` protocols, `_CONSOLIDATE_GATE`, `LLMConsolidator`, `resolve_consolidator` | create |
| `src/cairn/config.py` | `consolidate` knob + `resolve_consolidate` | modify |
| `src/cairn/ingest/models.py` | `IngestReport.semantic_deduped` + `.superseded` (+`to_dict`) | modify |
| `src/cairn/ingest/distill.py` | `mark_superseded(path, by_permalink)` frontmatter editor | modify |
| `src/cairn/ingest/pipeline.py` | Phase C: consolidate step, timestamp ordering, injected deps | modify |
| `src/cairn/cli.py` | wire DuckDB-backed `NeighborIndex` + `resolve_consolidator`; report counts | modify |
| `scripts/eval_consolidate.py` (new) | gate validation on the real vault | create |
| `src/cairn/__init__.py`, `CHANGELOG.md` | 0.10.0 | modify |

---

## Task 1: `consolidate.py` — verdict, models, protocols, gate

**Files:** Create `src/cairn/ingest/consolidate.py`; Test `tests/ingest/test_consolidate.py`.

- [ ] **Step 1: Write the failing test**

Create `tests/ingest/test_consolidate.py`:

```python
# SPDX-License-Identifier: Apache-2.0
def test_verdict_values_and_neighbor():
    from cairn.ingest.consolidate import ConsolidationVerdict, Neighbor

    assert ConsolidationVerdict.DISTINCT == "distinct"
    assert ConsolidationVerdict.DUPLICATE == "duplicate"
    assert ConsolidationVerdict.SUPERSEDES == "supersedes"
    n = Neighbor(permalink="p", text="t", timestamp="t0")
    assert n.permalink == "p" and n.text == "t" and n.timestamp == "t0"


def test_gate_is_a_conservative_float():
    from cairn.ingest.consolidate import _CONSOLIDATE_GATE

    assert 0.5 < _CONSOLIDATE_GATE < 1.0  # a high cosine pre-gate
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_consolidate.py -v`
Expected: FAIL — `ModuleNotFoundError: cairn.ingest.consolidate`.

- [ ] **Step 3: Create the module skeleton**

Create `src/cairn/ingest/consolidate.py`:

```python
# src/cairn/ingest/consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Memory consolidation: collapse a new memory that semantically duplicates an
existing one, or mark an older memory superseded by a newer version of the same
evolving fact. LLM-classified above a cosine pre-gate; fail-safe (any uncertainty
or error -> DISTINCT, i.e. keep both). LLM tier only."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from cairn.ingest.judge import _anthropic_request

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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/ingest/test_consolidate.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/consolidate.py tests/ingest/test_consolidate.py
git commit -m "feat(consolidate): verdict, Neighbor, protocols, cosine gate"
```

---

## Task 2: `LLMConsolidator` + `resolve_consolidator`

**Files:** Modify `src/cairn/ingest/consolidate.py`; Test `tests/ingest/test_consolidate.py`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_consolidate.py`:

```python
def _resp(relation):
    return {"content": [{"type": "text", "text": __import__("json").dumps({"relation": relation})}]}


def test_llm_consolidator_parses_each_verdict(monkeypatch):
    import cairn.ingest.consolidate as cmod
    from cairn.ingest.consolidate import ConsolidationVerdict, LLMConsolidator, Neighbor

    nb = Neighbor(permalink="old", text="Fly RAM scaled to 2GB", timestamp="t1")
    for relation, expected in [
        ("distinct", ConsolidationVerdict.DISTINCT),
        ("duplicate", ConsolidationVerdict.DUPLICATE),
        ("supersedes", ConsolidationVerdict.SUPERSEDES),
    ]:
        monkeypatch.setattr(cmod, "_anthropic_request", lambda p, k, t, _r=relation: _resp(_r))
        c = LLMConsolidator(api_key="k", model="m", timeout=5.0)
        assert c.classify(new_text="Fly RAM scaled to 4GB", new_ts="t2", neighbor=nb) == expected


def test_llm_consolidator_failsafe_distinct(monkeypatch):
    import cairn.ingest.consolidate as cmod
    from cairn.ingest.consolidate import ConsolidationVerdict, LLMConsolidator, Neighbor

    nb = Neighbor(permalink="o", text="x", timestamp=None)
    c = LLMConsolidator(api_key="k", model="m", timeout=5.0)

    # unknown relation value -> DISTINCT
    monkeypatch.setattr(cmod, "_anthropic_request", lambda p, k, t: _resp("merge?!"))
    assert c.classify(new_text="y", new_ts=None, neighbor=nb) == ConsolidationVerdict.DISTINCT

    # malformed JSON -> DISTINCT
    monkeypatch.setattr(
        cmod, "_anthropic_request", lambda p, k, t: {"content": [{"type": "text", "text": "nope"}]}
    )
    assert c.classify(new_text="y", new_ts=None, neighbor=nb) == ConsolidationVerdict.DISTINCT

    # request raises -> DISTINCT
    def boom(p, k, t):
        raise TimeoutError("down")

    monkeypatch.setattr(cmod, "_anthropic_request", boom)
    assert c.classify(new_text="y", new_ts=None, neighbor=nb) == ConsolidationVerdict.DISTINCT


def test_resolve_consolidator(monkeypatch):
    from cairn.ingest.consolidate import LLMConsolidator, resolve_consolidator

    # anthropic + key + enabled -> LLMConsolidator
    env = {"CAIRN_JUDGE": "anthropic", "ANTHROPIC_API_KEY": "k", "CAIRN_CONSOLIDATE": "true"}
    assert isinstance(resolve_consolidator(env=env), LLMConsolidator)
    # disabled -> None
    assert resolve_consolidator(env={**env, "CAIRN_CONSOLIDATE": "false"}) is None
    # no key -> None
    assert resolve_consolidator(env={"CAIRN_JUDGE": "anthropic", "CAIRN_CONSOLIDATE": "true"}) is None
    # non-anthropic -> None
    assert resolve_consolidator(env={"CAIRN_JUDGE": "embedding", "CAIRN_CONSOLIDATE": "true"}) is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ingest/test_consolidate.py -k "consolidator or resolve" -v`
Expected: FAIL — `LLMConsolidator` / `resolve_consolidator` not defined.

- [ ] **Step 3: Implement**

Append to `src/cairn/ingest/consolidate.py`:

```python
_PROMPT = """You compare a NEW developer memory against the most similar EXISTING \
memory and classify their relationship. Respond with ONLY a JSON object \
{"relation": "<value>"} where value is one of:
- "duplicate": they state the same fact and the NEW one adds nothing newer (same \
value, or the NEW one is an older/equal version of an evolving fact).
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/ingest/test_consolidate.py -v`
Expected: PASS (Task 4 of config must land first if `resolve_consolidate` is missing — see note). If `resolve_consolidate` is undefined, do Task 3 below first, then return here.

> **Order note:** `resolve_consolidator` imports `resolve_consolidate` from `config.py` (Task 3). Implement Task 3 before running this step's `resolve` tests. The `LLMConsolidator` tests pass independently.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/consolidate.py tests/ingest/test_consolidate.py
git commit -m "feat(consolidate): LLMConsolidator (fail-safe) + resolve_consolidator"
```

---

## Task 3: config `consolidate` knob + `resolve_consolidate`

**Files:** Modify `src/cairn/config.py`; Test `tests/test_config.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
def test_consolidate_knob_default_on_and_disablable():
    assert cfg.resolve_consolidate({}) is True  # default on
    assert cfg.resolve_consolidate({"CAIRN_CONSOLIDATE": "false"}) is False
    assert cfg.resolve_consolidate({"CAIRN_CONSOLIDATE": "0"}) is False
    assert cfg.resolve_consolidate({"CAIRN_CONSOLIDATE": "true"}) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_config.py::test_consolidate_knob_default_on_and_disablable -v`
Expected: FAIL — `resolve_consolidate` not defined.

- [ ] **Step 3: Implement**

In `src/cairn/config.py`, add to the `KNOBS` list (near the other knobs):

```python
    Knob(
        "consolidate",
        "CAIRN_CONSOLIDATE",
        "true",
        "Semantic dedup + supersession during ingest (LLM judge tier only).",
    ),
```

And add a resolver near the other `resolve_*` helpers (mirror how `resolve_rerank` reads a boolean env value):

```python
def resolve_consolidate(env: dict | None = None) -> bool:
    """Whether ingest-time memory consolidation is enabled (default True)."""
    e = env if env is not None else cairn_env()
    val = e.get("CAIRN_CONSOLIDATE")
    if val is None:
        return True
    return str(val).strip().lower() not in ("0", "false", "no", "off", "")
```

(If `resolve_rerank` uses a shared truthiness helper, reuse that helper instead of re-implementing the string check, to stay DRY.)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_config.py -k consolidate -v` then `uv run pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/config.py tests/test_config.py
git commit -m "feat(config): CAIRN_CONSOLIDATE knob (default on) + resolve_consolidate"
```

---

## Task 4: `IngestReport` counters

**Files:** Modify `src/cairn/ingest/models.py`; Test `tests/ingest/test_models.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_models.py`:

```python
def test_ingest_report_consolidation_counters():
    from cairn.ingest.models import IngestReport

    r = IngestReport()
    assert r.semantic_deduped == 0 and r.superseded == 0
    r.semantic_deduped += 1
    r.superseded += 2
    d = r.to_dict()
    assert d["semantic_deduped"] == 1 and d["superseded"] == 2
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_models.py::test_ingest_report_consolidation_counters -v`
Expected: FAIL — attributes / dict keys missing.

- [ ] **Step 3: Implement**

In `src/cairn/ingest/models.py`, in `IngestReport`, add after `judge_degraded`:

```python
    semantic_deduped: int = 0  # candidates skipped as semantic duplicates
    superseded: int = 0  # existing notes marked superseded_by a newer candidate
```

And in `to_dict`, add the two keys (alongside `judge_degraded`):

```python
            "semantic_deduped": self.semantic_deduped,
            "superseded": self.superseded,
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/ingest/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/models.py tests/ingest/test_models.py
git commit -m "feat(ingest): IngestReport semantic_deduped + superseded counters"
```

---

## Task 5: `mark_superseded` frontmatter editor

**Files:** Modify `src/cairn/ingest/distill.py`; Test `tests/ingest/test_distill.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_distill.py`:

```python
def test_mark_superseded_sets_frontmatter(tmp_path):
    from cairn.ingest.distill import mark_superseded
    from cairn.vault import parse_note

    p = tmp_path / "old.md"
    p.write_text(
        "---\ntitle: Old\ntype: memory\npermalink: old\n---\n\n- [context] old fact #ingested\n",
        encoding="utf-8",
    )
    mark_superseded(p, "new-permalink")
    note = parse_note(p.read_text(encoding="utf-8"))
    assert note.frontmatter.get("superseded_by") == "new-permalink"
    assert "old fact" in note.body  # body preserved
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_distill.py::test_mark_superseded_sets_frontmatter -v`
Expected: FAIL — `mark_superseded` not defined.

- [ ] **Step 3: Implement**

In `src/cairn/ingest/distill.py`, add (it already imports from `cairn.vault`; if not, add `from cairn.vault import parse_note, write_note`):

```python
def mark_superseded(path: Path, by_permalink: str) -> None:
    """Set `superseded_by: <by_permalink>` in an existing note's frontmatter,
    preserving body/observations. Idempotent (re-setting the same value rewrites
    identical content). The reindex picks up the change and demotes it in recall."""
    note = parse_note(path.read_text(encoding="utf-8"))
    if note.frontmatter.get("superseded_by") == by_permalink:
        return
    note.frontmatter["superseded_by"] = by_permalink
    path.write_text(write_note(note), encoding="utf-8")
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/ingest/test_distill.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/distill.py tests/ingest/test_distill.py
git commit -m "feat(ingest): mark_superseded frontmatter editor"
```

---

## Task 6: Pipeline Phase C — consolidate step

**Files:** Modify `src/cairn/ingest/pipeline.py`; Test `tests/ingest/test_pipeline.py`.

This is the integration task. Phase C splits into: (1) the existing gate loop, which now *collects* kept candidates with their distilled notes instead of writing inline; (2) a write pass over the kept candidates **sorted by timestamp**, applying consolidation.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_pipeline.py` (helpers for fakes + the four behaviors):

```python
class _FakeNeighborIndex:
    """In-memory text->nearest fake. `pairs` maps a substring to a (Neighbor, cos)
    to return when a candidate's text contains it; also remembers add()ed notes."""

    def __init__(self, pairs=None):
        from cairn.ingest.consolidate import Neighbor

        self._pairs = pairs or {}
        self.added = []
        self._Neighbor = Neighbor

    def nearest(self, text):
        for sub, (perm, ntext, ts, cos) in self._pairs.items():
            if sub in text:
                return self._Neighbor(permalink=perm, text=ntext, timestamp=ts), cos
        return None

    def add(self, permalink, text, timestamp):
        self.added.append((permalink, text))


class _FakeConsolidator:
    def __init__(self, verdict):
        self.verdict = verdict
        self.calls = 0

    def classify(self, *, new_text, new_ts, neighbor):
        self.calls += 1
        return self.verdict


def _llm_judge_keep_all():
    """A stub judge that is treated as the LLM tier and keeps+distills everything."""
    from cairn.ingest.judge import Judgment

    class LLMishKeep:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            return [Judgment(durability=0.9, title="T", distilled=t) for t in texts]

    return LLMishKeep()


def _consolidation_transcript(tmp_path, text, ts="t0", sid="s"):
    return Transcript(
        session_id=sid,
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / f"{sid}.jsonl",
        events=[_ev(EventKind.AUTHORED_USER, text, ts=ts)],
    )


def test_consolidation_only_on_llm_tier(tmp_path):
    """A non-LLM judge tier -> consolidator never called, note written."""
    from cairn.ingest.consolidate import ConsolidationVerdict
    from cairn.ingest.judge import EmbeddingJudge
    from cairn.ingest.pipeline import ingest_transcripts
    from tests.ingest.test_judge import StubEmbedder

    cons = _FakeConsolidator(ConsolidationVerdict.DUPLICATE)
    nidx = _FakeNeighborIndex({"rebase": ("old", "old", "t0", 0.99)})
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [_consolidation_transcript(tmp_path, "we always rebase-merge approved PRs")],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=EmbeddingJudge(StubEmbedder()),  # embedding tier, not llm
        consolidator=cons,
        neighbor_index=nidx,
    )
    assert cons.calls == 0  # consolidation skipped off the llm tier
    assert len(rep.written) == 1 and rep.semantic_deduped == 0


def test_consolidation_duplicate_skips_and_ledgers(tmp_path):
    from cairn.ingest.consolidate import ConsolidationVerdict
    from cairn.ingest.dedup import DedupLedger as _DL
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.pipeline import ingest_transcripts

    text = "the signoz endpoint is https://ingest.us2.signoz.cloud"
    cons = _FakeConsolidator(ConsolidationVerdict.DUPLICATE)
    nidx = _FakeNeighborIndex({"signoz": ("signoz-old", "signoz endpoint", "t0", 0.97)})
    vault = tmp_path / "v"
    vault.mkdir()
    ledger_path = tmp_path / "l.sha256"
    rep = ingest_transcripts(
        [_consolidation_transcript(tmp_path, text)],
        vault_root=vault,
        ledger=DedupLedger(ledger_path),
        judge=_llm_judge_keep_all(),
        consolidator=cons,
        neighbor_index=nidx,
    )
    assert cons.calls == 1
    assert rep.written == [] and rep.semantic_deduped == 1
    assert _DL(ledger_path).seen(content_hash(text))  # ledgered so future sweeps skip it


def test_consolidation_supersedes_marks_old(tmp_path):
    from cairn.ingest.consolidate import ConsolidationVerdict
    from cairn.ingest.pipeline import ingest_transcripts
    from cairn.vault import parse_note

    vault = tmp_path / "v"
    (vault / "memories").mkdir(parents=True)
    old = vault / "memories" / "ram-old.md"
    old.write_text(
        "---\ntitle: RAM\ntype: memory\npermalink: ram-old\n---\n\n- [context] RAM 2GB #ingested\n",
        encoding="utf-8",
    )
    cons = _FakeConsolidator(ConsolidationVerdict.SUPERSEDES)
    nidx = _FakeNeighborIndex({"4GB": ("ram-old", "RAM 2GB", "t0", 0.95)})
    rep = ingest_transcripts(
        [_consolidation_transcript(tmp_path, "scale RAM to 4GB", ts="t1")],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=_llm_judge_keep_all(),
        consolidator=cons,
        neighbor_index=nidx,
    )
    assert len(rep.written) == 1 and rep.superseded == 1
    assert parse_note(old.read_text(encoding="utf-8")).frontmatter.get("superseded_by")


def test_consolidation_distinct_writes_both(tmp_path):
    from cairn.ingest.consolidate import ConsolidationVerdict
    from cairn.ingest.pipeline import ingest_transcripts

    cons = _FakeConsolidator(ConsolidationVerdict.DISTINCT)
    nidx = _FakeNeighborIndex({"rebase": ("old", "old", "t0", 0.95)})
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [_consolidation_transcript(tmp_path, "we always rebase-merge approved PRs")],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=_llm_judge_keep_all(),
        consolidator=cons,
        neighbor_index=nidx,
    )
    assert len(rep.written) == 1 and rep.semantic_deduped == 0 and rep.superseded == 0


def test_consolidation_below_gate_skips_classify(tmp_path):
    """No neighbor above the gate -> nearest returns None -> classifier not called."""
    from cairn.ingest.consolidate import ConsolidationVerdict
    from cairn.ingest.pipeline import ingest_transcripts

    cons = _FakeConsolidator(ConsolidationVerdict.DUPLICATE)
    nidx = _FakeNeighborIndex({})  # nearest() always None
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [_consolidation_transcript(tmp_path, "a distinct durable decision")],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=_llm_judge_keep_all(),
        consolidator=cons,
        neighbor_index=nidx,
    )
    assert cons.calls == 0 and len(rep.written) == 1


def test_consolidation_classifier_error_is_distinct(tmp_path):
    """A consolidator that raises must not lose the candidate (fail-safe write)."""
    from cairn.ingest.pipeline import ingest_transcripts

    class Boom:
        def classify(self, *, new_text, new_ts, neighbor):
            raise RuntimeError("classifier down")

    nidx = _FakeNeighborIndex({"rebase": ("old", "old", "t0", 0.99)})
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [_consolidation_transcript(tmp_path, "we always rebase-merge approved PRs")],
        vault_root=vault,
        ledger=DedupLedger(tmp_path / "l.sha256"),
        judge=_llm_judge_keep_all(),
        consolidator=Boom(),
        neighbor_index=nidx,
    )
    assert len(rep.written) == 1  # written despite classifier error
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/ingest/test_pipeline.py -k consolidation -v`
Expected: FAIL — `ingest_transcripts` has no `consolidator`/`neighbor_index` params.

- [ ] **Step 3: Implement**

In `src/cairn/ingest/pipeline.py`:

Add imports at top:
```python
from cairn.ingest.consolidate import Consolidator, ConsolidationVerdict, NeighborIndex
from cairn.ingest.distill import mark_superseded
```

Add the two parameters to `ingest_transcripts` (after `judged_cache`):
```python
    consolidator: Consolidator | None = None,
    neighbor_index: NeighborIndex | None = None,
```

Replace the Phase C loop. The current loop gates and writes inline. New structure — gate into a `kept` list, then a timestamp-ordered consolidate+write pass:

```python
    # Phase C: combined gate. Kept candidates are collected (not written yet) so
    # the write pass can run in timestamp order for correct supersession.
    consolidating = (
        consolidator is not None
        and neighbor_index is not None
        and report.judge_tier == "llm"
        and not dry_run
    )
    kept: list[tuple[Candidate, str]] = []  # (candidate-with-judgment, content hash)
    for idx, (cand, h) in enumerate(pending):
        heuristic = score(cand.text)
        j = judged.get(idx)
        llm_verdict = j is not None and report.judge_tier == "llm" and not j.degraded
        if llm_verdict:
            keep = j.distilled is not None
            combined = j.durability
            cand = replace(cand, judgment=j, importance=combined)
        elif j is not None:
            combined = max(0.0, min(1.0, 0.5 * heuristic + 0.5 * j.durability))
            keep = combined >= threshold
            cand = replace(cand, judgment=j, importance=combined)
        else:
            combined = heuristic
            keep = combined >= threshold
            cand = replace(cand, importance=combined)
        if not keep:
            report.gated_out += 1
            if (
                judge is not None
                and judged_cache is not None
                and j is not None
                and not j.degraded
                and not dry_run
            ):
                judged_cache.put(_judge_cache_key(cand), j, report.judge_tier)
            continue
        kept.append((cand, h))

    # Write pass: timestamp order so a later memory supersedes an earlier one.
    for cand, h in sorted(kept, key=lambda ch: ch[0].timestamp or ""):
        note = distiller.distill(cand)
        if dry_run:
            report.candidates += 1
            continue
        if consolidating:
            verdict, neighbor = _consolidate(cand, note, consolidator, neighbor_index)
            if verdict is ConsolidationVerdict.DUPLICATE:
                report.semantic_deduped += 1
                ledger.add(h)  # never reprocess this duplicate
                continue
            if verdict is ConsolidationVerdict.SUPERSEDES and neighbor is not None:
                old_path = vault_root / subdir / f"{neighbor.permalink}.md"
                if old_path.exists():
                    mark_superseded(old_path, note.permalink)
                    report.superseded += 1
        report.candidates += 1
        path = write_derived_note(note, vault_root, subdir=subdir)
        ledger.add(h)
        report.written.append(path)
        if consolidating:
            neighbor_index.add(note.permalink, _memory_text(cand, note), cand.timestamp)
    return report
```

Add two module-level helpers (near `_judge_cache_key`):

```python
def _memory_text(cand: Candidate, note) -> str:
    """The text used to embed/compare a memory: the LLM distillation if present,
    else the candidate's (redacted) text."""
    j = cand.judgment
    return j.distilled if (j and j.distilled) else cand.text


def _consolidate(cand, note, consolidator, neighbor_index):
    """Return (verdict, neighbor). Fail-safe: any error -> (DISTINCT, None)."""
    try:
        hit = neighbor_index.nearest(_memory_text(cand, note))
        if hit is None:
            return ConsolidationVerdict.DISTINCT, None
        neighbor, _cos = hit  # nearest() already applied the cosine gate
        verdict = consolidator.classify(
            new_text=_memory_text(cand, note), new_ts=cand.timestamp, neighbor=neighbor
        )
        return verdict, neighbor
    except Exception:
        return ConsolidationVerdict.DISTINCT, None
```

> **Gate placement:** the cosine `_CONSOLIDATE_GATE` is applied inside the real `NeighborIndex.nearest` (Task 7): it returns `None` when the best cosine is below the gate, so the pipeline only calls `classify` for above-gate hits. The fake in tests mirrors this (returns None when no pair matches).

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/ingest/test_pipeline.py -v` then `uv run pytest -q`
Expected: PASS. All existing pipeline tests stay green: when `consolidator`/`neighbor_index` are not passed (every existing test), `consolidating` is False and the write pass behaves exactly as before (gate → distill → write → ledger), only reordered by timestamp (single-candidate and same-timestamp tests are unaffected).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): consolidate step in Phase C (dedup + supersession, llm tier)"
```

---

## Task 7: CLI — wire a DuckDB-backed `NeighborIndex`

**Files:** Modify `src/cairn/cli.py`; Test `tests/test_cli.py`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py` (unit-test the NeighborIndex impl directly; the helper name below must match the implementation):

```python
def test_duckdb_neighbor_index_unions_index_and_batch(tmp_path, monkeypatch):
    """nearest() returns the higher-cosine of (DuckDB index, this-sweep batch),
    and None below the gate."""
    from cairn.cli import _DuckDBNeighborIndex
    from cairn.ingest.consolidate import _CONSOLIDATE_GATE

    class FakeEmbedder:
        dim = 3

        def embed(self, texts):
            # "ram" -> axis 0, "signoz" -> axis 1, else axis 2
            out = []
            for t in texts:
                if "ram" in t.lower():
                    out.append([1.0, 0.0, 0.0])
                elif "signoz" in t.lower():
                    out.append([0.0, 1.0, 0.0])
                else:
                    out.append([0.0, 0.0, 1.0])
            return out

    # No index connection -> only the in-memory batch is consulted.
    nidx = _DuckDBNeighborIndex(con=None, dim=3, embedder=FakeEmbedder())
    assert nidx.nearest("ram usage") is None  # batch empty
    nidx.add("ram-2gb", "ram 2gb", "t0")
    hit = nidx.nearest("ram 4gb")
    assert hit is not None and hit[0].permalink == "ram-2gb" and hit[1] >= _CONSOLIDATE_GATE
    # an orthogonal query is below the gate -> None
    assert nidx.nearest("signoz endpoint") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_duckdb_neighbor_index_unions_index_and_batch -v`
Expected: FAIL — `_DuckDBNeighborIndex` not defined.

- [ ] **Step 3: Implement**

In `src/cairn/cli.py`, add the class (imports: `from cairn.ingest.consolidate import Neighbor, _CONSOLIDATE_GATE`; `from cairn.search.engine import vector_search`):

```python
class _DuckDBNeighborIndex:
    """NeighborIndex over the DuckDB index (prior notes) unioned with an in-memory
    list of this-sweep's writes. Embeds query text with the sweep's embedder.
    Returns the closest memory only when its cosine >= _CONSOLIDATE_GATE."""

    def __init__(self, *, con, dim: int, embedder) -> None:
        self._con = con
        self._dim = dim
        self._embedder = embedder
        self._batch: list[tuple[str, list[float], str, str | None]] = []  # perm, vec, text, ts

    def _embed(self, text: str) -> list[float]:
        return self._embedder.embed([text])[0]

    def nearest(self, text: str):
        vec = self._embed(text)
        best: tuple[Neighbor, float] | None = None
        # in-memory batch arm
        for perm, bvec, btext, bts in self._batch:
            cos = _cosine(vec, bvec)
            if best is None or cos > best[1]:
                best = (Neighbor(permalink=perm, text=btext, timestamp=bts), cos)
        # DuckDB index arm (top chunk -> its note)
        if self._con is not None:
            rows = vector_search(self._con, vec, dim=self._dim, pool=1)
            if rows:
                chunk_id, cos = rows[0]
                meta = self._con.execute(
                    "SELECT n.permalink, n.title, n.created FROM chunks c "
                    "JOIN notes n ON n.permalink = c.note_permalink "
                    "WHERE c.chunk_id = ?",
                    [chunk_id],
                ).fetchone()
                if meta is not None and (best is None or cos > best[1]):
                    best = (Neighbor(permalink=meta[0], text=meta[1] or "", timestamp=meta[2]), cos)
        if best is None or best[1] < _CONSOLIDATE_GATE:
            return None
        return best

    def add(self, permalink: str, text: str, timestamp: str | None) -> None:
        self._batch.append((permalink, self._embed(text), text, timestamp))


def _cosine(a: list[float], b: list[float]) -> float:
    import math

    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)
```

> **Verify the join columns** against `src/cairn/index/schema.py` / `build.py`: the chunk→note link column may be `c.note_permalink` (used in `search/engine.py`'s graph-boost) — confirm and match. If chunks store the note key under a different column, adjust the JOIN accordingly.

In the `sweep` command, before calling `ingest_transcripts`, open the index read-only for neighbor lookups and build the deps:

```python
    emb = get_embedder(embedder)
    idx = index or _default_index()
    nbr_con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id) if idx.exists() else None
    consolidator = resolve_consolidator()
    neighbor_index = (
        _DuckDBNeighborIndex(con=nbr_con, dim=emb.dim, embedder=emb)
        if consolidator is not None
        else None
    )
```

Pass `consolidator=consolidator, neighbor_index=neighbor_index` into `ingest_transcripts`. After ingest, close `nbr_con` if opened (before/after the reconcile `open_index` — ensure no two write handles; the neighbor connection is read-only query use, so open it, use it, and `nbr_con.close()` before the reconcile opens its own handle). Extend the summary echo:

```python
    extra = ""
    if rep.semantic_deduped or rep.superseded:
        extra = f"; {rep.semantic_deduped} deduped, {rep.superseded} superseded"
    typer.echo(f"swept: {len(rep.written)} memory note(s) written{extra}; reindexed ...")
```

(Keep the existing reindex/echo wording; just add `extra`.)

> **Connection caution:** DuckDB allows one read-write handle. Open `nbr_con` for queries, `close()` it before `open_index(...)` for the reconcile. Confirm `open_index` is safe to call twice sequentially on the same file (it is, sequentially). If simpler, reuse a single connection for both neighbor queries and reconcile.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_cli.py -k neighbor -v` then `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): DuckDB-backed NeighborIndex; wire consolidation into sweep"
```

---

## Task 8: gate-validation script + 0.10.0 release

**Files:** Create `scripts/eval_consolidate.py`; modify `src/cairn/__init__.py`, `CHANGELOG.md`.

- [ ] **Step 1: Create the validation script**

Create `scripts/eval_consolidate.py`:

```python
# scripts/eval_consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Validate _CONSOLIDATE_GATE on the real vault: embed all memory notes, report
the cosine distribution between known-duplicate pairs vs distinct neighbors so a
human can confirm the gate separates them. Run: uv run python scripts/eval_consolidate.py
[vault]. This is an analysis tool — it never edits the vault."""

from __future__ import annotations

import sys
from pathlib import Path

from cairn.embed import get_embedder
from cairn.ingest.consolidate import _CONSOLIDATE_GATE


def main() -> None:
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "agentcairn"
    notes = sorted((vault / "memories").glob("*.md"))
    emb = get_embedder("fastembed")
    texts = [p.read_text(encoding="utf-8") for p in notes]
    vecs = emb.embed(texts)

    def cos(a, b):
        import math

        d = sum(x * y for x, y in zip(a, b, strict=True))
        na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
        return 0.0 if na == 0 or nb == 0 else d / (na * nb)

    # top-1 nearest neighbor cosine for each note (excluding itself)
    sims = []
    for i in range(len(vecs)):
        best = max((cos(vecs[i], vecs[j]) for j in range(len(vecs)) if j != i), default=0.0)
        sims.append((best, notes[i].name))
    sims.sort(reverse=True)
    above = [s for s in sims if s[0] >= _CONSOLIDATE_GATE]
    print(f"notes={len(notes)} gate={_CONSOLIDATE_GATE}")
    print(f"pairs at/above gate (consolidation candidates): {len(above)}")
    for c, name in above[:30]:
        print(f"  {c:.3f}  {name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it (manual validation, not a unit test)**

Run: `uv run python scripts/eval_consolidate.py ~/agentcairn`
Expected: prints the count of at/above-gate pairs and the top matches. Eyeball that the known dups (SigNoz endpoint ×2) appear above the gate and obviously-distinct notes do not. If the separation is poor, adjust `_CONSOLIDATE_GATE` in `consolidate.py` and note the chosen value in the CHANGELOG/PR. (No code test asserts a specific corpus number — the corpus is not in the repo.)

- [ ] **Step 3: Bump version + CHANGELOG**

In `src/cairn/__init__.py`: `__version__ = "0.10.0"`.

In `CHANGELOG.md`, after `## [Unreleased]`:

```markdown
## [0.10.0] - 2026-06-13

### Added
- **Memory consolidation during ingest (LLM judge tier).** A new memory that semantically duplicates an existing one is skipped, and a memory that is a strictly newer version of the same evolving fact marks the old one `superseded_by` (kept in the vault, demoted in recall). Detection is cosine-pre-gated against the existing index plus this-sweep's writes, then classified by the LLM; any uncertainty or error resolves to "distinct" (both kept), so a wrong call never silently drops a distinct memory. Off the LLM tier, behavior is unchanged. New `CAIRN_CONSOLIDATE` knob (default on) is a kill-switch; the sweep reports `N deduped, M superseded`.
```

Add the link reference above `[0.9.8]`:
```markdown
[0.10.0]: https://github.com/ccf/agentcairn/compare/v0.9.8...v0.10.0
```

- [ ] **Step 4: Full suite + commit**

Run: `uv run pytest -q` (all pass) and `uv run ruff check src tests` (clean).

```bash
git add scripts/eval_consolidate.py src/cairn/__init__.py CHANGELOG.md
git commit -m "chore(release): 0.10.0 — memory consolidation + gate-eval script"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest -q` green; `uv run ruff check src tests` clean.
- [ ] PR; CI + Cursor Bugbot; fix Bugbot findings; rebase-merge `--delete-branch`.
- [ ] Tag `v0.10.0`; `gh release create`; confirm PyPI publish.
- [ ] **Dogfood e2e:** validate the gate (`scripts/eval_consolidate.py`), then a full from-scratch re-gate of `~/agentcairn` (back up first); confirm the SigNoz-endpoint dup collapses to one note and the Fly RAM series leaves a single live note with the older ones carrying `superseded_by`. Report `semantic_deduped` / `superseded` counts.

---

## Self-Review

**Spec coverage:**
- §A consolidate module (enum/Neighbor/protocols/gate/LLMConsolidator/resolve) → Tasks 1–2. ✓
- §B NeighborIndex (DuckDB ∪ batch, cosine gate, vector_search reuse) → Task 7. ✓ (text-based refinement noted in header.)
- §C pipeline integration (timestamp order, skip/supersede/distinct, batch register, tier+deps+dry-run gating, fail-safe) → Task 6. ✓
- §D report counters + config knob + (no Candidate change) → Tasks 3, 4. ✓
- supersession frontmatter edit (keep+demote via reconcile) → Task 5 (`mark_superseded`) + Task 6 (call). ✓
- threshold validation script → Task 8. ✓
- edge cases (no embedder/empty index, classifier error, non-llm, kill-switch, same-sweep series, dry-run, incremental older-candidate) → Task 6 tests + fail-safe `_consolidate` + Task 7. ✓
- release 0.10.0 → Task 8. ✓

**Placeholder scan:** No TBD/TODO. Two explicit "verify against existing code" notes (the chunk→note JOIN column in Task 7; the DuckDB single-connection caution) are real integration checks with the resolution stated, not deferred work. ✓

**Type consistency:** `ConsolidationVerdict.{DISTINCT,DUPLICATE,SUPERSEDES}`, `Neighbor(permalink,text,timestamp)`, `NeighborIndex.nearest(text)->tuple[Neighbor,float]|None`/`.add(permalink,text,timestamp)`, `Consolidator.classify(*,new_text,new_ts,neighbor)`, `resolve_consolidator(*,env)`, `resolve_consolidate(env)`, `mark_superseded(path, by_permalink)`, `_memory_text`, `_consolidate`, pipeline params `consolidator`/`neighbor_index`, `IngestReport.semantic_deduped`/`.superseded` — names consistent across tasks. ✓

**Note on test ordering:** Task 2's `resolve_consolidator` tests depend on Task 3 (`config.resolve_consolidate`). If executing strictly in order, implement Task 3's resolver before re-running Task 2 Step 4 (called out inline). Subagent-driven execution should sequence 1 → 3 → 2 → 4 → 5 → 6 → 7 → 8, or land Task 3 within Task 2. ✓
