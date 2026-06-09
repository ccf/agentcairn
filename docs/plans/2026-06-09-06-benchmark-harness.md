# Benchmark Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible benchmark harness (`benchmarks/`) that measures agentcairn retrieval quality (and optional end-to-end QA accuracy) on LongMemEval-S and LoCoMo across a 5-arm ablation, per `docs/specs/2026-06-09-benchmark-harness-design.md`.

**Architecture:** A top-level `benchmarks/` dev tool (never shipped in the wheel; LoCoMo is CC BY-NC) that imports `cairn`. Each dataset record becomes a scoped markdown vault (one note/session, one chunk/turn, gold IDs embedded in `permalink`/`heading_path`), reconciled into a fresh DuckDB index, queried across the ablation arms, and scored with deterministic retrieval metrics + an opt-in Anthropic QA layer. A committed synthetic fixture drives offline CI with `FakeEmbedder`.

**Tech Stack:** Python 3.12, stdlib + `cairn` for the retrieval core; optional `anthropic` + `huggingface-hub` (the `bench` dependency group) for the QA layer and real-dataset downloads. `uv` for everything.

---

## Conventions

- Run everything with `uv` from `/Users/ccf/git/agentcairn`. Branch: `feat/bench-harness` (already created; never commit to `main`).
- SPDX header on every new `.py`: `# SPDX-License-Identifier: Apache-2.0`.
- `from __future__ import annotations`, type hints, dataclasses; match existing cairn style (ruff `E,F,I,UP,B`, B008 ignored, line-length 100). Keep `uv run ruff check .` + `uv run pre-commit run --all-files` green.
- The core `cairn` suite stays under `tests/` (`testpaths=["tests"]`); the bench tests live under `benchmarks/tests/` and run via an explicit path so they never pull bench deps into the core run.

## Cairn APIs this plan uses (already merged — do NOT reimplement)

- `cairn.search.engine`: `Hit(chunk_id, permalink, heading_path, snippet, score)`; `open_search(index_path)`; `search(con, query, *, embedder=None, k=10, pool=200, rerank=False) -> list[Hit]`; `hybrid_search(con, query, qvec, *, dim, limit=10, pool=200) -> list[dict]`; `vector_search(con, qvec, *, dim, pool=200) -> list[tuple[str,float]]`; `bm25_only(con, query, *, limit=10, pool=200) -> list[dict]`; `get_chunks(con, chunk_ids) -> list[dict]` (keys: chunk_id, note_permalink, heading_path, ordinal, text); `get_note(con, permalink) -> dict|None`.
- `cairn.index`: `open_index(path, *, dim, model_id)`, `reconcile(con, vault_dir, embedder)`.
- `cairn.embed`: `get_embedder(name)`, `FakeEmbedder(dim=8)` (deterministic, offline; has `.dim`, `.model_id`, `.embed_query(str)`).
- `cairn.vault`: `Note(permalink=None, frontmatter={}, body="", ...)`, `write_note(note) -> str`.

## File structure

```
src/cairn/search/engine.py            # MODIFY (Task 1: graph_boost toggle)
benchmarks/
  manifest.toml                       # Task 2
  fixtures/synthetic/
    longmemeval_synth.json            # Task 2
    locomo_synth.json                 # Task 2
  cairn_bench/
    __init__.py                       # Task 3
    models.py                         # Task 3 (Query, RetrievalGold)
    adapters/__init__.py              # Task 3
    adapters/longmemeval.py           # Task 3
    adapters/locomo.py                # Task 3
    vaultize.py                       # Task 3
    build.py                          # Task 4
    retrieval_metrics.py              # Task 5
    config.py                         # Task 5 (ArmConfig + ARMS)
    ablation.py                       # Task 5
    report.py                         # Task 6
    qa/__init__.py                    # Task 8
    qa/provider.py                    # Task 8
    qa/generate.py                    # Task 8
    qa/judge.py                       # Task 8
    download.py                       # Task 9
    run.py                            # Task 9 (manual entrypoint)
    README.md                         # Task 10
  tests/
    __init__.py                       # Task 7
    conftest.py                       # Task 7 (paths to fixtures)
    test_metrics.py                   # Task 5
    test_adapters.py                  # Task 3
    test_synthetic.py                 # Task 7 (end-to-end, exact recall@k)
    test_locomo_denominator.py        # Task 7
    test_qa.py                        # Task 8 (fake provider)
.github/workflows/bench.yml           # Task 7 (offline CI job)
```

---

### Task 1: `graph_boost` toggle in `cairn.search`

**Files:**
- Modify: `src/cairn/search/engine.py`
- Test: `tests/search/test_search.py`

**Context:** The ×1.2 graph-boost in `_hybrid_sql` is unconditional, so the matrix can't produce "hybrid without boost." Add a `graph_boost: bool = True` parameter threaded `search → hybrid_search → _hybrid_sql`. Default `True` preserves all current behavior/tests.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/search/test_search.py  (reuse the file's existing index-building helper /
# FakeEmbedder import pattern; build_index here mirrors the existing tests)
def test_graph_boost_toggle_changes_score(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    vault = tmp_path / "v"
    vault.mkdir()
    # note "tea" is a link target of "coffee" -> graph-boost applies to tea
    (vault / "coffee.md").write_text(
        "---\ntitle: Coffee\npermalink: coffee\n---\nbrewing tea methods. See [[tea]].\n"
    )
    (vault / "tea.md").write_text(
        "---\ntitle: Tea\npermalink: tea\n---\nbrewing tea steeping methods.\n"
    )
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(vault), emb)
    con0.close()

    con = open_search(str(idx))
    try:
        on = {h.permalink: h.score for h in search(con, "brewing tea", embedder=emb, graph_boost=True)}
        off = {h.permalink: h.score for h in search(con, "brewing tea", embedder=emb, graph_boost=False)}
        default = {h.permalink: h.score for h in search(con, "brewing tea", embedder=emb)}
    finally:
        con.close()
    # tea is a link target -> boosted when on, not when off
    assert on["tea"] > off["tea"]
    assert default["tea"] == on["tea"]  # default is graph_boost=True
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/search/test_search.py::test_graph_boost_toggle_changes_score -v`
Expected: FAIL — `search() got an unexpected keyword argument 'graph_boost'`.

- [ ] **Step 3: Implement the toggle**

In `src/cairn/search/engine.py`, change `_hybrid_sql` to build the boost expression conditionally, and thread the flag through `hybrid_search` and `search`:

```python
def _hybrid_sql(dim: int, graph_boost: bool = True) -> str:
    boost = (
        "* (CASE WHEN EXISTS (SELECT 1 FROM links l WHERE l.dst_target = c.note_permalink) "
        "THEN 1.2 ELSE 1.0 END)"
        if graph_boost
        else ""
    )
    return f"""
        WITH fts AS (
            SELECT chunk_id, rank() OVER (ORDER BY score DESC) AS r
            FROM (
                SELECT chunk_id, fts_main_chunks.match_bm25(chunk_id, ?, fields := 'text') AS score
                FROM chunks
            ) WHERE score IS NOT NULL
            ORDER BY score DESC LIMIT ?
        ),
        vec AS (
            SELECT chunk_id, rank() OVER (ORDER BY sim DESC) AS r
            FROM (
                SELECT chunk_id, array_cosine_similarity(vec, ?::FLOAT[{dim}]) AS sim
                FROM chunk_embeddings ORDER BY sim DESC LIMIT ?
            )
        ),
        fused AS (
            SELECT coalesce(fts.chunk_id, vec.chunk_id) AS chunk_id,
                   rrf(fts.r) + rrf(vec.r) AS rrf_score
            FROM fts FULL OUTER JOIN vec ON fts.chunk_id = vec.chunk_id
        )
        SELECT f.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               f.rrf_score {boost} AS score
        FROM fused f JOIN chunks c ON c.chunk_id = f.chunk_id
        ORDER BY score DESC LIMIT ?
    """
