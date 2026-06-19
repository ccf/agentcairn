# Recall-Eval Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build #45 leg 1 — an offline plumbing-smoke test + a gated real-embedder recall-quality eval, plus the dedup-by-note fix in recall — so we can measure and safely tune recall ranking.

**Architecture:** One production change (`_dedupe_by_note` in `search/engine.py`, collapse chunks→note keeping best score) plus two test tiers under `tests/e2e/`: Tier 1 ingests a programmatic transcript fixture with the fake embedder and asserts capture→index→recall returns the note (always-on, offline); Tier 2 (skipped unless `CAIRN_E2E=1`) indexes authored vault fixtures with real `fastembed`, asserts atomic notes outrank a session-summary mega-note, and prints recall@k / MRR.

**Tech Stack:** Python, pytest, DuckDB, `cairn.embed` (`FakeEmbedder`, `get_embedder("fastembed")`), `cairn.index` (`open_index`, `index_vault`, `build_fts`), `cairn.search` (`open_search`, `search`, `Hit`), `cairn.ingest.pipeline` (`ingest_transcript`).

---

## File Structure

- `src/cairn/search/engine.py` — **modify** `search()`; add `_dedupe_by_note()` helper. One responsibility added: results are unique per note.
- `tests/search/test_engine.py` — **add** a unit test for dedup-by-note (reuses the existing `build_index` helper there).
- `tests/e2e/__init__.py` — **create** (empty package marker).
- `tests/e2e/test_recall_eval.py` — **create**; both tiers.
- `tests/e2e/fixtures/recall_eval/*.md` — **create**; authored vault notes (atomic facts + one session-summary mega-note) for Tier 2.
- `.github/workflows/` — **modify** the test workflow to add a gated `CAIRN_E2E=1` job (Task 5).

---

## Task 1: dedup-by-note in recall

**Files:**
- Modify: `src/cairn/search/engine.py` (the `search()` function, lines ~301-437; add helper above it)
- Test: `tests/search/test_engine.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/search/test_engine.py` (it already has `build_index` + `from cairn.search import ... search`). This writes a note whose body has two headed sections so it produces two chunks, then asserts recall returns that note once:

```python
def test_recall_dedupes_by_note(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import build_fts, index_vault, open_index
    from cairn.search import open_search, search

    emb = FakeEmbedder(dim=8)
    v = tmp_path / "vault"
    v.mkdir()
    # Two headed sections => two chunks of the SAME note, both mentioning "alpha".
    (v / "multi.md").write_text(
        "---\ntitle: Multi\npermalink: multi\n---\n"
        "## One\nalpha alpha beans.\n\n## Two\nalpha alpha brewing.\n"
    )
    (v / "other.md").write_text("---\ntitle: Other\npermalink: other\n---\nbeta gamma.\n")
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(v), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    hits = search(con, "alpha", embedder=emb, k=10)
    permalinks = [h.permalink for h in hits]
    assert permalinks.count("multi") == 1, f"note returned more than once: {permalinks}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/search/test_engine.py::test_recall_dedupes_by_note -v`
Expected: FAIL — `multi` appears twice (recall is currently chunk-level).

- [ ] **Step 3: Add the dedup helper**

In `src/cairn/search/engine.py`, add this function immediately above `def search(` (~line 301):

```python
def _dedupe_by_note(rows: list[dict]) -> list[dict]:
    """Collapse multiple chunks of the same note into one result, keeping the
    best-scoring chunk. ``rows`` must already be sorted best-first."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in rows:
        pk = r["note_permalink"]
        if pk in seen:
            continue
        seen.add(pk)
        out.append(r)
    return out
```

- [ ] **Step 4: Widen the candidate pool and apply dedup in `search()`**

In `search()`: (a) both the `hybrid_search(...)` and `bm25_only(...)` calls currently pass `limit=(max(20, k) if rerank else k)` — change BOTH to `limit=max(20, k)` so the non-rerank path also has enough candidates to dedup down to `k` notes. (b) Change the rerank call `ranked = rerank_candidates(query, cands, top_k=k)` to `top_k=max(20, k)`. (c) Replace the `else: rows = rows[:k]` branch with `else: pass` (keep all fetched rows). (d) Immediately before the final `return [Hit(...) ...]`, insert:

```python
    rows = _dedupe_by_note(rows)[:k]
```

- [ ] **Step 5: Run the new test + the whole search suite**

