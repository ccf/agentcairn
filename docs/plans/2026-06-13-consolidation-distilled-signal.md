# Consolidation on the Distilled Signal Implementation Plan (0.10.1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make memory consolidation's neighbor lookup use the clean distilled-vs-distilled signal on incremental sweeps (not just within-sweep), by replacing the DuckDB chunk-embedding neighbor index with a vault-backed distilled-`[context]` index, re-tuning the cosine gate on that signal, and fixing the eval script.

**Architecture:** A new `_DistilledNeighborIndex` (in `cli.py`) loads every *live* vault note's `[context]` line at sweep start, embeds them batched in memory, and serves `nearest()` over (preloaded-live ∪ this-sweep batch) — dropping DuckDB from consolidation entirely. A shared `extract_context` helper (in `consolidate.py`) is used by both the index and the eval script so they measure the same text.

**Tech Stack:** Python 3.12, `uv`, pytest, fastembed (nomic). Spec: `docs/specs/2026-06-13-consolidation-distilled-signal-design.md`.

**Conventions:**
- Tests: `uv run pytest`. Pre-commit runs ruff + ruff-format + pytest; **ruff-format reformats on the first commit attempt and aborts it** — re-`git add -A` and re-run the same `git commit`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- The consolidation *machinery* (pipeline step, `LLMConsolidator`, verdicts, fail-safe) is **unchanged**. Pipeline consolidation tests (which inject `_FakeNeighborIndex`) must stay green — the `NeighborIndex` protocol (`nearest(text)`, `add(permalink, text, timestamp, path=None)`, `note_superseded(permalink)`) is preserved.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cairn/ingest/consolidate.py` | consolidation types + `extract_context` + gate | add `extract_context(body)`; re-tune `_CONSOLIDATE_GATE` |
| `src/cairn/cli.py` | `_DistilledNeighborIndex` + sweep wiring | replace `_DuckDBNeighborIndex`; build vault-backed index; drop `nbr_con`/`vector_search`/`datetime` if now unused |
| `scripts/eval_consolidate.py` | gate validation tool | batch embeddings; embed `[context]`; exclude superseded; `CAIRN_EVAL_EMBEDDER` hook |
| `src/cairn/__init__.py`, `CHANGELOG.md` | 0.10.1 | version + notes |

---

## Task 1: `extract_context` helper

**Files:** Modify `src/cairn/ingest/consolidate.py`; Test `tests/ingest/test_consolidate.py`.

- [ ] **Step 1: Write the failing test.** Append to `tests/ingest/test_consolidate.py`:

```python
def test_extract_context():
    from cairn.ingest.consolidate import extract_context

    assert (
        extract_context("- [context] The endpoint is https://x #ingested\n- [verbatim] raw turn\n")
        == "The endpoint is https://x"
    )
    # no LLM distillation -> the [context] line holds the verbatim, still extracted
    assert extract_context("- [context] just the verbatim text #ingested\n") == "just the verbatim text"
    # a note with no [context] line
    assert extract_context("some hand-authored body without the marker") is None
    # tolerate a missing #ingested suffix
    assert extract_context("- [context] bare fact\n") == "bare fact"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_consolidate.py::test_extract_context -v`
Expected: FAIL — `extract_context` not defined.

- [ ] **Step 3: Implement.** In `src/cairn/ingest/consolidate.py`, add `import re` to the imports and add this function (place it near the top-level helpers, after `_CONSOLIDATE_GATE`):

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/ingest/test_consolidate.py -v`
Expected: PASS (new test + existing consolidate tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/consolidate.py tests/ingest/test_consolidate.py
git commit -m "feat(consolidate): extract_context helper (distilled [context] text)"
```

---

## Task 2: Re-tune `_CONSOLIDATE_GATE` on the distilled signal

**Files:** Modify `src/cairn/ingest/consolidate.py`; Test `tests/ingest/test_consolidate.py`.

The gate must admit the known dup/supersession pairs on the distilled signal while keeping above-gate volume small. Measure, then set.

- [ ] **Step 1: Measure the known pairs (manual, not a unit test).** Run this against the live vault to see the distilled-cosine of the real targets and the above-gate volume:

```bash
uv run python -c "
import math, glob
from cairn.embed import get_embedder
from cairn.ingest.consolidate import extract_context
from cairn.vault import parse_note
emb = get_embedder('fastembed')
def cos(a,b):
    d=sum(x*y for x,y in zip(a,b)); na=math.sqrt(sum(x*x for x in a)); nb=math.sqrt(sum(y*y for y in b))
    return 0.0 if na==0 or nb==0 else d/(na*nb)
ps = sorted(glob.glob('$HOME/agentcairn/memories/*.md'))
items=[]
for p in ps:
    c = extract_context(parse_note(open(p,encoding='utf-8').read()).body)
    if c: items.append((p.split('/')[-1], c))
texts=[c for _,c in items]
vecs=[]
import itertools
B=64
for i in range(0,len(texts),B): vecs.extend(emb.embed(texts[i:i+B]))
# top-1 neighbor cosine distribution
import statistics
top1=[]
for i in range(len(vecs)):
    best=max((cos(vecs[i],vecs[j]) for j in range(len(vecs)) if j!=i), default=0.0)
    top1.append(best)
for g in (0.90,0.88,0.86,0.85,0.84,0.82,0.80):
    print('>= %.2f : %d/%d'%(g, sum(1 for v in top1 if v>=g), len(top1)))
# known pairs: signoz endpoint, fly RAM scaling
def grp(sub1, sub2=None):
    idx=[k for k,(n,c) in enumerate(items) if sub1 in c.lower() and (sub2 is None or sub2 in c.lower())]
    return idx
sig=grp('signoz','endpoint') or grp('signoz','ingest')
ram=grp('gb')
ramfly=[k for k in ram if 'ram' in items[k][1].lower() or 'memory' in items[k][1].lower()]
print('signoz-endpoint idx:', [(items[k][0]) for k in sig])
for a,b in itertools.combinations(sig,2): print('  signoz cos %.3f'%cos(vecs[a],vecs[b]))
print('fly RAM idx:', [(items[k][0]) for k in ramfly][:6])
for a,b in itertools.combinations(ramfly,2): print('  ram cos %.3f  %s ~ %s'%(cos(vecs[a],vecs[b]), items[a][0][:25], items[b][0][:25]))
"
```
Read the output: note the lowest cosine among the genuine dup/supersession pairs and the above-gate count at candidate thresholds. **Choose the gate = a value just at/below the lowest true-pair cosine, biased toward keeping the ≥gate count modest** (the LLM adjudicates everything above it; fail-safe means a slightly-too-low gate only costs classifier calls, never a wrong drop). The spec's target is **0.85**; use it unless the measurement shows the true pairs sit clearly lower (then pick that, rounded down to 0.01) or that 0.85 floods (>~15% of notes — then nudge up).

- [ ] **Step 2: Set the gate.** In `src/cairn/ingest/consolidate.py`, set the constant to the chosen value (default 0.85) and update its comment to reflect the distilled-signal calibration:

```python
_CONSOLIDATE_GATE = 0.85  # cosine below this -> no classify call. Calibrated on the
# DISTILLED [context] signal (0.10.1): full-note embeddings cluster by genre and are
# useless here; distilled-vs-distilled separates (the known dup/supersede pairs clear
# this gate, while most distinct notes fall below it). The LLM adjudicates above it.
```

- [ ] **Step 3: Add an assertion test.** Append to `tests/ingest/test_consolidate.py`:

```python
def test_gate_calibrated_for_distilled_signal():
    from cairn.ingest.consolidate import _CONSOLIDATE_GATE

    assert _CONSOLIDATE_GATE == 0.85  # distilled-signal calibration (0.10.1)
```
(If Step 1 led you to a different value, use that value in both the constant and this assertion.)

- [ ] **Step 4: Run + commit.**

Run: `uv run pytest tests/ingest/test_consolidate.py -q` (all pass).

```bash
git add src/cairn/ingest/consolidate.py tests/ingest/test_consolidate.py
git commit -m "fix(consolidate): re-tune gate to 0.85 for the distilled signal"
```

---

## Task 3: `_DistilledNeighborIndex` (replaces `_DuckDBNeighborIndex`)

**Files:** Modify `src/cairn/cli.py`; Test `tests/test_cli.py`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/test_cli.py`:

```python
def test_distilled_neighbor_index_loads_live_and_excludes_superseded(tmp_path):
    from cairn.cli import _DistilledNeighborIndex

    class FakeEmbedder:
        dim = 3

        def embed(self, texts):
            out = []
            for t in texts:
                tl = t.lower()
                out.append([1.0, 0.0, 0.0] if "ram" in tl else ([0.0, 1.0, 0.0] if "signoz" in tl else [0.0, 0.0, 1.0]))
            return out

    mem = tmp_path / "memories"
    mem.mkdir()
    (mem / "ram-live.md").write_text(
        "---\ntitle: RAM\ntype: memory\npermalink: ram-live\ncreated: '2026-06-01T00:00:00'\n---\n\n- [context] scale RAM to 2GB #ingested\n",
        encoding="utf-8",
    )
    (mem / "ram-old.md").write_text(  # superseded -> must be excluded
        "---\ntitle: RAM old\ntype: memory\npermalink: ram-old\nsuperseded_by: ram-live\n---\n\n- [context] scale RAM to 1GB #ingested\n",
        encoding="utf-8",
    )
    (mem / "no-context.md").write_text(  # no [context] -> skipped
        "---\ntitle: hand\ntype: memory\npermalink: hand\n---\n\nhand-authored body\n",
        encoding="utf-8",
    )
    nidx = _DistilledNeighborIndex(vault_root=tmp_path, subdir="memories", embedder=FakeEmbedder())
    hit = nidx.nearest("scale RAM to 4GB")
    assert hit is not None
    neighbor, cos = hit
    assert neighbor.permalink == "ram-live"  # the live note, not the superseded one
    assert neighbor.timestamp == "2026-06-01T00:00:00"  # created frontmatter
    assert neighbor.path and neighbor.path.endswith("ram-live.md")
    # an orthogonal query matches nothing above the gate
    assert nidx.nearest("totally unrelated topic xyz") is None


def test_distilled_neighbor_index_batch_and_note_superseded(tmp_path):
    from cairn.cli import _DistilledNeighborIndex

    class FakeEmbedder:
        dim = 2

        def embed(self, texts):
            return [[1.0, 0.0] if "ram" in t.lower() else [0.0, 1.0] for t in texts]

    (tmp_path / "memories").mkdir()
    nidx = _DistilledNeighborIndex(vault_root=tmp_path, subdir="memories", embedder=FakeEmbedder())
    assert nidx.nearest("ram 4gb") is None  # empty
    nidx.add("ram-2gb", "scale ram to 2gb", "t0", str(tmp_path / "memories" / "ram-2gb.md"))
    hit = nidx.nearest("scale ram to 4gb")
    assert hit is not None and hit[0].permalink == "ram-2gb"
    nidx.note_superseded("ram-2gb")
    assert nidx.nearest("scale ram to 4gb") is None  # flagged -> skipped
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "distilled_neighbor" -v`
Expected: FAIL — `_DistilledNeighborIndex` not defined.

- [ ] **Step 3: Implement.** In `src/cairn/cli.py`:

Update imports:
- Change `from cairn.ingest.consolidate import _CONSOLIDATE_GATE, Neighbor, resolve_consolidator` to also import `extract_context`.
- Add `from cairn.ingest.judge import _EMBED_BATCH` and `from cairn.vault import parse_note` (if not already imported — check the existing import block and add only what's missing).

Replace the entire `_DuckDBNeighborIndex` class with:

```python
class _DistilledNeighborIndex:
    """NeighborIndex over the DISTILLED `[context]` text of live vault notes (loaded
    and embedded at construction), unioned with this-sweep's writes. No DuckDB: the
    recall chunk embeddings include the `[verbatim]` turn and cluster by conversational
    genre (useless for dedup); distilled-vs-distilled separates cleanly (0.10.1)."""

    def __init__(self, *, vault_root: Path, subdir: str, embedder) -> None:
        self._embedder = embedder
        # (permalink, vec, distilled_text, created_ts, path)
        self._batch: list[tuple[str, list[float], str, str | None, str | None]] = []
        self._superseded: set[str] = set()
        loaded: list[tuple[str, str, str | None, str]] = []  # perm, ctx, created, path
        for p in sorted((vault_root / subdir).glob("*.md")):
            try:
                note = parse_note(p.read_text(encoding="utf-8"))
            except Exception:
                continue  # a malformed note must not abort the sweep
            if note.frontmatter.get("superseded_by"):
                continue  # already demoted — never match against it
            ctx = extract_context(note.body)
            if not ctx:
                continue
            perm = note.permalink or note.frontmatter.get("permalink") or p.stem
            loaded.append((perm, ctx, note.frontmatter.get("created"), str(p.resolve())))
        self._live: list[tuple[str, list[float], str, str | None, str]] = []
        for i in range(0, len(loaded), _EMBED_BATCH):  # batch to avoid OOM on big vaults
            batch = loaded[i : i + _EMBED_BATCH]
            for (perm, ctx, created, path), vec in zip(
                batch, embedder.embed([b[1] for b in batch]), strict=True
            ):
                self._live.append((perm, vec, ctx, created, path))

    def _embed(self, text: str) -> list[float]:
        return self._embedder.embed([text])[0]

    def nearest(self, text: str):
        vec = self._embed(text)
        best = None  # (Neighbor, cosine)
        for perm, nvec, ntext, nts, npath in (*self._live, *self._batch):
            if perm in self._superseded:
                continue
            cos = _cosine(vec, nvec)
            if best is None or cos > best[1]:
                best = (Neighbor(permalink=perm, text=ntext, timestamp=nts, path=npath), cos)
        if best is None or best[1] < _CONSOLIDATE_GATE:
            return None
        return best

    def add(self, permalink: str, text: str, timestamp: str | None, path: str | None = None) -> None:
        self._batch.append((permalink, self._embed(text), text, timestamp, path))

    def note_superseded(self, permalink: str) -> None:
        self._superseded.add(permalink)
```

(Keep the existing module-level `_cosine` helper. Do NOT delete it.)

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "distilled_neighbor" -v`
Expected: PASS. (The old `_DuckDBNeighborIndex` tests will now error — they're removed/replaced in Task 4's wiring step; if a test named `test_duckdb_neighbor_index*` still exists, delete it now since the class is gone, and note it.)

Also delete the two now-obsolete tests `test_duckdb_neighbor_index_unions_index_and_batch` and `test_duckdb_neighbor_index_arm_and_iso_timestamp` from `tests/test_cli.py` (their class no longer exists; the two new tests above replace their coverage).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): _DistilledNeighborIndex (vault-backed distilled signal, no DuckDB)"
```

---

## Task 4: Sweep wiring — build the vault-backed index, drop the DuckDB handle

**Files:** Modify `src/cairn/cli.py`; Test: existing suite.

- [ ] **Step 1: Replace the neighbor wiring block.** In the `sweep` command, replace:

```python
    consolidator = resolve_consolidator()
    neighbor_index = None
    nbr_con = None
    if consolidator is not None and idx.exists():
        nbr_con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
        neighbor_index = _DuckDBNeighborIndex(con=nbr_con, dim=emb.dim, embedder=emb)
    elif consolidator is not None:
        neighbor_index = _DuckDBNeighborIndex(con=None, dim=emb.dim, embedder=emb)
    try:
        rep = ingest_transcripts(
            transcripts,
            vault_root=vault,
            ledger=led,
            threshold=threshold,
            judge=resolve_judge(embedder=emb),
            judged_cache=JudgedCache(led_path.parent / f"{vault_key}.judged.jsonl"),
            consolidator=consolidator,
            neighbor_index=neighbor_index,
        )
    finally:
        # Release the neighbor read handle before reconcile opens its write handle.
        if nbr_con is not None:
            nbr_con.close()
```

with:

```python
    consolidator = resolve_consolidator()
    neighbor_index = (
        _DistilledNeighborIndex(vault_root=vault, subdir="memories", embedder=emb)
        if consolidator is not None
        else None
    )
    rep = ingest_transcripts(
        transcripts,
        vault_root=vault,
        ledger=led,
        threshold=threshold,
        judge=resolve_judge(embedder=emb),
        judged_cache=JudgedCache(led_path.parent / f"{vault_key}.judged.jsonl"),
        consolidator=consolidator,
        neighbor_index=neighbor_index,
    )
```

(Confirm `subdir="memories"` matches the `subdir` the sweep passes elsewhere; the sweep writes notes under `memories` by default — match whatever default `ingest_transcripts(..., subdir=...)` uses. If the sweep customizes `subdir`, thread the same value here.)

- [ ] **Step 2: Remove now-unused imports.** `vector_search` (from `cairn.search.engine`) is no longer used — delete its import. Check whether `import datetime` is still used anywhere in `cli.py` (it was only for the DuckDB-arm mtime→ISO conversion); if unused now, remove it. Run `uv run ruff check src/cairn/cli.py` — it flags unused imports (F401); remove exactly what it reports.

- [ ] **Step 3: Run the suite.**

Run: `uv run pytest -q` then `uv run ruff check src tests`.
Expected: all pass, ruff clean. (Pipeline consolidation tests use `_FakeNeighborIndex` and are unaffected; CLI sweep tests should pass — the index now builds from the vault dir, which exists in those tests.)

- [ ] **Step 4: Commit**

```bash
git add src/cairn/cli.py
git commit -m "feat(cli): wire _DistilledNeighborIndex into sweep; drop DuckDB neighbor handle"
```

---

## Task 5: Fix `scripts/eval_consolidate.py`

**Files:** Modify `scripts/eval_consolidate.py`; Test `tests/test_eval_consolidate.py` (new, smoke).

- [ ] **Step 1: Write the failing smoke test.** Create `tests/test_eval_consolidate.py`:

```python
# SPDX-License-Identifier: Apache-2.0
import subprocess
import sys
from pathlib import Path


def test_eval_consolidate_smoke(tmp_path):
    """The eval script runs to completion on a tiny vault without OOM/crash, using
    the fake embedder (no model download). Guards the OOM regression."""
    mem = tmp_path / "memories"
    mem.mkdir()
    for i, ctx in enumerate(["scale RAM to 2GB", "scale RAM to 4GB", "deploy the website"]):
        (mem / f"n{i}.md").write_text(
            f"---\npermalink: n{i}\ntype: memory\n---\n\n- [context] {ctx} #ingested\n",
            encoding="utf-8",
        )
    script = Path(__file__).resolve().parents[1] / "scripts" / "eval_consolidate.py"
    r = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        capture_output=True,
        text=True,
        env={"CAIRN_EVAL_EMBEDDER": "fake", "PATH": __import__("os").environ.get("PATH", "")},
    )
    assert r.returncode == 0, r.stderr
    assert "gate=" in r.stdout
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_eval_consolidate.py -v`
Expected: FAIL — the current script OOMs / embeds full text / has no `CAIRN_EVAL_EMBEDDER` hook (the `fake` embedder path won't be honored, or it crashes).

- [ ] **Step 3: Rewrite `scripts/eval_consolidate.py`:**

```python
# scripts/eval_consolidate.py
# SPDX-License-Identifier: Apache-2.0
"""Validate _CONSOLIDATE_GATE on the real vault: embed each live note's DISTILLED
[context] line (the production consolidation signal — NOT the full note body, which
clusters by genre) and report the top-1 nearest-neighbor cosine distribution so a
human can confirm the gate separates dups from distinct notes. Run:
    uv run python scripts/eval_consolidate.py [vault]
Set CAIRN_EVAL_EMBEDDER=fake for a model-free smoke run. Analysis tool — never edits."""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

from cairn.embed import get_embedder
from cairn.ingest.consolidate import _CONSOLIDATE_GATE, extract_context
from cairn.vault import parse_note

_EMBED_BATCH = 64


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


def main() -> None:
    vault = Path(sys.argv[1]) if len(sys.argv) > 1 else Path.home() / "agentcairn"
    items = []  # (name, distilled_text)
    for p in sorted((vault / "memories").glob("*.md")):
        try:
            note = parse_note(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if note.frontmatter.get("superseded_by"):
            continue  # exclude already-demoted notes
        ctx = extract_context(note.body)
        if ctx:
            items.append((p.name, ctx))
    if not items:
        print(f"no live [context] notes under {vault}/memories")
        return
    emb = get_embedder(os.environ.get("CAIRN_EVAL_EMBEDDER", "fastembed"))
    vecs = []
    texts = [c for _, c in items]
    for i in range(0, len(texts), _EMBED_BATCH):  # batch -> no OOM
        vecs.extend(emb.embed(texts[i : i + _EMBED_BATCH]))
    sims = []
    for i in range(len(vecs)):
        best, bj = 0.0, -1
        for j in range(len(vecs)):
            if j == i:
                continue
            c = _cos(vecs[i], vecs[j])
            if c > best:
                best, bj = c, j
        sims.append((best, items[i][0], items[bj][0] if bj >= 0 else "-"))
    sims.sort(reverse=True)
    above = [s for s in sims if s[0] >= _CONSOLIDATE_GATE]
    print(f"live notes={len(items)} gate={_CONSOLIDATE_GATE}")
    print(f"pairs at/above gate (consolidation candidates): {len(above)}")
    for c, a, b in above[:40]:
        print(f"  {c:.3f}  {a}  ~  {b}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_eval_consolidate.py -v`
Expected: PASS (script exits 0 on the fixture with the fake embedder, prints `gate=`).

- [ ] **Step 5: Commit**

```bash
git add scripts/eval_consolidate.py tests/test_eval_consolidate.py
git commit -m "fix(scripts): eval_consolidate batches embeddings + uses distilled [context]"
```

---

## Task 6: 0.10.1 bump + CHANGELOG

**Files:** Modify `src/cairn/__init__.py`, `CHANGELOG.md`.

- [ ] **Step 1: Bump version.** `src/cairn/__init__.py`: `__version__ = "0.10.1"`.

- [ ] **Step 2: CHANGELOG.** Insert after `## [Unreleased]`:

```markdown
## [0.10.1] - 2026-06-13

### Fixed
- **Memory consolidation now works on incremental sweeps, not just full rebuilds.** Dogfooding 0.10.0 showed the cosine gate only separates duplicates on *distilled-vs-distilled* embeddings — comparing against the recall index's full-note-body chunk embeddings clustered by conversational genre (74% of notes scored ≥0.88, and the highest-cosine pairs were distinct). Consolidation's neighbor lookup now embeds each live note's distilled `[context]` line (excluding superseded notes), held in memory per sweep, dropping the DuckDB dependency from the consolidation path entirely. The gate is re-tuned to 0.85 on this signal, and `scripts/eval_consolidate.py` is fixed (it OOM'd, and measured the wrong full-body signal).
```

Add the link ref above the `[0.10.0]:` line:
```markdown
[0.10.1]: https://github.com/ccf/agentcairn/compare/v0.10.0...v0.10.1
```

- [ ] **Step 3: Full suite + commit.**

Run: `uv run pytest -q` (all pass), `uv run ruff check src tests` (clean).

```bash
git add src/cairn/__init__.py CHANGELOG.md
git commit -m "chore(release): 0.10.1 — consolidation on the distilled signal"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest -q` green; `uv run ruff check src tests` clean.
- [ ] PR; CI + Cursor Bugbot; fix Bugbot findings; rebase-merge `--delete-branch`.
- [ ] Tag `v0.10.1`; `gh release create`; confirm PyPI publish.
- [ ] **Dogfood:** `uv run python scripts/eval_consolidate.py ~/agentcairn` — confirm the known dup pairs (SigNoz endpoint) sit ≥ gate and the above-gate volume is modest. Then a full from-scratch re-gate (back up first); confirm the SigNoz endpoint dup collapses to one note and the Fly RAM series leaves a single live note with the others carrying `superseded_by`. Report `semantic_deduped` / `superseded` counts.

---

## Self-Review

**Spec coverage:**
- §A `_DistilledNeighborIndex` (vault load, skip superseded, extract_context, batched embed, in-memory cosine over live∪batch, gate, nearest/add/note_superseded) → Task 3. ✓
- §B `extract_context` in consolidate.py → Task 1. ✓
- §C sweep wiring (build vault index, drop nbr_con + vector_search) → Task 4. ✓
- §D gate re-tune → Task 2. ✓
- §E eval script fix (batch, [context], exclude superseded) → Task 5. ✓
- release 0.10.1 → Task 6. ✓
- edge cases (no [context] skip, empty vault, superseded-at-load, missing created, OOM batch, malformed note) → Task 3 impl + tests. ✓

**Placeholder scan:** No TBD/TODO. The gate value is concrete (0.85) with a measurement step that may adjust it — that's validation, not a placeholder; the constant + test both carry the same value. The "confirm subdir" and "remove unused imports per ruff" notes are real integration checks with the resolution stated. ✓

**Type consistency:** `extract_context(body) -> str | None`; `_DistilledNeighborIndex(*, vault_root, subdir, embedder)` with `nearest(text)`, `add(permalink, text, timestamp, path=None)`, `note_superseded(permalink)` — matches the `NeighborIndex` protocol the pipeline calls; `Neighbor(permalink, text, timestamp, path)` matches the 0.10.0 dataclass; `_CONSOLIDATE_GATE` used in both index + eval. Consistent. ✓