```

Update `hybrid_search` signature/body:
```python
def hybrid_search(con, query, qvec, *, dim, limit=10, pool=200, graph_boost=True) -> list[dict]:
    rows = con.execute(_hybrid_sql(dim, graph_boost), [query, pool, qvec, pool, limit]).fetchall()
    # ... unchanged row->dict shaping ...
```

Update `search` to accept `graph_boost: bool = True` and pass it through ONLY on the hybrid (embedder-present) path:
```python
def search(con, query, *, embedder=None, k=10, pool=200, rerank=False, graph_boost=True) -> list[Hit]:
    if embedder is not None:
        qvec = embedder.embed_query(query)
        rows = hybrid_search(con, query, qvec, dim=embedder.dim,
                             limit=(max(20, k) if rerank else k), pool=pool, graph_boost=graph_boost)
    else:
        rows = bm25_only(con, query, limit=(max(20, k) if rerank else k), pool=pool)
    # ... rest unchanged ...
```
(Keep the exact existing bodies for the parts shown as comments — only add the parameter and pass-through.)

- [ ] **Step 4: Run to verify it passes + no regressions**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/search/ -q`
Expected: PASS (new test + all existing search tests unchanged, since default `graph_boost=True`).

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/search/engine.py tests/search/test_search.py
git commit -m "feat(search): optional graph_boost toggle (enables benchmark ablation)"
```

---

### Task 2: Synthetic fixtures + manifest

**Files:**
- Create: `benchmarks/manifest.toml`
- Create: `benchmarks/fixtures/synthetic/longmemeval_synth.json`
- Create: `benchmarks/fixtures/synthetic/locomo_synth.json`

**Context:** These are the only committed data. They mimic the real schemas so the same adapter code runs offline in CI, with known gold so tests assert exact recall@k. They are hand-authored, NOT derived from the real datasets.

- [ ] **Step 1: Create `benchmarks/manifest.toml`**

```toml
# Pinned sources for the real benchmark datasets. NEVER vendored (LoCoMo is CC BY-NC 4.0).
# The downloader (cairn_bench/download.py) fetches into ~/.cache/agentcairn/bench and
# verifies sha256 against these entries.

[longmemeval_s]
kind = "hf"                      # huggingface_hub.hf_hub_download
repo_id = "xiaowu0162/longmemeval-cleaned"
filename = "longmemeval_s_cleaned.json"
revision = "98d7416c24c778c2fee6e6f3006e7a073259d48f"
license = "MIT"
sha256 = ""                      # filled on first verified download (treat empty = "record on fetch")

[locomo]
kind = "url"                     # plain https GET
url = "https://raw.githubusercontent.com/snap-research/locomo/3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376/data/locomo10.json"
license = "CC BY-NC 4.0"
sha256 = ""                      # filled on first verified download
```

- [ ] **Step 2: Create `benchmarks/fixtures/synthetic/longmemeval_synth.json`**

A small JSON array of LongMemEval-shaped instances. Include one answerable multi-session, one knowledge-update, one temporal, and one `_abs`. Keep turns short. Exact shape (field names verbatim from the spec):

```json
[
  {
    "question_id": "synth_multi_1",
    "question_type": "multi-session",
    "question": "What pet did Alex adopt and what did they name it?",
    "answer": "a cat named Mochi",
    "question_date": "2024-03-10",
    "haystack_session_ids": ["s_a", "s_b", "s_distract"],
    "haystack_dates": ["2024-01-05", "2024-02-01", "2024-01-20"],
    "answer_session_ids": ["s_a", "s_b"],
    "haystack_sessions": [
      [
        {"role": "user", "content": "I adopted a cat last week.", "has_answer": true},
        {"role": "assistant", "content": "Congrats on the new cat!"}
      ],
      [
        {"role": "user", "content": "I named my cat Mochi.", "has_answer": true},
        {"role": "assistant", "content": "Mochi is a lovely name."}
      ],
      [
        {"role": "user", "content": "The weather has been rainy."},
        {"role": "assistant", "content": "Hope it clears up."}
      ]
    ]
  },
  {
    "question_id": "synth_update_1",
    "question_type": "knowledge-update",
    "question": "Where does Alex currently work?",
    "answer": "Globex",
    "question_date": "2024-04-01",
    "haystack_session_ids": ["s_old", "s_new"],
    "haystack_dates": ["2024-01-01", "2024-03-01"],
    "answer_session_ids": ["s_new"],
    "haystack_sessions": [
      [{"role": "user", "content": "I work at Initech.", "has_answer": true}],
      [{"role": "user", "content": "I switched jobs; I now work at Globex.", "has_answer": true}]
    ]
  },
  {
    "question_id": "synth_temporal_1",
    "question_type": "temporal-reasoning",
    "question": "In which month did Alex start running?",
    "answer": "February 2024",
    "question_date": "2024-05-01",
    "haystack_session_ids": ["s_run", "s_noise"],
    "haystack_dates": ["2024-02-15", "2024-03-15"],
    "answer_session_ids": ["s_run"],
    "haystack_sessions": [
      [{"role": "user", "content": "I started running this month.", "has_answer": true}],
      [{"role": "user", "content": "I bought new shoes."}]
    ]
  },
  {
    "question_id": "synth_unanswerable_1_abs",
    "question_type": "single-session-user",
    "question": "What is the name of Alex's brother?",
    "answer": "(unanswerable - no sibling mentioned)",
    "question_date": "2024-05-02",
    "haystack_session_ids": ["s_only"],
    "haystack_dates": ["2024-04-10"],
    "answer_session_ids": [],
    "haystack_sessions": [
      [{"role": "user", "content": "I went hiking with friends."}]
    ]
  }
]
```

- [ ] **Step 3: Create `benchmarks/fixtures/synthetic/locomo_synth.json`**

A JSON array of one LoCoMo-shaped conversation. Include qa across categories 1–4, one cat-5 adversarial, and one **malformed** dia_id (`D1:02`) in evidence to exercise normalization.

```json
[
  {
    "sample_id": "conv-synth-1",
    "conversation": {
      "speaker_a": "Alex",
      "speaker_b": "Sam",
      "session_1_date_time": "1:56 pm on 8 May, 2023",
      "session_1": [
        {"speaker": "Alex", "dia_id": "D1:1", "text": "I adopted a cat named Mochi."},
        {"speaker": "Sam", "dia_id": "D1:2", "text": "How old is Mochi?"},
        {"speaker": "Alex", "dia_id": "D1:3", "text": "Mochi is two years old."}
      ],
      "session_2_date_time": "10:00 am on 20 June, 2023",
      "session_2": [
        {"speaker": "Sam", "dia_id": "D2:1", "text": "Did you move recently?"},
        {"speaker": "Alex", "dia_id": "D2:2", "text": "Yes, I moved to Portland in June."}
      ]
    },
    "qa": [
      {"question": "What is the name of Alex's cat?", "answer": "Mochi", "category": 4, "evidence": ["D1:1"]},
      {"question": "How old is the cat Alex adopted?", "answer": "two years old", "category": 1, "evidence": ["D1:02", "D1:3"]},
      {"question": "When did Alex move to Portland?", "answer": "June 2023", "category": 2, "evidence": ["D2:2"]},
      {"question": "What city does Alex live in now?", "answer": "Portland", "category": 3, "evidence": ["D2:2"]},
      {"question": "What car does Alex drive?", "answer": "No information available.", "category": 5, "evidence": []}
    ]
  }
]
```

- [ ] **Step 4: Sanity-check the JSON parses**

Run: `cd /Users/ccf/git/agentcairn && uv run python -c "import json; json.load(open('benchmarks/fixtures/synthetic/longmemeval_synth.json')); json.load(open('benchmarks/fixtures/synthetic/locomo_synth.json')); print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/manifest.toml benchmarks/fixtures/
git commit -m "test(bench): synthetic LongMemEval/LoCoMo fixtures + dataset manifest"
```

---

### Task 3: Adapters + vaultize

**Files:**
- Create: `benchmarks/cairn_bench/__init__.py`, `benchmarks/cairn_bench/models.py`, `benchmarks/cairn_bench/adapters/__init__.py`, `benchmarks/cairn_bench/adapters/longmemeval.py`, `benchmarks/cairn_bench/adapters/locomo.py`, `benchmarks/cairn_bench/vaultize.py`
- Create: `benchmarks/tests/__init__.py`, `benchmarks/tests/conftest.py`, `benchmarks/tests/test_adapters.py`

**Context:** Adapters convert one dataset record into (`list[Note]`, `list[Query]`) where each `Query` carries the gold evidence sets. `vaultize` writes the notes to a directory as markdown. The turn id (LongMemEval positional, LoCoMo `dia_id`) is embedded in each turn's `##` header so it appears in `Hit.heading_path`.

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/conftest.py
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent.parent / "fixtures" / "synthetic"


