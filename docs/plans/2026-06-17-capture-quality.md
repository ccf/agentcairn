# Capture-Quality Backlog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three capture-quality fixes — title-derived slugs, semantic neighbors in `build_context`, and honest compaction counting in the ingest report.

**Architecture:** Part 1 reorders two lines in the extractive distiller. Part 2 adds a reusable `semantic_neighbors()` to the search engine that reuses each note's **already-indexed chunk vectors** (no embedder/recompute) and wires it into `build_context_tool`. Part 3 corrects the ingest report's compaction arithmetic.

**Tech Stack:** Python 3.12, DuckDB (`array_cosine_similarity`), Typer CLI, pytest. Spec: `docs/specs/2026-06-17-capture-quality-design.md`.

**Design refinement vs spec (intentional):** the spec described `semantic_neighbors(con, permalink, embedder, …)` re-embedding the note text and threading an embedder into `build_context`. This plan instead reuses the note's stored `chunk_embeddings.vec` rows, so the signature is `semantic_neighbors(con, permalink, *, k=5, min_score=0.0)` with **no embedder** — no recompute, no embedder dependency, and `build_context_tool`'s signature is unchanged (no `server.py` change). Observable behavior (a `related` neighbor list) is identical.

---

## File Structure

- **Modify** `src/cairn/ingest/distill.py` — Part 1: slug off the title (user-memory branch).
- **Modify** `src/cairn/search/engine.py` — Part 2: add `semantic_neighbors()`.
- **Modify** `src/cairn/mcp/tools.py` — Part 2: add `related` to `build_context_tool`.
- **Modify** `src/cairn/cli.py` — Part 3: ingest report headline + skip breakdown.
- **Tests:** `tests/ingest/test_distill.py`, `tests/search/test_engine.py` (or existing search test file), `tests/mcp/test_tools.py` (or existing), `tests/test_cli.py`.
- **Modify** `CHANGELOG.md` — Part 4.

---

## Task 1: Title-derived slugs

**Files:**
- Modify: `src/cairn/ingest/distill.py` (user-memory branch, ~lines 84-87)
- Test: `tests/ingest/test_distill.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/ingest/test_distill.py — add
def test_slug_derives_from_judge_title_not_verbatim():
    from cairn.ingest.distill import ExtractiveDistiller
    from cairn.ingest.models import Candidate, Judgment

    cand = Candidate(
        text="Is Node 22 being deprecated?",  # trivial trigger turn
        session_id="s1",
        timestamp="2026-06-17T00:00:00Z",
        judgment=Judgment(keep=True, importance=0.8,
                          title="agentcairn release ritual: CHANGELOG + GitHub Release at every tag",
                          distilled="The release ritual is bump version, promote CHANGELOG, tag, GH release."),
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.permalink.startswith("agentcairn-release-ritual")  # slug from the title
    assert "node-22" not in note.permalink


def test_slug_falls_back_to_verbatim_without_judge_title():
    from cairn.ingest.distill import ExtractiveDistiller
    from cairn.ingest.models import Candidate

    cand = Candidate(text="We decided to always rebase-merge the branch",
                     session_id="s1", timestamp="2026-06-17T00:00:00Z")
    note = ExtractiveDistiller().distill(cand)  # no judgment → title is the verbatim
    assert note.permalink.startswith("we-decided-to-always")
```

(Check `Candidate`/`Judgment` field names first: `grep -n "class Candidate\|class Judgment" -A12 src/cairn/ingest/models.py`. Adjust the constructor kwargs to the real fields — `judgment` carries `title`/`distilled`; importance may live on the candidate. Keep the two assertions.)

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_distill.py -q -k slug`
Expected: FAIL (first test: permalink starts with `is-node-22`).

- [ ] **Step 3: Implement** — in `src/cairn/ingest/distill.py`, the user-memory branch currently reads:

```python
        slug = f"{_slugify(candidate.text)}-{h[:8]}"
        j = candidate.judgment
        title = (j.title if j and j.title else None) or _truncate_title(candidate.text)
```

Reorder so the title is computed first and the slug derives from it:

```python
        j = candidate.judgment
        title = (j.title if j and j.title else None) or _truncate_title(candidate.text)
        slug = f"{_slugify(title)}-{h[:8]}"