Run: `uv run pytest tests/search/ -q`
Expected: PASS (incl. `test_recall_dedupes_by_note`; existing engine/search/rerank tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/search/engine.py tests/search/test_engine.py
git commit -m "fix(search): dedupe recall results by note (keep best chunk)"
```

---

## Task 2: Tier 1 — offline plumbing smoke (capture → index → recall)

**Files:**
- Create: `tests/e2e/__init__.py`
- Create: `tests/e2e/test_recall_eval.py`

Mirrors the proven offline ingest pattern in `tests/ingest/test_pipeline.py` (`_ev`, `Transcript`, `ingest_transcript`) so it never hits the network or the LLM judge.

- [ ] **Step 1: Create the package marker**

Create `tests/e2e/__init__.py` (empty file).

- [ ] **Step 2: Write the Tier 1 test**

Create `tests/e2e/test_recall_eval.py`:

```python
# SPDX-License-Identifier: Apache-2.0
import os

import pytest

from cairn.embed import FakeEmbedder
from cairn.index import build_fts, index_vault, open_index
from cairn.ingest.dedup import DedupLedger
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.models import Transcript
from cairn.ingest.pipeline import ingest_transcript
from cairn.search import open_search, search


def _ev(kind: EventKind, text: str) -> NormalizedEvent:
    return NormalizedEvent(kind=kind, text=text)


def test_core_loop_offline(tmp_path):
    """Capture -> index -> recall, end to end, offline (fake embedder, no judge).
    Guards the loop the SessionEnd/PreCompact sweep runs."""
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    transcript = Transcript(
        session_id="e2e-1",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "e2e-1.jsonl",
        events=[
            _ev(
                EventKind.AUTHORED_USER,
                "We decided to pin the DuckDB version to 1.1 because 1.2 broke array_cosine_similarity.",
            ),
        ],
    )
    report = ingest_transcript(transcript, vault_root=vault, ledger=ledger)
    assert report.written, "ingest wrote no notes"

    emb = FakeEmbedder(dim=8)
    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(vault), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    hits = search(con, "why is the DuckDB version pinned", embedder=emb, k=10)
    assert hits, "recall returned nothing for an ingested fact"
    # Membership only — fake vectors make ranking meaningless; Tier 2 asserts ranking.
    blob = " ".join(h.snippet.lower() for h in hits)
    assert "duckdb" in blob
```

- [ ] **Step 3: Run it**

Run: `uv run pytest tests/e2e/test_recall_eval.py::test_core_loop_offline -v`
Expected: PASS. (If `NormalizedEvent`/`Transcript` constructor args differ, copy the exact construction from `tests/ingest/test_pipeline.py` `_transcript()`/`_ev` — that is the source of truth for these types.)

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/__init__.py tests/e2e/test_recall_eval.py
git commit -m "test(e2e): offline plumbing smoke for capture->index->recall"
```

---

## Task 3: Tier 2 — authored fixtures + gated recall-quality eval

**Files:**
- Create: `tests/e2e/fixtures/recall_eval/cairn-link-scope.md`, `vault-scoped-index.md`, `precompact-capture.md`, `session-summary-2026-06-18.md`
- Modify: `tests/e2e/test_recall_eval.py` (append the gated test)

- [ ] **Step 1: Create the atomic fixture notes**

`tests/e2e/fixtures/recall_eval/cairn-link-scope.md`:
```markdown
---
title: cairn link scope is Obsidian-graph-focused
permalink: cairn-link-scope
type: memory
---
The `cairn link` command writes a `related:` frontmatter list of wikilinks from each note's semantic neighbors, so the Obsidian graph renders edges. It deliberately does not add an agentcairn link table.
```

`tests/e2e/fixtures/recall_eval/vault-scoped-index.md`:
```markdown
---
title: the DuckDB index is vault-scoped
permalink: vault-scoped-index
type: memory
---
The index path is derived from a hash of the resolved vault path, so a scratch vault can never pollute the production index. Doctor reports DRIFT when they diverge.
```

`tests/e2e/fixtures/recall_eval/precompact-capture.md`:
```markdown
---
title: capture runs on PreCompact, not only SessionEnd
permalink: precompact-capture
type: memory
---
A PreCompact hook runs the detached cairn sweep so long and resumed sessions are captured at each compaction boundary, instead of only when a session formally ends.
```

`tests/e2e/fixtures/recall_eval/session-summary-2026-06-18.md` (the greedy mega-note):
```markdown
---
title: Session summary 2026-06-18
permalink: session-summary-2026-06-18
type: memory
---
Wide-ranging session covering the Obsidian plugin, the store submission, cairn link and the Obsidian graph, the vault-scoped index and drift, the DuckDB version, PreCompact capture, recall quality, the launch kit, and many merges and releases. Touches cairn link scope, vault-scoped index, and PreCompact capture among dozens of other topics.
```

- [ ] **Step 2: Write the failing gated test**

Append to `tests/e2e/test_recall_eval.py`:

```python
_LABELS = [
    ("what is the scope of cairn link for the Obsidian graph", "cairn-link-scope"),
    ("how does the index avoid scratch-vault pollution", "vault-scoped-index"),
    ("when does capture run relative to compaction", "precompact-capture"),
]
_SUMMARY = "session-summary-2026-06-18"


@pytest.mark.skipif(
    not os.environ.get("CAIRN_E2E"),
    reason="set CAIRN_E2E=1 to run the real-embedder recall-quality eval",
)
def test_recall_quality(tmp_path):
    from pathlib import Path

    fixtures = Path(__file__).parent / "fixtures" / "recall_eval"
    vault = tmp_path / "vault"
    vault.mkdir()
    for md in fixtures.glob("*.md"):
        (vault / md.name).write_text(md.read_text())

    try:
        emb = get_embedder("fastembed")
    except Exception as exc:  # model unavailable offline -> skip, never fail
        pytest.skip(f"fastembed unavailable: {exc}")

    idx = str(tmp_path / "i.duckdb")
    con = open_index(idx, dim=emb.dim, model_id=emb.model_id)
    index_vault(con, str(vault), emb)
    build_fts(con)
    con.close()

    con = open_search(idx)
    k = 10
    hit_at_k = 0
    rr_total = 0.0
    failures = []
    for query, expected in _LABELS:
        hits = search(con, query, embedder=emb, k=k, rerank=True)
        permalinks = [h.permalink for h in hits]
        # dedup-by-note invariant
        assert len(permalinks) == len(set(permalinks)), f"dup notes: {permalinks}"
        if expected in permalinks:
            hit_at_k += 1
            rr_total += 1.0 / (permalinks.index(expected) + 1)
        # ranking: the atomic note must outrank the greedy session summary
        e_idx = permalinks.index(expected) if expected in permalinks else 10**6
        s_idx = permalinks.index(_SUMMARY) if _SUMMARY in permalinks else 10**6
        if not e_idx < s_idx:
            failures.append((query, expected, permalinks))

    recall_at_k = hit_at_k / len(_LABELS)
    mrr = rr_total / len(_LABELS)
    print(f"\n[recall-eval] recall@{k}={recall_at_k:.3f} MRR={mrr:.3f}")
    assert not failures, f"atomic note did not outrank the session summary: {failures}"
    assert recall_at_k == 1.0, f"recall@{k}={recall_at_k}"
```

Add `get_embedder` to the imports at the top: `from cairn.embed import FakeEmbedder, get_embedder`.

- [ ] **Step 3: Run it with the gate on**

Run: `CAIRN_E2E=1 uv run pytest tests/e2e/test_recall_eval.py::test_recall_quality -v -s`
Expected: PASS, and the captured output shows a `[recall-eval] recall@10=… MRR=…` line. If the atomic-note-outranks-summary assertion fails, that is the recall-quality bug the harness exists to expose — capture the metric, then (separately, after this PR) tune ranking until it passes. For THIS PR the fixtures are designed so dedup-by-note + the existing reranker make it pass; if it does not, reduce the summary note's overlap or raise k and note the metric in the PR.

- [ ] **Step 4: Confirm it skips by default**

Run: `uv run pytest tests/e2e/ -q`
Expected: `test_core_loop_offline` PASS, `test_recall_quality` SKIPPED.

- [ ] **Step 5: Commit**

```bash
git add tests/e2e/fixtures/recall_eval tests/e2e/test_recall_eval.py
git commit -m "test(e2e): gated recall-quality eval (atomic notes outrank summaries) + metric"
```

---

## Task 4: gated CI job for the recall-quality eval

**Files:**
- Modify: the test workflow under `.github/workflows/` (read the existing file first to match its style; it currently runs `format · lint · test`, `validate`, `bench-offline`).

- [ ] **Step 1: Read the existing workflow**

Run: `ls .github/workflows/ && cat .github/workflows/<the-test-workflow>.yml`
Identify the job that runs pytest and how Python/uv are set up.

- [ ] **Step 2: Add a gated job**

Add a new job mirroring the existing pytest job's setup (checkout + Python/uv install), but running only the e2e quality leg with the gate on and `-s` so the metric prints:

```yaml
  recall-eval:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      # ...mirror the existing job's Python/uv setup steps here...
      - run: CAIRN_E2E=1 uv run pytest tests/e2e/test_recall_eval.py -q -s
```

(Place it as a separate job so a slow/regressing recall leg never blocks the fast default suite. `fastembed` downloads its model on first run; if the existing jobs cache `~/.cache`, reuse that cache key.)

- [ ] **Step 3: Validate YAML locally**

Run: `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('ok')"`
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows
git commit -m "ci: gated recall-eval job (CAIRN_E2E=1)"
```

---

## Self-Review

**Spec coverage:** Tier 1 plumbing smoke → Task 2. Tier 2 gated quality eval (real fastembed, atomic-outranks-summary, recall@k/MRR) → Task 3. dedup-by-note + unit test → Task 1 (and asserted in Task 3). Gating/CI → Tasks 3+4. Out-of-scope legs (MCP/host/plugin) correctly absent.

**Placeholder scan:** every code step has concrete code; the one explicit confirm-against-source note (Transcript/NormalizedEvent constructor in Task 2) points at the exact existing helper (`tests/ingest/test_pipeline.py::_transcript`) rather than inventing args.

**Type consistency:** `Hit.permalink` (not `note_permalink`) is used on `search()` results in tests; the dict key `note_permalink` is used inside `_dedupe_by_note` (operates on the row dicts, which use `note_permalink`) — verified against `engine.py` (rows use `note_permalink`; `Hit` exposes `permalink`).