@pytest.fixture
def lme_instances():
    return json.loads((FIX / "longmemeval_synth.json").read_text())


@pytest.fixture
def locomo_samples():
    return json.loads((FIX / "locomo_synth.json").read_text())
```

```python
# benchmarks/tests/test_adapters.py
# SPDX-License-Identifier: Apache-2.0
from cairn_bench.adapters import locomo, longmemeval


def test_longmemeval_adapter_notes_and_gold(lme_instances):
    inst = next(i for i in lme_instances if i["question_id"] == "synth_multi_1")
    notes, queries = longmemeval.adapt(inst)
    # one note per haystack session
    assert {n.permalink for n in notes} == {"s_a", "s_b", "s_distract"}
    q = queries[0]
    assert q.gold_sessions == {"s_a", "s_b"}
    # gold turn ids are positional 1-based on evidence (has_answer) turns
    assert "s_a_1" in q.gold_turns and "s_b_1" in q.gold_turns
    assert q.is_abstention is False
    # turn id is embedded in a header so it survives chunking
    body = next(n for n in notes if n.permalink == "s_a").body
    assert "s_a_1" in body


def test_longmemeval_abstention_flag(lme_instances):
    inst = next(i for i in lme_instances if i["question_id"].endswith("_abs"))
    _notes, queries = longmemeval.adapt(inst)
    assert queries[0].is_abstention is True
    assert queries[0].gold_sessions == set()


def test_locomo_adapter_notes_queries_and_normalization(locomo_samples):
    notes, queries = locomo.adapt(locomo_samples[0])
    assert {n.permalink for n in notes} == {"conv-synth-1_session_1", "conv-synth-1_session_2"}
    # category 5 (adversarial) is excluded from retrieval queries
    cats = {q.category for q in queries}
    assert 5 not in cats
    # malformed dia_id "D1:02" normalizes and matches the header-embedded "D1:2"
    q_age = next(q for q in queries if q.category == 1)
    assert q_age.gold_turns == {"D1:2", "D1:3"}
    body = next(n for n in notes if n.permalink == "conv-synth-1_session_1").body
    assert "D1:2" in body


def test_locomo_normalize_dia_id():
    assert locomo.normalize_dia_id("D1:02") == "D1:2"
    assert locomo.normalize_dia_id("D30:05") == "D30:5"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/test_adapters.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn_bench'`.
(Note: bench tests import `cairn_bench`; add `benchmarks` to the path — see Task 7 conftest/`pytest` invocation. For now run with `PYTHONPATH=benchmarks`.)

- [ ] **Step 3: Write `models.py`**

```python
# benchmarks/cairn_bench/models.py
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
    gold_turns: set[str] = field(default_factory=set)      # turn ids (in heading_path)
    category: str | int | None = None                      # question_type / LoCoMo category
    is_abstention: bool = False
    meta: dict = field(default_factory=dict)               # question_date, session dates, etc.
```

- [ ] **Step 4: Write `adapters/__init__.py` and the two adapters**

```python
# benchmarks/cairn_bench/adapters/__init__.py
# SPDX-License-Identifier: Apache-2.0
```

```python
# benchmarks/cairn_bench/adapters/longmemeval.py
# SPDX-License-Identifier: Apache-2.0
"""LongMemEval-S instance -> (Notes, Query). One note per haystack session; each turn
is a '## {session_id}_{turn_idx+1}' header so the positional gold turn id lands in
Hit.heading_path. Gold sessions = answer_session_ids; gold turns = has_answer turns."""

from __future__ import annotations

from cairn.vault import Note

from cairn_bench.models import Query


def adapt(instance: dict) -> tuple[list[Note], list[Query]]:
    sids = instance["haystack_session_ids"]
    dates = instance["haystack_dates"]
    sessions = instance["haystack_sessions"]
    notes: list[Note] = []
    gold_turns: set[str] = set()
    for sid, date, turns in zip(sids, dates, sessions, strict=True):
        lines = []
        for i, turn in enumerate(turns):
            turn_id = f"{sid}_{i + 1}"
            if turn.get("has_answer") is True:
                gold_turns.add(turn_id)
            role = turn.get("role", "user")
            lines.append(f"## {turn_id}  ({role}, {date})\n\n{turn['content']}\n")
        notes.append(
            Note(
                permalink=sid,
                frontmatter={"title": sid, "type": "session", "permalink": sid,
                             "session_date": date, "instance_id": instance["question_id"]},
                body="\n".join(lines),
            )
        )
    is_abs = instance["question_id"].endswith("_abs")
    q = Query(
        qid=instance["question_id"],
        question=instance["question"],
        answer=instance.get("answer", ""),
        gold_sessions=set() if is_abs else set(instance.get("answer_session_ids", [])),
        gold_turns=set() if is_abs else gold_turns,
        category=instance.get("question_type"),
        is_abstention=is_abs,
        meta={"question_date": instance.get("question_date")},
    )
    return notes, [q]
```