```

(Leave the `session-summary` branch untouched — its slug is already title/identity-based.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_distill.py -q`
Expected: PASS (both new tests + existing distill tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/distill.py tests/ingest/test_distill.py
git commit -m "feat(distill): derive slug from the distilled title, not the trigger turn"
```

---

## Task 2: `semantic_neighbors()` in the search engine

**Files:**
- Modify: `src/cairn/search/engine.py`
- Test: `tests/search/test_engine.py` (create if absent; else add to the existing engine test file — `grep -rln "from cairn.search" tests/ | head`)

- [ ] **Step 1: Write the failing test**

```python
# tests/search/test_engine.py — add (create file with SPDX header if new)
def _build_index(tmp_path, notes):
    """notes: list of (permalink, body). Reindex with the fake embedder."""
    from typer.testing import CliRunner
    from cairn.cli import app

    v = tmp_path / "vault"
    v.mkdir()
    for permalink, body in notes:
        (v / f"{permalink}.md").write_text(
            f"---\ntitle: {permalink}\npermalink: {permalink}\n---\n{body}\n"
        )
    idx = tmp_path / "i.duckdb"
    r = CliRunner().invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    return str(idx)


def test_semantic_neighbors_excludes_self_and_returns_related(tmp_path):
    from cairn.search.engine import open_search, semantic_neighbors

    idx = _build_index(tmp_path, [
        ("ram", "scale the RAM to 4 gigabytes for the build"),
        ("ram2", "increase memory RAM to 8 gigabytes"),
        ("coffee", "pour over coffee brewing method beans"),
    ])
    con = open_search(idx)
    try:
        rel = semantic_neighbors(con, "ram", k=5)
    finally:
        con.close()
    perms = [r["permalink"] for r in rel]
    assert "ram" not in perms  # excludes self
    assert "ram2" in perms  # a semantically-near note is returned
    assert all("score" in r and "title" in r for r in rel)


def test_semantic_neighbors_excludes_superseded(tmp_path):
    from cairn.search.engine import open_search, semantic_neighbors

    v = tmp_path / "vault"; v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha topic widget\n")
    (v / "b.md").write_text(
        "---\ntitle: B\npermalink: b\nsuperseded_by: a\n---\nalpha topic widget\n"
    )
    from typer.testing import CliRunner
    from cairn.cli import app
    idx = tmp_path / "i.duckdb"
    assert CliRunner().invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"]).exit_code == 0
    con = open_search(str(idx))
    try:
        rel = semantic_neighbors(con, "a", k=5)
    finally:
        con.close()
    assert "b" not in [r["permalink"] for r in rel]  # superseded note never surfaced


def test_semantic_neighbors_missing_note_returns_empty(tmp_path):
    from cairn.search.engine import open_search, semantic_neighbors

    idx = _build_index(tmp_path, [("a", "alpha body")])
    con = open_search(idx)
    try:
        assert semantic_neighbors(con, "does-not-exist", k=5) == []
    finally:
        con.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/search/test_engine.py -q -k semantic`
Expected: FAIL (`cannot import name 'semantic_neighbors'`).

- [ ] **Step 3: Implement** — add to `src/cairn/search/engine.py` (it already has `open_search`, `vector_search`, `_dim`):

```python
def semantic_neighbors(
    con: duckdb.DuckDBPyConnection, permalink: str, *, k: int = 5, min_score: float = 0.0
) -> list[dict]:
    """Top-`k` semantically-nearest live notes to `permalink`, by cosine over the
    note's own indexed chunk vectors (no re-embedding). Excludes the note itself and
    superseded notes. Best-effort: any failure (no vectors, missing note, empty index)
    → []. Reusable core for build_context's `related` and a future `cairn link`."""
    try:
        dim = _dim(con)
        if dim <= 0:
            return []
        vecs = [
            r[0]
            for r in con.execute(
                "SELECT ce.vec FROM chunk_embeddings ce "
                "JOIN chunks c ON ce.chunk_id = c.chunk_id "
                "WHERE c.note_permalink = ?",
                [permalink],
            ).fetchall()
        ]
        if not vecs:
            return []
        n = len(vecs)
        centroid = [sum(col) / n for col in zip(*vecs)]  # note-level mean vector
        rows = con.execute(
            f"SELECT n.permalink, n.title, "
            f"max(array_cosine_similarity(ce.vec, ?::FLOAT[{dim}])) AS sim "
            f"FROM chunk_embeddings ce "
            f"JOIN chunks c ON ce.chunk_id = c.chunk_id "
            f"JOIN notes n ON c.note_permalink = n.permalink "
            f"WHERE n.permalink != ? AND n.superseded_by IS NULL "
            f"GROUP BY n.permalink, n.title ORDER BY sim DESC LIMIT ?",
            [centroid, permalink, k],
        ).fetchall()
        return [
            {"permalink": r[0], "title": r[1], "score": round(float(r[2]), 4)}
            for r in rows
            if float(r[2]) >= min_score
        ]
    except Exception:
        return []
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/search/test_engine.py -q -k semantic`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/search/engine.py tests/search/test_engine.py
git commit -m "feat(search): semantic_neighbors() over indexed chunk vectors"
```

---

## Task 3: Wire `related` into `build_context`

**Files:**
- Modify: `src/cairn/mcp/tools.py` (`build_context_tool`, ~lines 196-250)
- Test: `tests/mcp/test_tools.py` (create if absent; else the existing tools test file)

- [ ] **Step 1: Write the failing test**

```python
# tests/mcp/test_tools.py — add (SPDX header if new file)
def test_build_context_includes_semantic_related(tmp_path):
    from typer.testing import CliRunner
    from cairn.cli import app
    from cairn.mcp.tools import build_context_tool

    v = tmp_path / "vault"; v.mkdir()
    (v / "ram.md").write_text("---\ntitle: RAM\npermalink: ram\n---\nscale RAM to 4 gigabytes\n")
    (v / "ram2.md").write_text("---\ntitle: RAM2\npermalink: ram2\n---\nincrease memory RAM 8 gigabytes\n")
    idx = tmp_path / "i.duckdb"
    assert CliRunner().invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"]).exit_code == 0

    ctx = build_context_tool(str(idx), "ram")
    assert ctx["root"]["permalink"] == "ram"
    assert "related" in ctx
    assert "ram2" in [r["permalink"] for r in ctx["related"]]  # semantic neighbor surfaced
    assert ctx["outgoing"] == [] and ctx["incoming"] == []  # no user wikilinks → still empty


def test_build_context_missing_note_has_related_key(tmp_path):
    from typer.testing import CliRunner
    from cairn.cli import app
    from cairn.mcp.tools import build_context_tool

    v = tmp_path / "vault"; v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha\n")
    idx = tmp_path / "i.duckdb"
    assert CliRunner().invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"]).exit_code == 0
    ctx = build_context_tool(str(idx), "nope")
    assert ctx == {"root": None, "outgoing": [], "incoming": [], "related": []}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/mcp/test_tools.py -q -k related`
Expected: FAIL (`KeyError: 'related'` / missing key).

- [ ] **Step 3: Implement** — in `src/cairn/mcp/tools.py`:

Add the import at the top alongside the existing search imports (`grep -n "from cairn.search" src/cairn/mcp/tools.py`):
```python
from cairn.search.engine import semantic_neighbors
```

Update the early-return for a missing root (currently `return {"root": None, "outgoing": [], "incoming": []}`) to include `related`:
```python
        if root is None:
            return {"root": None, "outgoing": [], "incoming": [], "related": []}
```

Compute `related` before closing the connection, and add it to the final return. Change the end of the `try` / the final `return` from:
```python
        incoming = [{"permalink": r[0]} for r in in_rows if r[0] != permalink]
    finally:
        con.close()
    return {"root": root, "outgoing": outgoing, "incoming": incoming}
```
to:
```python
        incoming = [{"permalink": r[0]} for r in in_rows if r[0] != permalink]
        related = semantic_neighbors(con, permalink, k=5)
    finally:
        con.close()
    return {"root": root, "outgoing": outgoing, "incoming": incoming, "related": related}
```

(No `server.py` change: `semantic_neighbors` needs no embedder, and `build_context_tool`'s signature is unchanged.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/mcp/test_tools.py -q -k related`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/mcp/tools.py tests/mcp/test_tools.py
git commit -m "feat(build_context): add semantic `related` neighbors"
```

---

## Task 4: Honest compaction counting in the ingest report

**Files:**
- Modify: `src/cairn/cli.py` (`ingest` report rendering, ~lines 843-851)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — add
def test_ingest_report_reconciles_compaction_counts(tmp_path):
    """2 compaction-summary events in one session → 1 promoted (latest). Headline shows
    `1 summaries`; the skipped line shows `1 compact_summary` (2 − 1), not 2."""
    import json as _j

    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    def rec(text, ts, compact=False):
        d = {"type": "user", "sessionId": "s", "cwd": "/Users/x/proj",
             "timestamp": ts, "message": {"role": "user", "content": text}}
        if compact:
            d["isCompactSummary"] = True
        return _j.dumps(d)
    (proj / "t.jsonl").write_text("\n".join([
        rec("We decided to always rebase-merge the branch", "2026-06-17T00:00:00Z"),
        rec("first compaction summary text", "2026-06-17T01:00:00Z", compact=True),
        rec("second (latest) compaction summary text", "2026-06-17T02:00:00Z", compact=True),
    ]) + "\n")
    vault = tmp_path / "vault"
    r = runner.invoke(app, ["ingest", "--vault", str(vault), "--transcripts-dir",
                            str(tmp_path / "projects"), "--harness", "claude-code",
                            "--ledger", str(tmp_path / "led.sha256"),
                            "--index", str(tmp_path / "i.duckdb")],
                      env={"CAIRN_JUDGE": "none"})
    assert r.exit_code == 0, r.output
    assert "1 summaries" in r.output  # the promoted compaction is surfaced
    assert "1 compact_summary" in r.output  # 2 events − 1 promoted
    assert "2 compact_summary" not in r.output  # the old miscount is gone
```

(If the claude-code adapter keys compaction differently than `isCompactSummary`, confirm with
`grep -rn "isCompactSummary\|COMPACT_SUMMARY" src/cairn/ingest/harness/claude_code.py` and match
the seeded record to what `classify_claude_code` recognizes.)

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py::test_ingest_report_reconciles_compaction_counts -q`
Expected: FAIL (`2 compact_summary` present; `1 summaries` absent).

- [ ] **Step 3: Implement** — in `src/cairn/cli.py`, the `ingest` report currently renders:

```python
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

Change to surface summaries in the headline and subtract promoted summaries from the
`compact_summary` skip tally:

```python
    summaries_part = f"{rep.summaries} summaries · " if rep.summaries else ""
    typer.echo(
        f"{prefix}{rep.authored} authored · {summaries_part}{rep.candidates} candidates · "
        f"{rep.redactions} redactions · {rep.deduped} deduped · "
        f"{rep.gated_out} gated · {len(rep.written)} written · judge: {rep.judge_tier}"
        + (f" ({rep.judge_degraded} degraded)" if rep.judge_degraded else "")
    )
    skipped = {k: v for k, v in rep.event_kinds.items() if k != "authored_user"}
    # `compact_summary` events that were promoted to session-summary notes aren't skips.
    if "compact_summary" in skipped:
        remaining = skipped["compact_summary"] - rep.summaries
        if remaining > 0:
            skipped["compact_summary"] = remaining
        else:
            del skipped["compact_summary"]
    if skipped:
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]))
        typer.echo(f"  skipped (non-authored): {breakdown}")
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k "compaction or ingest"`
Expected: PASS (new test + existing ingest tests; update any existing test that asserted the old
`N compact_summary` count if one exists — `grep -n "compact_summary" tests/test_cli.py`).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(ingest): reconcile compaction counts (summaries headline + skip subtraction)"
```

---

## Task 5: Docs + full verify

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: CHANGELOG** — add under `## [Unreleased]` (create the section if missing):

```markdown
### Changed
- Memory **permalinks/slugs derive from the distilled title** instead of the (often trivial)
  trigger turn — readable filenames in the vault (existing notes unchanged).
- `cairn ingest` now reports promoted compaction `summaries` in the headline and no longer
  double-counts them under "skipped".

### Added
- `build_context` returns a `related` list of semantic-neighbor notes (cosine over indexed
  vectors), so it's useful even for notes without `[[wikilinks]]`. User-authored wikilinks
  still populate `outgoing`/`incoming`.
```

- [ ] **Step 2: Full verify** — run and confirm:

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q`
Expected: all green (3 pre-existing skips OK).
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.
Run: `uv run --no-project --with pytest pytest plugin/tests/ -q`
Expected: green (this suite is outside the repo-root testpaths — run it explicitly).

- [ ] **Step 3: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: readable slugs, build_context related neighbors, honest compaction report"
```

---

## Self-Review Notes (author)

- **Spec coverage:** Part 1 → Task 1; Part 2 (reusable core) → Task 2; Part 2 (build_context wiring)
  → Task 3; Part 3 → Task 4; rollout/CHANGELOG → Task 5. The deferred `cairn link` follow-up is
  intentionally NOT a task (next cycle) — Task 2's `semantic_neighbors` is its reusable core.
- **Intentional refinement:** `semantic_neighbors` uses stored chunk vectors (no embedder) rather
  than the spec's re-embed-the-text; flagged in the header. `build_context_tool` signature unchanged
  → no `server.py` change.
- **Naming consistency:** `semantic_neighbors(con, permalink, *, k=5, min_score=0.0)` returns
  `[{"permalink","title","score"}]`; `build_context` adds key `related`; report uses `rep.summaries`
  + `rep.event_kinds` (verified present in cli.py / models.py).
- **No-placeholder check:** every code step shows complete code; the two "confirm field names / adapter
  key" notes point at exact grep commands, with the surrounding assertions fixed.
- **plugin/tests/** reminder included in verify (the CI gotcha from the prior cycle).