```python
# benchmarks/cairn_bench/adapters/locomo.py
# SPDX-License-Identifier: Apache-2.0
"""LoCoMo sample -> (Notes, Queries). One note per session_{N}; each turn is a
'## {dia_id}  ({speaker})' header so the native dia_id lands in Hit.heading_path.
Gold turns = qa.evidence (normalized). Category 5 (adversarial) is excluded from
retrieval queries (kept only for the QA-abstention metric, handled elsewhere)."""

from __future__ import annotations

import re

from cairn.vault import Note

from cairn_bench.models import Query

_SESSION_RE = re.compile(r"^session_(\d+)$")
_DIA_RE = re.compile(r"^D(\d+):(\d+)$")


def normalize_dia_id(raw: str) -> str:
    """Strip zero-padding: 'D1:02' -> 'D1:2'. Leaves unrecognized ids unchanged."""
    raw = raw.strip()
    m = _DIA_RE.match(raw)
    if not m:
        return raw
    return f"D{int(m.group(1))}:{int(m.group(2))}"


def _evidence_turns(evidence: list) -> set[str]:
    out: set[str] = set()
    for ev in evidence or []:
        for part in str(ev).split(";"):  # handle semicolon-compound
            part = part.strip()
            if _DIA_RE.match(part):
                out.add(normalize_dia_id(part))
    return out


def adapt(sample: dict) -> tuple[list[Note], list[Query]]:
    sample_id = sample["sample_id"]
    conv = sample["conversation"]
    notes: list[Note] = []
    for key in sorted(conv):
        m = _SESSION_RE.match(key)
        if not m:
            continue  # skips session_N_date_time and speaker_a/b
        n = m.group(1)
        date = conv.get(f"session_{n}_date_time", "")
        lines = []
        for turn in conv[key]:
            did = normalize_dia_id(turn["dia_id"])
            text = turn.get("text", "")
            if turn.get("blip_caption"):
                text = f"{text}\n[image: {turn['blip_caption']}]"
            lines.append(f"## {did}  ({turn.get('speaker', '')})\n\n{text}\n")
        permalink = f"{sample_id}_session_{n}"
        notes.append(
            Note(
                permalink=permalink,
                frontmatter={"title": permalink, "type": "session",
                             "permalink": permalink, "session_date": date},
                body="\n".join(lines),
            )
        )
    queries: list[Query] = []
    for i, qa in enumerate(sample.get("qa", [])):
        cat = qa.get("category")
        if cat == 5:
            continue  # adversarial: excluded from retrieval
        gold_turns = _evidence_turns(qa.get("evidence", []))
        gold_sessions = {f"{sample_id}_session_{t.split(':')[0][1:]}" for t in gold_turns}
        queries.append(
            Query(
                qid=f"{sample_id}_q{i}",
                question=qa["question"],
                answer=str(qa.get("answer", "")),
                gold_sessions=gold_sessions,
                gold_turns=gold_turns,
                category=cat,
                is_abstention=False,
            )
        )
    return notes, queries
```

```python
# benchmarks/cairn_bench/__init__.py
# SPDX-License-Identifier: Apache-2.0
```

- [ ] **Step 5: Write `vaultize.py`**

```python
# benchmarks/cairn_bench/vaultize.py
# SPDX-License-Identifier: Apache-2.0
"""Write adapter Notes to a directory as markdown via cairn.vault.write_note."""

from __future__ import annotations

from pathlib import Path

from cairn.vault import Note, write_note


def write_vault(notes: list[Note], vault_dir: Path) -> Path:
    vault_dir = Path(vault_dir)
    vault_dir.mkdir(parents=True, exist_ok=True)
    for note in notes:
        (vault_dir / f"{note.permalink}.md").write_text(write_note(note), encoding="utf-8")
    return vault_dir
```

- [ ] **Step 6: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_adapters.py -v`
Expected: PASS (4 tests).

- [ ] **Step 7: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/cairn_bench/__init__.py benchmarks/cairn_bench/models.py benchmarks/cairn_bench/adapters/ benchmarks/cairn_bench/vaultize.py benchmarks/tests/__init__.py benchmarks/tests/conftest.py benchmarks/tests/test_adapters.py
git commit -m "feat(bench): LongMemEval + LoCoMo adapters and vaultize"
```

---

### Task 4: Scoped index build

**Files:**
- Create: `benchmarks/cairn_bench/build.py`
- Test: `benchmarks/tests/test_adapters.py` (add `test_build_scoped_index`)

**Context:** Given adapter Notes, write a temp vault, reconcile a fresh DuckDB index, and return an `open_search` connection plus the corpus chunk count (so the ablation runner can set `pool ≥ chunk_count` and not silently cap recall).

- [ ] **Step 1: Write the failing test**

```python
# add to benchmarks/tests/test_adapters.py
def test_build_scoped_index(lme_instances, tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.search import search

    from cairn_bench.adapters import longmemeval
    from cairn_bench.build import build_scoped_index

    inst = next(i for i in lme_instances if i["question_id"] == "synth_multi_1")
    notes, _q = longmemeval.adapt(inst)
    con, chunk_count = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        assert chunk_count >= 3  # at least one chunk per session
        hits = search(con, "cat named Mochi", embedder=FakeEmbedder(dim=8), k=10)
        assert any(h.permalink in {"s_a", "s_b"} for h in hits)
    finally:
        con.close()
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_adapters.py::test_build_scoped_index -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn_bench.build'`.

- [ ] **Step 3: Implement `build.py`**

```python
# benchmarks/cairn_bench/build.py
# SPDX-License-Identifier: Apache-2.0
"""Build a scoped, throwaway DuckDB index from adapter Notes and return a read-only
search connection + the corpus chunk count (for pool sizing)."""

from __future__ import annotations

from pathlib import Path

from cairn.index import open_index, reconcile
from cairn.search import open_search
from cairn.vault import Note

from cairn_bench.vaultize import write_vault


def build_scoped_index(notes: list[Note], work_dir: Path, embedder) -> tuple[object, int]:
    work_dir = Path(work_dir)
    vault = write_vault(notes, work_dir / "vault")
    idx = work_dir / "index.duckdb"
    wcon = open_index(str(idx), dim=embedder.dim, model_id=embedder.model_id)
    try:
        reconcile(wcon, str(vault), embedder)
        chunk_count = wcon.execute("SELECT count(*) FROM chunks").fetchone()[0]
    finally:
        wcon.close()  # release the write lock before opening read-only
    return open_search(str(idx)), int(chunk_count)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_adapters.py::test_build_scoped_index -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/cairn_bench/build.py benchmarks/tests/test_adapters.py
git commit -m "feat(bench): scoped index builder (vaultize + reconcile)"
```

---

### Task 5: Retrieval metrics + ablation config/runner

**Files:**
- Create: `benchmarks/cairn_bench/retrieval_metrics.py`, `benchmarks/cairn_bench/config.py`, `benchmarks/cairn_bench/ablation.py`
- Test: `benchmarks/tests/test_metrics.py`

**Context:** Pure metric functions over `(ranked_ids: list[str], gold: set[str])`. The ablation `config` defines the 5 arms as functions returning ranked turn-ids for a query; `ablation.run_arm` scores one arm on one query at the requested k values.

- [ ] **Step 1: Write the failing test**

```python
# benchmarks/tests/test_metrics.py
# SPDX-License-Identifier: Apache-2.0
import math

from cairn_bench.retrieval_metrics import (
    ndcg_any_at_k,
    ndcg_at_k,
    recall_all_at_k,
    recall_at_k,
    reciprocal_rank,
)


def test_recall_at_k_fractional():
    ranked = ["a", "x", "b", "y"]
    gold = {"a", "b", "c"}  # 2 of 3 gold in top-3
    assert recall_at_k(ranked, gold, 3) == 2 / 3
    assert recall_at_k(ranked, gold, 1) == 1 / 3
    assert recall_at_k([], gold, 5) == 0.0


def test_recall_all_at_k_strict():
    ranked = ["a", "b", "x"]
    assert recall_all_at_k(ranked, {"a", "b"}, 3) == 1.0   # all gold present
    assert recall_all_at_k(ranked, {"a", "b"}, 1) == 0.0   # not all in top-1


def test_reciprocal_rank():
    assert reciprocal_rank(["x", "a", "b"], {"a"}) == 0.5   # first gold at rank 2
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_ndcg_monotonic():
    gold = {"a"}
    # gold at rank 1 scores higher than gold at rank 3
    assert ndcg_at_k(["a", "x", "y"], gold, 3) > ndcg_at_k(["x", "y", "a"], gold, 3)
    assert math.isclose(ndcg_at_k(["a"], gold, 3), 1.0)


def test_ndcg_any_binary():
    # ndcg_any uses binary relevance; multiple gold contribute
    assert ndcg_any_at_k(["a", "b"], {"a", "b"}, 2) > 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_metrics.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `retrieval_metrics.py`**

```python
# benchmarks/cairn_bench/retrieval_metrics.py
# SPDX-License-Identifier: Apache-2.0
"""Retrieval metrics over a ranked list of ids and a gold set. Deterministic, no LLM.
`recall_at_k`/`ndcg_at_k`/`reciprocal_rank` are textbook (fractional). `recall_all_at_k`
/`ndcg_any_at_k` replicate the LongMemEval official 'strict' definitions for line-up."""

from __future__ import annotations

import math


def recall_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    topk = set(ranked[:k])
    return len(topk & gold) / len(gold)


def recall_all_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    """LongMemEval-style: 1.0 iff ALL gold ids appear in the top-k, else 0.0."""
    if not gold:
        return 0.0
    topk = set(ranked[:k])
    return 1.0 if gold <= topk else 0.0


def reciprocal_rank(ranked: list[str], gold: set[str]) -> float:
    for i, rid in enumerate(ranked):
        if rid in gold:
            return 1.0 / (i + 1)
    return 0.0


def _dcg(rels: list[float]) -> float:
    return rels[0] + sum(r / math.log2(i + 2) for i, r in enumerate(rels[1:], start=1)) if rels else 0.0


def ndcg_at_k(ranked: list[str], gold: set[str], k: int) -> float:
    if not gold:
        return 0.0
    rels = [1.0 if rid in gold else 0.0 for rid in ranked[:k]]
    ideal = [1.0] * min(len(gold), k)
    idcg = _dcg(ideal)
    return _dcg(rels) / idcg if idcg else 0.0


# ndcg_any uses binary relevance too; kept as a distinct name to match the paper's label.
ndcg_any_at_k = ndcg_at_k
```

- [ ] **Step 4: Implement `config.py` (the arms)**

```python
# benchmarks/cairn_bench/config.py
# SPDX-License-Identifier: Apache-2.0
"""The ablation arms. Each arm, given (con, query_text, embedder, pool, k), returns a
ranked list of Hit-like rows with .permalink and .heading_path so gold matching works
identically across arms."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from cairn.search import get_chunks, search, vector_search


@dataclass
class RankedRow:
    permalink: str
    heading_path: str


def _from_hits(hits) -> list[RankedRow]:
    return [RankedRow(h.permalink, h.heading_path) for h in hits]


def _vector_only(con, q, embedder, pool, k):
    pairs = vector_search(con, embedder.embed_query(q), dim=embedder.dim, pool=pool)
    ids = [cid for cid, _sim in pairs][:k]
    rows = {c["chunk_id"]: c for c in get_chunks(con, ids)}
    return [RankedRow(rows[cid]["note_permalink"], rows[cid]["heading_path"]) for cid in ids if cid in rows]


@dataclass
class ArmConfig:
    name: str
    rank: Callable  # (con, query_text, embedder, pool, k) -> list[RankedRow]


ARMS: list[ArmConfig] = [
    ArmConfig("bm25-only", lambda con, q, e, pool, k: _from_hits(
        search(con, q, embedder=None, k=k, pool=pool))),
    ArmConfig("vector-only", _vector_only),
    ArmConfig("hybrid-rrf", lambda con, q, e, pool, k: _from_hits(
        search(con, q, embedder=e, k=k, pool=pool, graph_boost=False))),
    ArmConfig("hybrid+graph-boost", lambda con, q, e, pool, k: _from_hits(
        search(con, q, embedder=e, k=k, pool=pool, graph_boost=True))),
    ArmConfig("hybrid+reranker", lambda con, q, e, pool, k: _from_hits(
        search(con, q, embedder=e, k=k, pool=pool, rerank=True))),
]
```

- [ ] **Step 5: Implement `ablation.py`**

```python
# benchmarks/cairn_bench/ablation.py
# SPDX-License-Identifier: Apache-2.0
"""Run one arm on one query and compute retrieval metrics at the requested k values,
at both turn granularity (parse turn id from heading_path) and session granularity."""

from __future__ import annotations

from cairn_bench.config import ArmConfig, RankedRow
from cairn_bench.models import Query
from cairn_bench.retrieval_metrics import ndcg_at_k, recall_all_at_k, recall_at_k, reciprocal_rank

# Turn id is the first whitespace-delimited token of the header text we authored
# ("## {turn_id}  (...)"), surfaced verbatim in Hit.heading_path.


def _turn_id(heading_path: str) -> str:
    return heading_path.split()[0] if heading_path else ""


def score_query(rows: list[RankedRow], query: Query, ks: list[int]) -> dict:
    """Return {granularity: {metric@k: value}} for one (arm, query)."""
    turn_ranked = [_turn_id(r.heading_path) for r in rows]
    sess_ranked = [r.permalink for r in rows]
    out: dict = {"turn": {}, "session": {}}
    for gran, ranked, gold in (
        ("turn", turn_ranked, query.gold_turns),
        ("session", sess_ranked, query.gold_sessions),
    ):
        if not gold:
            continue
        for k in ks:
            out[gran][f"recall@{k}"] = recall_at_k(ranked, gold, k)
            out[gran][f"ndcg@{k}"] = ndcg_at_k(ranked, gold, k)
            out[gran][f"recall_all@{k}"] = recall_all_at_k(ranked, gold, k)
        out[gran]["mrr"] = reciprocal_rank(ranked, gold)
    return out


def run_arm(con, arm: ArmConfig, query: Query, embedder, *, ks: list[int], pool: int) -> dict:
    rows = arm.rank(con, query.question, embedder, pool, max(ks))
    return score_query(rows, query, ks)
```

- [ ] **Step 6: Run to verify metrics pass**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_metrics.py -v`
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/cairn_bench/retrieval_metrics.py benchmarks/cairn_bench/config.py benchmarks/cairn_bench/ablation.py benchmarks/tests/test_metrics.py
git commit -m "feat(bench): retrieval metrics + ablation arms/runner"
```

---

### Task 6: Report aggregation

**Files:**
- Create: `benchmarks/cairn_bench/report.py`
- Test: `benchmarks/tests/test_metrics.py` (add `test_aggregate`)

**Context:** Aggregate per-(arm, query) metric dicts into macro-averages overall and per-category, with Wilson 95% CIs on rates. Emit a plain dict (JSON-serializable) and a markdown table. Retrieval and QA columns are labeled distinctly (QA added in Task 8).

- [ ] **Step 1: Write the failing test**

```python
# add to benchmarks/tests/test_metrics.py
def test_aggregate_macro_average():
    from cairn_bench.report import aggregate, wilson_ci

    per_query = [
        {"arm": "hybrid-rrf", "category": "multi-session", "turn": {"recall@5": 1.0, "mrr": 1.0}},
        {"arm": "hybrid-rrf", "category": "multi-session", "turn": {"recall@5": 0.0, "mrr": 0.0}},
    ]
    agg = aggregate(per_query, ks=[5])
    assert agg["hybrid-rrf"]["turn"]["recall@5"] == 0.5
    lo, hi = wilson_ci(1, 2)
    assert 0.0 <= lo <= 0.5 <= hi <= 1.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_metrics.py::test_aggregate_macro_average -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `report.py`**

```python
# benchmarks/cairn_bench/report.py
# SPDX-License-Identifier: Apache-2.0
"""Aggregate per-query metrics into macro-averages (overall + per-category) with Wilson
95% CIs, and render a labeled markdown table. No single headline number — every row is
tagged with its arm, granularity, and (retrieval|qa) axis."""

from __future__ import annotations

import math
from collections import defaultdict


def wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for a binomial rate (used for per-category accuracy)."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def aggregate(per_query: list[dict], *, ks: list[int]) -> dict:
    """per_query rows: {arm, category, turn:{metric:val}, session:{...}}. Returns
    {arm: {granularity: {metric: macro-mean}}}."""
    buckets: dict = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for row in per_query:
        arm = row["arm"]
        for gran in ("turn", "session"):
            for metric, val in row.get(gran, {}).items():
                buckets[arm][gran][metric].append(val)
    out: dict = {}
    for arm, grans in buckets.items():
        out[arm] = {gran: {m: _mean(vals) for m, vals in metrics.items()}
                    for gran, metrics in grans.items()}
    return out


def to_markdown(agg: dict, *, granularity: str = "turn") -> str:
    lines = [f"### Retrieval — {granularity}-level (macro-avg)\n",
             "| arm | recall@5 | recall@10 | ndcg@10 | mrr |", "|---|---|---|---|---|"]
    for arm, grans in agg.items():
        m = grans.get(granularity, {})
        lines.append(
            f"| {arm} | {m.get('recall@5', 0):.3f} | {m.get('recall@10', 0):.3f} "
            f"| {m.get('ndcg@10', 0):.3f} | {m.get('mrr', 0):.3f} |"
        )
    lines.append("\n_Retrieval metrics only — not QA accuracy. No single headline number; "
                 "see caveats in benchmarks/README.md._")
    return "\n".join(lines)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && PYTHONPATH=benchmarks uv run pytest benchmarks/tests/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/cairn_bench/report.py benchmarks/tests/test_metrics.py
git commit -m "feat(bench): report aggregation with Wilson CIs + markdown table"
```

---

### Task 7: Offline end-to-end fixture test + CI job

**Files:**
- Create: `benchmarks/tests/test_synthetic.py`, `benchmarks/tests/test_locomo_denominator.py`
- Create: `.github/workflows/bench.yml`
- Modify: `benchmarks/tests/conftest.py` (make `cairn_bench` importable without `PYTHONPATH`)

**Context:** The full pipeline (adapt → build → ablation → metrics → aggregate) on the synthetic fixtures with `FakeEmbedder`, asserting **exact** recall@k against the known gold — a regression test on the metric+pipeline wiring, fully offline. Plus the LoCoMo adversarial-denominator test (the "Zep bug"). The CI job runs only these.

- [ ] **Step 1: Make `cairn_bench` importable in tests**

Add to the TOP of `benchmarks/tests/conftest.py`:
```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # benchmarks/ on path -> import cairn_bench
```

- [ ] **Step 2: Write the end-to-end synthetic test**

```python
# benchmarks/tests/test_synthetic.py
# SPDX-License-Identifier: Apache-2.0
from cairn.embed import FakeEmbedder

from cairn_bench.ablation import run_arm
from cairn_bench.adapters import locomo, longmemeval
from cairn_bench.build import build_scoped_index
from cairn_bench.config import ARMS

KS = [1, 3, 5, 10, 20]


def _arm(name):
    return next(a for a in ARMS if a.name == name)


def test_longmemeval_pipeline_recovers_gold(lme_instances, tmp_path):
    inst = next(i for i in lme_instances if i["question_id"] == "synth_multi_1")
    notes, queries = longmemeval.adapt(inst)
    con, chunks = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        res = run_arm(con, _arm("hybrid+graph-boost"), queries[0], FakeEmbedder(dim=8),
                      ks=KS, pool=max(200, chunks))
    finally:
        con.close()
    # tiny corpus: both gold sessions must be in the top-20 -> session recall@20 == 1.0
    assert res["session"]["recall@20"] == 1.0


def test_locomo_pipeline_turn_gold(locomo_samples, tmp_path):
    notes, queries = locomo.adapt(locomo_samples[0])
    con, chunks = build_scoped_index(notes, tmp_path, FakeEmbedder(dim=8))
    try:
        q = next(q for q in queries if q.category == 4)  # single-hop "name of cat"
        res = run_arm(con, _arm("bm25-only"), q, FakeEmbedder(dim=8), ks=KS, pool=max(200, chunks))
    finally:
        con.close()
    assert res["turn"]["recall@20"] == 1.0  # gold dia_id D1:1 is recoverable
```

- [ ] **Step 3: Write the denominator test**

```python
# benchmarks/tests/test_locomo_denominator.py
# SPDX-License-Identifier: Apache-2.0
from cairn_bench.adapters import locomo


def test_adversarial_excluded_from_retrieval_queries(locomo_samples):
    _notes, queries = locomo.adapt(locomo_samples[0])
    # category 5 (adversarial) contributes NO retrieval query -> excluded from both
    # numerator and denominator of any macro-average (the Zep denominator bug).
    assert all(q.category != 5 for q in queries)
    # the fixture has exactly one cat-5 item, so 5 qa -> 4 retrieval queries
    assert len(queries) == 4
```

- [ ] **Step 4: Run the offline suite**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/ -v`
Expected: PASS (adapters + metrics + synthetic + denominator), no network, no API key.

- [ ] **Step 5: Add the CI job**

Create `.github/workflows/bench.yml` (mirror the existing `ci.yml` setup-uv pattern; runs only the offline bench tests):
```yaml
name: bench

on:
  pull_request:
  push:
    branches: [main]

jobs:
  bench-offline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
        with:
          python-version: "3.12"
      - run: uv sync
      - run: uv run pytest benchmarks/tests/ -q
```

- [ ] **Step 6: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/tests/test_synthetic.py benchmarks/tests/test_locomo_denominator.py benchmarks/tests/conftest.py .github/workflows/bench.yml
git commit -m "test(bench): offline end-to-end fixture suite + CI job"
```

---

### Task 8: Opt-in QA layer

**Files:**
- Create: `benchmarks/cairn_bench/qa/__init__.py`, `benchmarks/cairn_bench/qa/provider.py`, `benchmarks/cairn_bench/qa/generate.py`, `benchmarks/cairn_bench/qa/judge.py`
- Modify: `pyproject.toml` (`bench` dependency group)
- Test: `benchmarks/tests/test_qa.py`

**Context:** Generation + judging behind a thin provider seam so tests use a fake (no key, deterministic) and real runs use Anthropic. The judge routes per question type (temporal tolerance, knowledge-update, preference, abstention). Tests use the fake provider; real-API behavior is exercised only when a key is present (skip otherwise).

- [ ] **Step 1: Add the `bench` dependency group**

In `pyproject.toml`, under `[dependency-groups]`:
```toml
bench = ["anthropic>=0.40", "huggingface-hub>=0.25"]
```
Run: `cd /Users/ccf/git/agentcairn && uv sync` (the group is optional; core install unaffected).

- [ ] **Step 2: Write the failing test (fake provider)**

```python
# benchmarks/tests/test_qa.py
# SPDX-License-Identifier: Apache-2.0
from cairn_bench.qa.judge import judge
from cairn_bench.qa.provider import FakeProvider


def test_judge_yes_no_parsing():
    p = FakeProvider(reply="Yes, the response is correct.")
    assert judge("Q?", gold="Mochi", response="The cat is Mochi", question_type="multi-session", provider=p) is True
    p2 = FakeProvider(reply="No.")
    assert judge("Q?", gold="Mochi", response="A dog", question_type="multi-session", provider=p2) is False


def test_judge_abstention_routes_to_refusal_prompt():
    p = FakeProvider(reply="yes")
    # for abstention, the prompt asks whether the model correctly refused; provider is fake,
    # so we just assert the abstention path is taken (prompt contains 'unanswerable').
    last = {}
    p.on_prompt = lambda prompt: last.setdefault("p", prompt)
    judge("Q?", gold="(unanswerable)", response="I don't have that info.",
          question_type="single-session-user", is_abstention=True, provider=p)
    assert "unanswerable" in last["p"].lower()
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/test_qa.py -v`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement `provider.py`**

```python
# benchmarks/cairn_bench/qa/provider.py
# SPDX-License-Identifier: Apache-2.0
"""Thin LLM provider seam. FakeProvider for tests; AnthropicProvider for real runs."""

from __future__ import annotations

import os
from typing import Callable, Protocol


class Provider(Protocol):
    def complete(self, prompt: str, *, max_tokens: int = 256, temperature: float = 0.0) -> str: ...


class FakeProvider:
    def __init__(self, reply: str = "yes") -> None:
        self.reply = reply
        self.on_prompt: Callable[[str], None] | None = None

    def complete(self, prompt: str, *, max_tokens: int = 256, temperature: float = 0.0) -> str:
        if self.on_prompt:
            self.on_prompt(prompt)
        return self.reply


class AnthropicProvider:
    def __init__(self, model: str = "claude-sonnet-4-6") -> None:
        from anthropic import Anthropic

        self.model = model
        self._client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    def complete(self, prompt: str, *, max_tokens: int = 256, temperature: float = 0.0) -> str:
        msg = self._client.messages.create(
            model=self.model, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")
```

- [ ] **Step 5: Implement `judge.py`**

```python
# benchmarks/cairn_bench/qa/judge.py
# SPDX-License-Identifier: Apache-2.0
"""LLM-as-judge with per-type prompts. Binary verdict. temperature=0. The judge sees
only (question, gold answer, model response) — we score correctness, not faithfulness.
NOTE: judge is Anthropic, NOT the canonical GPT-4o; numbers are NOT comparable to
published LongMemEval/LoCoMo leaderboards (relative ablation signal only)."""

from __future__ import annotations

_BASE = ("You are grading an answer. Question: {q}\nReference answer: {gold}\n"
         "Model response: {resp}\nReply 'yes' if the response contains the correct "
         "answer, otherwise 'no'.")
_TEMPORAL = _BASE + " Allow off-by-one tolerance in day/week/month."
_UPDATE = ("You are grading an answer to a knowledge-update question. Question: {q}\n"
           "Latest correct answer: {gold}\nModel response: {resp}\nReply 'yes' only if "
           "the response gives the UPDATED/latest value, otherwise 'no'.")
_PREF = _BASE + " Be lenient; the response need not match every detail of the reference."
_ABS = ("You are grading whether a model correctly abstained. Question: {q}\n"
        "This question is unanswerable / based on a false premise.\nModel response: {resp}\n"
        "Reply 'yes' if the response correctly indicates the question is unanswerable or "
        "that the information is missing, otherwise 'no'.")


def _prompt(q, gold, resp, question_type, is_abstention):
    if is_abstention:
        return _ABS.format(q=q, resp=resp)
    qt = (question_type or "").lower()
    if "temporal" in qt:
        return _TEMPORAL.format(q=q, gold=gold, resp=resp)
    if "update" in qt:
        return _UPDATE.format(q=q, gold=gold, resp=resp)
    if "preference" in qt:
        return _PREF.format(q=q, gold=gold, resp=resp)
    return _BASE.format(q=q, gold=gold, resp=resp)


def judge(question, *, gold, response, question_type=None, is_abstention=False, provider) -> bool:
    out = provider.complete(_prompt(question, gold, response, question_type, is_abstention),
                            max_tokens=10, temperature=0.0)
    return "yes" in out.lower()
```

- [ ] **Step 6: Implement `generate.py`**

```python
# benchmarks/cairn_bench/qa/generate.py
# SPDX-License-Identifier: Apache-2.0
"""Generate an answer from the retrieved top-k Hits (hydrated to full chunk text)."""

from __future__ import annotations

from cairn.search import get_chunks

_READER = ("Answer the question using ONLY the context below. If the answer is not in "
           "the context, say you don't have that information.\n\nContext:\n{ctx}\n\n"
           "Question: {q}\nAnswer:")


def generate_answer(con, question: str, hits, *, provider, max_chunks: int = 10) -> str:
    ids = [h.chunk_id for h in hits[:max_chunks]]
    chunks = {c["chunk_id"]: c for c in get_chunks(con, ids)}
    ctx = "\n\n".join(
        f"[{chunks[cid]['heading_path']}] {chunks[cid]['text']}" for cid in ids if cid in chunks
    )
    return provider.complete(_READER.format(ctx=ctx, q=question), max_tokens=256, temperature=0.0)
```

```python
# benchmarks/cairn_bench/qa/__init__.py
# SPDX-License-Identifier: Apache-2.0
```

- [ ] **Step 7: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/test_qa.py -v`
Expected: PASS (fake-provider tests; no API key needed).

- [ ] **Step 8: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/cairn_bench/qa/ pyproject.toml uv.lock benchmarks/tests/test_qa.py
git commit -m "feat(bench): opt-in Anthropic QA layer (generate + per-type judge)"
```

---

### Task 9: Real-dataset downloader + run entrypoint

**Files:**
- Create: `benchmarks/cairn_bench/download.py`, `benchmarks/cairn_bench/run.py`
- Test: `benchmarks/tests/test_qa.py` (add `test_sha_verify`) — or a new `test_download.py`

**Context:** `download.py` fetches the pinned real datasets into `~/.cache/agentcairn/bench/`, verifying SHA256 against `manifest.toml` (recording it on first fetch). `run.py` is the manual entrypoint that wires adapt → build → ablation → report over a real dataset. The actual network fetch is opt-in (not in CI); only the SHA-verify helper is unit-tested.

- [ ] **Step 1: Write the failing test (SHA verify on a local file)**

```python
# benchmarks/tests/test_download.py
# SPDX-License-Identifier: Apache-2.0
import hashlib

from cairn_bench.download import sha256_of, verify_sha


def test_sha_roundtrip(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("[]")
    digest = sha256_of(f)
    assert digest == hashlib.sha256(b"[]").hexdigest()
    verify_sha(f, digest)          # exact match: no raise
    verify_sha(f, "")              # empty expected = "record on first fetch": no raise
    import pytest
    with pytest.raises(ValueError):
        verify_sha(f, "deadbeef")  # mismatch raises
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/test_download.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `download.py`**

```python
# benchmarks/cairn_bench/download.py
# SPDX-License-Identifier: Apache-2.0
"""Fetch the pinned real datasets into ~/.cache/agentcairn/bench, SHA-verified against
manifest.toml. LoCoMo is CC BY-NC 4.0 and is NEVER vendored — only cached locally."""

from __future__ import annotations

import hashlib
import tomllib
import urllib.request
from pathlib import Path

CACHE = Path.home() / ".cache" / "agentcairn" / "bench"
MANIFEST = Path(__file__).parent.parent / "manifest.toml"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_sha(path: Path, expected: str) -> None:
    if not expected:
        return  # empty in manifest = "record on first verified fetch"
    actual = sha256_of(path)
    if actual != expected:
        raise ValueError(f"SHA256 mismatch for {path}: got {actual}, expected {expected}")


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text())


def fetch(dataset: str) -> Path:
    """Download (if absent) and SHA-verify a dataset; return the cached path."""
    entry = _manifest()[dataset]
    CACHE.mkdir(parents=True, exist_ok=True)
    if entry["kind"] == "url":
        dest = CACHE / f"{dataset}.json"
        if not dest.exists():
            urllib.request.urlretrieve(entry["url"], dest)  # noqa: S310 (pinned https)
    elif entry["kind"] == "hf":
        from huggingface_hub import hf_hub_download

        src = hf_hub_download(repo_id=entry["repo_id"], filename=entry["filename"],
                              revision=entry["revision"], repo_type="dataset")
        dest = CACHE / entry["filename"]
        if not dest.exists():
            dest.write_bytes(Path(src).read_bytes())
    else:
        raise ValueError(f"unknown manifest kind: {entry['kind']}")
    verify_sha(dest, entry.get("sha256", ""))
    return dest
```

- [ ] **Step 4: Implement `run.py` (manual entrypoint)**

```python
# benchmarks/cairn_bench/run.py
# SPDX-License-Identifier: Apache-2.0
"""Manual benchmark entrypoint: `python -m cairn_bench.run --dataset longmemeval-s`.
Loads a real dataset (downloaded+pinned), runs the ablation matrix over a sample of
queries, prints the retrieval report. QA layer is opt-in via --qa (needs ANTHROPIC_API_KEY)."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from cairn.embed import get_embedder

from cairn_bench import download
from cairn_bench.ablation import run_arm
from cairn_bench.adapters import locomo, longmemeval
from cairn_bench.build import build_scoped_index
from cairn_bench.config import ARMS
from cairn_bench.report import aggregate, to_markdown

KS = [1, 3, 5, 10, 20]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", choices=["longmemeval-s", "locomo"], required=True)
    ap.add_argument("--limit", type=int, default=50, help="max instances/conversations to run")
    ap.add_argument("--embedder", default="fastembed")
    args = ap.parse_args()

    emb = get_embedder(args.embedder)
    per_query: list[dict] = []
    if args.dataset == "longmemeval-s":
        data = json.loads(download.fetch("longmemeval_s").read_text())
        records = [(longmemeval.adapt(inst)) for inst in data[: args.limit]]
    else:
        data = json.loads(download.fetch("locomo").read_text())
        records = [(locomo.adapt(s)) for s in data[: args.limit]]

    for notes, queries in records:
        with tempfile.TemporaryDirectory() as d:
            con, chunks = build_scoped_index(notes, Path(d), emb)
            try:
                for q in queries:
                    if not q.gold_turns and not q.gold_sessions:
                        continue
                    for arm in ARMS:
                        res = run_arm(con, arm, q, emb, ks=KS, pool=max(200, chunks))
                        per_query.append({"arm": arm.name, "category": q.category, **res})
            finally:
                con.close()

    agg = aggregate(per_query, ks=KS)
    print(to_markdown(agg, granularity="turn"))


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify the SHA test passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/test_download.py -v`
Expected: PASS. (The real fetch is not exercised in CI; verify manually with `uv run --group bench python -m cairn_bench.run --dataset locomo --limit 2 --embedder fake` once, separately.)

- [ ] **Step 6: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/cairn_bench/download.py benchmarks/cairn_bench/run.py benchmarks/tests/test_download.py
git commit -m "feat(bench): pinned SHA-verified downloader + manual run entrypoint"
```

---

### Task 10: Documentation

**Files:**
- Create: `benchmarks/README.md`

**Context:** How to run the harness and — critically — how to read the numbers without overclaiming. This is where the honest-reporting principles (§11 of the spec) live for humans.

- [ ] **Step 1: Write `benchmarks/README.md`**

````markdown
# agentcairn benchmark harness

Measures agentcairn **retrieval** quality (and optional end-to-end **QA accuracy**) on
LongMemEval-S and LoCoMo across a 5-arm ablation. Dev/research tool — NOT part of the
shipped `agentcairn` package.

## Quick start (offline, synthetic fixtures)

```bash
uv run pytest benchmarks/tests/      # offline, no keys, exact-recall regression
```

## Real datasets (manual)

```bash
# retrieval only (downloads + SHA-verifies into ~/.cache/agentcairn/bench)
uv run --group bench python -m cairn_bench.run --dataset longmemeval-s --limit 50
uv run --group bench python -m cairn_bench.run --dataset locomo
```

- **LongMemEval-S**: HuggingFace `xiaowu0162/longmemeval-cleaned` (MIT), revision-pinned.
- **LoCoMo**: GitHub `snap-research/locomo` (**CC BY-NC 4.0** — NonCommercial; never vendored).

## How to read the numbers (do not overclaim)

- **No single headline number.** Report ranges per arm/dataset/granularity.
- **Retrieval ≠ QA.** Never compare a retrieval recall to a QA accuracy.
- **Ablation is relative.** The arms differ only in the retrieval config; the absolute
  numbers depend on the embedder, k, and dataset slice — all pinned in each result row.
- **`graph-boost` is near-inert** on these conversational corpora (they have no native
  wikilink graph); the row ≈ plain hybrid by design. Cairn's graph wedge is for real vaults.
- **The reranker may lose** on chat turns (ms-marco domain shift) — that's a real result.
- **QA judge is Anthropic, not GPT-4o** — our QA numbers are NOT comparable to published
  LongMemEval/LoCoMo leaderboards. Use for relative ablation signal only.
- **Wrong-gold ceilings**: LoCoMo has a documented ~6.4% wrong-gold rate; cap claims at ~93.6%.
- Per-category accuracy carries **Wilson 95% CIs** — many adjacent comparisons are
  statistically indistinguishable; don't over-read orderings.
- LongMemEval "paper-style" `recall_all@k`/`ndcg_any@k` are labeled separately from our
  fractional `recall@k`; don't conflate.
````

- [ ] **Step 2: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add benchmarks/README.md
git commit -m "docs(bench): how to run + how to read the numbers (caveats)"
```

---

## Self-Review Notes (for the controller)

- **Spec coverage:** §3 layout (Tasks 2–10), §4 graph_boost (Task 1), §5 corpus→vault (Task 3), §6 ground-truth (Tasks 3,5), §7 metrics (Tasks 5,6), §8 QA (Task 8), §9 datasets/pinning/fixture (Tasks 2,9), §10 CI (Task 7), §11 honest reporting (Tasks 6,10), §13 task order — all mapped.
- **Type consistency:** `Query(qid, question, answer, gold_sessions, gold_turns, category, is_abstention, meta)`, `RankedRow(permalink, heading_path)`, `ArmConfig(name, rank)`, adapter `adapt(record) -> (list[Note], list[Query])`, `build_scoped_index(notes, work_dir, embedder) -> (con, chunk_count)`, `run_arm(con, arm, query, embedder, *, ks, pool) -> dict`, `aggregate(per_query, *, ks)`, `judge(question, *, gold, response, question_type, is_abstention, provider) -> bool` — consistent across tasks.
- **Latitude:** synthetic fixtures may be enlarged and exact-recall asserts tuned to the fixture, but never weaken the deterministic offline guarantee; never vendor a real dataset; never let the QA layer or `anthropic`/`huggingface_hub` leak into the core (`src/cairn`) install or the default `tests/` run.
