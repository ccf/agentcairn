# Provenance-Aware Recall Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stamp each memory note with its origin (`project`, `harness`), index it, and at recall time softly boost current-project memories (non-lossy) with an optional hard project scope.

**Architecture:** Persist `project`/`harness` to note frontmatter at distill time → add nullable `project`/`harness` columns to the DuckDB `notes` table (additive migration) populated by reconcile → resolve a "current project" (explicit arg, else process cwd) → apply a ×1.4 multiplier (alongside the existing graph ×1.2 / validity ×0.5) and an optional `WHERE project = ?` in the ranking SQL → surface `project` on each result and mark cross-project hits.

**Tech Stack:** Python 3.11+, DuckDB (FTS + array cosine, RRF fusion), Typer CLI, FastMCP tools, pytest, uv (`uv run pytest` / `uv run ruff`).

**Reference:** Spec `docs/specs/2026-06-15-provenance-aware-recall-design.md`. Branch `feat/provenance-aware-recall` has the spec committed.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cairn/ingest/distill.py` | persist `project`/`harness` to frontmatter when present on the Candidate |
| `src/cairn/index/schema.py` | add nullable `project`/`harness` columns + additive `ALTER TABLE` migration |
| `src/cairn/index/build.py` | read `project`/`harness` from frontmatter into the `notes` row on (re)index |
| `src/cairn/search/engine.py` | `resolve_current_project`; surface `project` on rows/`Hit`; ×1.4 boost + optional `scope` filter; thread through `search()` |
| `src/cairn/mcp/tools.py` | `search_tool`/`recall_tool` gain `project`/`scope`; add `project` + `cross_project` to output |
| `src/cairn/cli.py` | `recall` command gains `--project`/`--scope`; render `[from: <project>]` for cross-project hits |
| `plugin/skills/using-agentcairn-memory/SKILL.md` + `src/cairn/assets/using-agentcairn-memory/SKILL.md` | one line on current-project preference (kept byte-identical) |
| `README.md`, `CLAUDE.md` | document provenance-aware recall |
| `tests/ingest/test_distill.py`, `tests/index/test_schema.py`, `tests/index/test_build*.py`, `tests/search/test_search.py`, `tests/mcp/`, `tests/test_cli.py` | per-task tests |

**Candidate fields available** (`src/cairn/ingest/models.py`): `candidate.project: str | None`, `candidate.harness` (confirm the attribute name in Task 1 — see note). **`project_from_cwd`** lives at `src/cairn/ingest/events.py:43` and returns the final path segment of a cwd, or `None`.

---

## Task 1: Persist `project` + `harness` to frontmatter

**Files:**
- Modify: `src/cairn/ingest/distill.py` (the `ExtractiveDistiller.distill` frontmatter dict, lines 58-66)
- Test: `tests/ingest/test_distill.py`

First confirm the Candidate attribute names:

```bash
cd /Users/ccf/git/agentcairn && sed -n '20,45p' src/cairn/ingest/models.py
```
Expected: a `@dataclass` `Candidate` with `project: str | None` and a harness field. **If the harness attribute is named differently (e.g. `harness` vs `source_harness`), use the real name in the code below.** The steps assume `candidate.project` and `candidate.harness`.

- [ ] **Step 1: Write the failing test**

Add to `tests/ingest/test_distill.py` (look at the existing `test_distiller_builds_non_lossy_note_with_backlink` for how a `Candidate` is constructed — reuse that construction, adding `project=` and `harness=`):

```python
def test_distiller_persists_project_and_harness_when_present():
    from cairn.ingest.distill import ExtractiveDistiller
    from cairn.ingest.models import Candidate

    cand = Candidate(
        text="We rotate the signing key on deploy.",
        session_id="sess-1",
        timestamp="2026-06-15T00:00:00Z",
        project="agentcairn",
        harness="claude-code",
        git_branch=None,
        source_path=__import__("pathlib").Path("/x/rollout.jsonl"),
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.frontmatter["project"] == "agentcairn"
    assert note.frontmatter["harness"] == "claude-code"


def test_distiller_omits_origin_keys_when_absent():
    from cairn.ingest.distill import ExtractiveDistiller
    from cairn.ingest.models import Candidate

    cand = Candidate(
        text="A memory with no project.",
        session_id="sess-2",
        timestamp="2026-06-15T00:00:00Z",
        project=None,
        harness=None,
        git_branch=None,
        source_path=__import__("pathlib").Path("/x/rollout.jsonl"),
    )
    note = ExtractiveDistiller().distill(cand)
    assert "project" not in note.frontmatter
    assert "harness" not in note.frontmatter
```

**If `Candidate(...)` requires other mandatory fields**, copy the exact constructor call from the existing test and only add/override `project`/`harness`.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_distill.py -k "persists_project or omits_origin" -v`
Expected: FAIL — `KeyError: 'project'` (and the key is absent today).

- [ ] **Step 3: Implement — add the keys when present**

In `src/cairn/ingest/distill.py`, after the `frontmatter = {...}` dict literal (after line 66) and before `verbatim = ...`:

```python
        if candidate.project:
            frontmatter["project"] = candidate.project
        if candidate.harness:
            frontmatter["harness"] = candidate.harness
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/ingest/test_distill.py -v`
Expected: PASS (new + existing distill tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/distill.py tests/ingest/test_distill.py
git commit -m "feat(provenance): persist project + harness to note frontmatter"
```

---

## Task 2: Index `project`/`harness` columns + migration + reconcile populate

**Files:**
- Modify: `src/cairn/index/schema.py` (the `notes` CREATE TABLE + ALTER migrations, lines 15-26)
- Modify: `src/cairn/index/build.py` (the `INSERT INTO notes` in `index_note`, lines 82-98)
- Test: `tests/index/test_schema.py`, and a build/reconcile test (find the existing one: `grep -rln "index_note\|reconcile" tests/index/`)

- [ ] **Step 1: Write the failing schema/migration test**

Add to `tests/index/test_schema.py` (reuse the pattern of `test_open_index_migrates_old_6col_notes_table`):

```python
def test_open_index_has_project_and_harness_columns(tmp_path):
    from cairn.index.schema import open_index

    con = open_index(str(tmp_path / "i.duckdb"), dim=8, model_id="m")
    cols = {r[0] for r in con.execute("PRAGMA table_info('notes')").fetchall()}
    assert "project" in cols and "harness" in cols


def test_open_index_migrates_old_notes_table_adds_origin_columns(tmp_path):
    import duckdb

    from cairn.index.schema import open_index

    p = str(tmp_path / "old.duckdb")
    con = duckdb.connect(p)
    # Pre-provenance notes table (no project/harness columns).
    con.execute(
        "CREATE TABLE notes (permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR,"
        " type VARCHAR, content_hash VARCHAR, mtime DOUBLE,"
        " valid_from TIMESTAMP, valid_until TIMESTAMP, superseded_by VARCHAR)"
    )
    con.execute("INSERT INTO notes (permalink) VALUES ('n1')")
    con.close()
    con = open_index(p, dim=8, model_id="m")
    cols = {r[0] for r in con.execute("PRAGMA table_info('notes')").fetchall()}
    assert "project" in cols and "harness" in cols
    assert con.execute("SELECT project FROM notes WHERE permalink='n1'").fetchone()[0] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/index/test_schema.py -k "project_and_harness or origin_columns" -v`
Expected: FAIL — columns absent.

- [ ] **Step 3: Implement schema + migration**

In `src/cairn/index/schema.py`, extend the `notes` CREATE TABLE column list and add two ALTERs next to the existing validity ALTERs (lines 24-26):

Change the CREATE TABLE (lines 16-20) to include the two columns:
```python
        "CREATE TABLE IF NOT EXISTS notes ("
        "  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR, type VARCHAR,"
        "  content_hash VARCHAR, mtime DOUBLE,"
        "  valid_from TIMESTAMP, valid_until TIMESTAMP, superseded_by VARCHAR,"
        "  project VARCHAR, harness VARCHAR)"
```
And after line 26 (`... superseded_by VARCHAR`):
```python
    con.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS project VARCHAR")
    con.execute("ALTER TABLE notes ADD COLUMN IF NOT EXISTS harness VARCHAR")
```

- [ ] **Step 4: Run schema tests to verify pass**

Run: `uv run pytest tests/index/test_schema.py -v`
Expected: PASS.

- [ ] **Step 5: Write the failing reconcile/populate test**

Find the existing single-note index test (`grep -rln "index_note" tests/index/`); add a test in that file that writes a note with `project`/`harness` frontmatter, indexes it, and asserts the columns are populated. Use the project's standard embedder fixture for tests — check how sibling tests obtain an embedder (often a `FakeEmbedder` or `get_embedder("fake")`); reuse that exact construction. Template:

```python
def test_index_note_populates_project_and_harness(tmp_path):
    from cairn.embed import get_embedder
    from cairn.index.build import index_note
    from cairn.index.schema import open_index

    note_path = tmp_path / "n.md"
    note_path.write_text(
        "---\n"
        "title: T\n"
        "type: memory\n"
        "permalink: n\n"
        "project: agentcairn\n"
        "harness: codex\n"
        "---\n"
        "- [context] a fact #ingested\n",
        encoding="utf-8",
    )
    emb = get_embedder("fake")
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    index_note(con, note_path, emb, vault_dir=str(tmp_path))
    row = con.execute("SELECT project, harness FROM notes WHERE permalink='n'").fetchone()
    assert row == ("agentcairn", "codex")
```

If `get_embedder("fake")` is not the right test embedder, mirror whatever `tests/index/` already uses.

- [ ] **Step 6: Run to verify it fails**

Run: `uv run pytest tests/index/ -k "populates_project" -v`
Expected: FAIL — `INSERT INTO notes` doesn't write the columns (they'd be `NULL`).

- [ ] **Step 7: Implement reconcile populate**

In `src/cairn/index/build.py`, update the `INSERT INTO notes` in `index_note` (lines 82-98) to include the two columns and values:

```python
    con.execute(
        "INSERT INTO notes "
        "(permalink, path, title, type, content_hash, mtime, "
        " valid_from, valid_until, superseded_by, project, harness) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            permalink,
            str(path),
            str(fm.get("title") or ""),
            str(fm.get("type") or ""),
            _content_hash(text),
            path.stat().st_mtime,
            to_db(_safe_temporal(fm.get("valid_from"))),
            to_db(_safe_temporal(fm.get("valid_until"))),
            (fm.get("superseded_by") or None),
            (fm.get("project") or None),
            (fm.get("harness") or None),
        ],
    )
```

- [ ] **Step 8: Run to verify it passes + full index suite**

Run: `uv run pytest tests/index/ -v`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/cairn/index/schema.py src/cairn/index/build.py tests/index/
git commit -m "feat(provenance): index project/harness columns with additive migration"
```

---

## Task 3: `resolve_current_project` helper

**Files:**
- Modify: `src/cairn/search/engine.py` (add the helper + an `import os` and the `project_from_cwd` import)
- Test: `tests/search/test_search.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/search/test_search.py`:

```python
def test_resolve_current_project_explicit_wins(monkeypatch):
    from cairn.search.engine import resolve_current_project

    monkeypatch.setattr("os.getcwd", lambda: "/Users/x/git/otherrepo")
    assert resolve_current_project("agentcairn") == "agentcairn"


def test_resolve_current_project_falls_back_to_cwd(monkeypatch):
    from cairn.search.engine import resolve_current_project

    monkeypatch.setattr("os.getcwd", lambda: "/Users/x/git/agentcairn")
    assert resolve_current_project(None) == "agentcairn"


def test_resolve_current_project_none_for_root(monkeypatch):
    from cairn.search.engine import resolve_current_project

    monkeypatch.setattr("os.getcwd", lambda: "/")
    assert resolve_current_project(None) is None


def test_resolve_current_project_none_when_getcwd_raises(monkeypatch):
    from cairn.search.engine import resolve_current_project

    def boom():
        raise OSError("cwd deleted")

    monkeypatch.setattr("os.getcwd", boom)
    assert resolve_current_project(None) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/search/test_search.py -k resolve_current_project -v`
Expected: FAIL — `ImportError` / `AttributeError` (function not defined).

- [ ] **Step 3: Implement the helper**

In `src/cairn/search/engine.py`, add `import os` to the imports, and add `from cairn.ingest.events import project_from_cwd` (verify no import cycle by running the test in Step 4; `events.py` imports only stdlib + dataclasses, so this is safe). Then add:

```python
def resolve_current_project(explicit: str | None) -> str | None:
    """Current project for provenance-aware recall: an explicit arg wins, else the
    process cwd's repo name (final path segment), else None (no boost)."""
    if explicit:
        return explicit
    try:
        return project_from_cwd(os.getcwd())
    except OSError:
        return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/search/test_search.py -k resolve_current_project -v`
Expected: PASS.

- [ ] **Step 5: Re-export from the package**

The MCP tools and CLI (Task 6) import `from cairn.search import resolve_current_project`, so add it to `src/cairn/search/__init__.py`: include `resolve_current_project` in the `from cairn.search.engine import (...)` list AND in `__all__` (keep both alphabetized as they currently are — insert after `rerank_candidates`/before `search` in `__all__`, and in the engine-import block between `open_search` and `search`).

Verify: `uv run python -c "from cairn.search import resolve_current_project; print('ok')"` → `ok`.

- [ ] **Step 6: Commit**

```bash
git add src/cairn/search/engine.py src/cairn/search/__init__.py tests/search/test_search.py
git commit -m "feat(provenance): resolve_current_project (explicit arg, else cwd)"
```

---

## Task 4: Surface `project` through search results (no ranking change yet)

**Files:**
- Modify: `src/cairn/search/engine.py` — `_hybrid_sql`/`_bm25_only_sql` SELECT `n.project`; `Hit.project`; dict rows in `hybrid_search`/`bm25_only`; rerank row passthrough; `Hit` construction in `search`; `get_note` returns `project`.
- Test: `tests/search/test_search.py`

This task makes every result carry its `project` so later tasks (boost, annotation) and `get_note` consumers can read it. No score change.

- [ ] **Step 1: Write the failing test**

Add to `tests/search/test_search.py` (find an existing helper that builds an index with notes — e.g. a fixture or a `_build_index` helper; reuse it, writing one note with `project: agentcairn` frontmatter). Template assuming a helper `make_index(tmp_path, notes)` exists; **if not, copy the index-construction from the nearest existing search test**:

```python
def test_hit_carries_project(tmp_path):
    from cairn.search import open_search, search
    from cairn.embed import get_embedder
    # ... build an index containing a note with frontmatter project: agentcairn,
    # body mentioning "signing key", using the same construction as neighboring tests ...
    con = open_search(str(index_path))
    try:
        hits = search(con, "signing key", embedder=get_embedder("fake"), k=5)
    finally:
        con.close()
    assert hits, "expected at least one hit"
    assert any(h.project == "agentcairn" for h in hits)
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/search/test_search.py -k hit_carries_project -v`
Expected: FAIL — `AttributeError: 'Hit' object has no attribute 'project'`.

- [ ] **Step 3: Add `project` to the `Hit` dataclass**

In `src/cairn/search/engine.py`, add to `Hit` (after `superseded_by`):
```python
    project: str | None = None
```

- [ ] **Step 4: SELECT `n.project` in both SQL builders**

In `_hybrid_sql`, change the final SELECT (line 125-127) to add `n.project`:
```python
        SELECT f.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               n.valid_from, n.valid_until, n.superseded_by, n.project,
               f.rrf_score{boost}{validity} AS score
```
In `_bm25_only_sql`, change the SELECT (line 158-160) to add `n.project`:
```python
        SELECT c.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               n.valid_from, n.valid_until, n.superseded_by, n.project,
               rrf(f.r){boost}{validity} AS score
```
**Note the column index shift:** `project` is now row index 7 and `score` is row index 8 (was 7). Update the row-dict builders accordingly in the next step.

- [ ] **Step 5: Map `project` into the dict rows (both `hybrid_search` and `bm25_only`)**

In `bm25_only` (lines 188-200) and `hybrid_search` (lines 227-239), the row dict comprehension must read the new column order. Change `"score": float(r[7])` → add `"project": r[7]` and `"score": float(r[8])`:
```python
            "superseded_by": r[6],
            "project": r[7],
            "score": float(r[8]),
```

- [ ] **Step 6: Preserve `project` through the rerank passthrough and the final `Hit` build in `search`**

In `search` (engine.py), the post-rerank `rows = [ {...} for c in ranked ]` block (lines 323-335) must carry `project`:
```python
                "superseded_by": c.get("superseded_by"),
                "project": c.get("project"),
                "score": c["rerank_score"],
```
And the final `Hit(...)` construction (lines 338-349) gains:
```python
            superseded_by=r.get("superseded_by"),
            project=r.get("project"),
```
(The reranker passes through unknown keys via `{**r, "text": ...}`, so `project` survives into `ranked` automatically.)

- [ ] **Step 7: Add `project` to `get_note`**

In `get_note` (lines 375-396), add `project` to the SELECT and the returned dict:
```python
    row = con.execute(
        "SELECT permalink, path, title, type, valid_from, valid_until, superseded_by, project "
        "FROM notes WHERE permalink = ?",
        [permalink],
    ).fetchone()
```
and in the returned dict add `"project": row[7],` (after `superseded_by`).

- [ ] **Step 8: Run to verify pass + full search suite**

Run: `uv run pytest tests/search/ -v`
Expected: PASS (the new test + all existing search tests, which must be unaffected since scores are unchanged).

- [ ] **Step 9: Commit**

```bash
git add src/cairn/search/engine.py tests/search/test_search.py
git commit -m "feat(provenance): surface note project on search hits and get_note"
```

---

## Task 5: Project boost (×1.4) + optional hard `scope` filter

**Files:**
- Modify: `src/cairn/search/engine.py` — `_hybrid_sql`/`_bm25_only_sql` gain a project-boost clause + optional scope WHERE; `hybrid_search`/`bm25_only` thread `current`/`scope` and append params in the exact positional order; `search()` gains `project`/`scope` params and resolves `current`.
- Test: `tests/search/test_search.py`

**Bind-param discipline:** DuckDB binds positionally by *appearance order in the SQL string*. The project-boost `?` appears in the SELECT list **after** the validity `?, ?`; the scope-filter `?` appears in the WHERE, **after** the SELECT and **before** the trailing `LIMIT ?`. Build the param list in that exact order.

- [ ] **Step 1: Write the failing ranking tests**

Add to `tests/search/test_search.py` (reuse the index-construction helper from Task 4; build an index with two notes that both match the query equally — one `project: agentcairn`, one `project: otherrepo`):

```python
def test_same_project_boosted_above_cross_project(tmp_path, monkeypatch):
    from cairn.search import open_search, search
    from cairn.embed import get_embedder
    # build index: note A (project agentcairn) and note B (project otherrepo),
    # both bodies equally matching "deploy key rotation"
    con = open_search(str(index_path))
    try:
        hits = search(con, "deploy key rotation", embedder=get_embedder("fake"),
                      k=5, project="agentcairn")
    finally:
        con.close()
    perms = [h.permalink for h in hits]
    assert perms, "expected hits"
    # both still present (non-lossy) ...
    assert any(h.project == "otherrepo" for h in hits)
    # ... and the agentcairn note ranks ahead of the otherrepo note
    a = next(i for i, h in enumerate(hits) if h.project == "agentcairn")
    b = next(i for i, h in enumerate(hits) if h.project == "otherrepo")
    assert a < b


def test_scope_project_filters_out_cross_project(tmp_path):
    from cairn.search import open_search, search
    from cairn.embed import get_embedder
    con = open_search(str(index_path))
    try:
        hits = search(con, "deploy key rotation", embedder=get_embedder("fake"),
                      k=5, project="agentcairn", scope="project")
    finally:
        con.close()
    assert hits
    assert all(h.project == "agentcairn" for h in hits)


def test_no_project_is_noop(tmp_path):
    # project=None and cwd unresolved → ordering unaffected, all notes present
    from cairn.search import open_search, search
    from cairn.embed import get_embedder
    con = open_search(str(index_path))
    try:
        hits = search(con, "deploy key rotation", embedder=get_embedder("fake"),
                      k=5, project=None, scope="project")  # scope degrades to all
    finally:
        con.close()
    projs = {h.project for h in hits}
    assert "agentcairn" in projs and "otherrepo" in projs
```

For `test_no_project_is_noop` to exercise the `current is None` path deterministically, `monkeypatch.setattr("os.getcwd", lambda: "/")` so the cwd fallback yields `None`.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/search/test_search.py -k "boosted_above or scope_project or no_project_is_noop" -v`
Expected: FAIL — `search()` has no `project`/`scope` kwargs (TypeError).

- [ ] **Step 3: Add project-boost + scope clauses to `_hybrid_sql`**

Change `_hybrid_sql(dim, graph_boost=True, validity_aware=True)` to `_hybrid_sql(dim, graph_boost=True, validity_aware=True, project_boost=False, scope_project=False)`. Add, near the existing `boost`/`validity` clause builders:
```python
    proj_boost = " * (CASE WHEN n.project = ? THEN 1.4 ELSE 1.0 END)" if project_boost else ""
    scope = " WHERE n.project = ?" if scope_project else ""
```
Then change the final SELECT's score expression and add the WHERE before `ORDER BY`:
```python
        SELECT f.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               n.valid_from, n.valid_until, n.superseded_by, n.project,
               f.rrf_score{boost}{validity}{proj_boost} AS score
        FROM fused f JOIN chunks c ON c.chunk_id = f.chunk_id
        JOIN notes n ON n.permalink = c.note_permalink{scope}
        ORDER BY score DESC LIMIT ?
```

- [ ] **Step 4: Add the same to `_bm25_only_sql`**

Change its signature to `_bm25_only_sql(graph_boost=True, validity_aware=True, project_boost=False, scope_project=False)`, add the same `proj_boost`/`scope` builders, and update its final SELECT:
```python
        SELECT c.chunk_id, c.note_permalink, c.heading_path, left(c.text, 240) AS snippet,
               n.valid_from, n.valid_until, n.superseded_by, n.project,
               rrf(f.r){boost}{validity}{proj_boost} AS score
        FROM fts f JOIN chunks c ON c.chunk_id = f.chunk_id
        JOIN notes n ON n.permalink = c.note_permalink{scope}
        ORDER BY score DESC LIMIT ?
```

- [ ] **Step 5: Thread `current`/`scope` params through `hybrid_search` and `bm25_only`**

Add `current: str | None = None` and `scope: str = "all"` kwargs to both `hybrid_search` and `bm25_only`. Compute the two booleans once:
```python
    project_boost = current is not None
    scope_project = scope == "project" and current is not None
```
Pass them into the SQL builder call (`_hybrid_sql(dim, graph_boost, validity_aware, project_boost, scope_project)` / `_bm25_only_sql(graph_boost, validity_aware, project_boost, scope_project)`).

Then build the param list in **exact appearance order**. For `hybrid_search` (current full form):
```python
    params: list = [query, pool, qvec, pool]
    if validity_aware:
        params += [now, now]
    if project_boost:
        params.append(current)   # the proj_boost CASE in the SELECT
    if scope_project:
        params.append(current)   # the WHERE n.project = ?
    params.append(limit)
```
For `bm25_only`:
```python
    params: list = [query, pool]
    if validity_aware:
        params += [now, now]
    if project_boost:
        params.append(current)
    if scope_project:
        params.append(current)
    params.append(limit)
```
(The `proj_boost` `?` is in the SELECT list after the validity CASE; the `scope` `?` is in the WHERE which the SQL string places after the SELECT — so append boost-param before scope-param, matching appearance order.)

- [ ] **Step 6: Add `project`/`scope` to `search()` and resolve `current`**

In `search(...)`, add params `project: str | None = None` and `scope: str = "all"`. Near the top (after `now_naive`):
```python
    current = resolve_current_project(project)
    if scope == "project" and current is None:
        import logging
        logging.getLogger(__name__).info(
            "recall scope='project' requested but no current project resolved; "
            "falling back to scope='all'"
        )
```
Pass `current=current, scope=scope` into both the `hybrid_search(...)` and `bm25_only(...)` calls.

Then apply the boost in the **rerank path** too (mirroring how validity is re-applied). After the cross-encoder `ranked` is produced and before/with the validity re-sort, multiply same-project scores. Simplest: after `ranked = rerank_candidates(...)`, if `current is not None`, scale each candidate's `rerank_score`:
```python
        if current is not None:
            ranked = [
                {**c, "rerank_score": c["rerank_score"] * (1.4 if c.get("project") == current else 1.0)}
                for c in ranked
            ]
```
Place this immediately after `ranked = rerank_candidates(query, cands, top_k=k)` (before the existing `if validity_aware:` block, so validity still re-sorts the final order). The `cands` already carry `project` (from the row dicts via Task 4), so `c.get("project")` is populated.

- [ ] **Step 7: Run the new ranking tests + full search suite**

Run: `uv run pytest tests/search/ -v`
Expected: PASS. The existing graph-boost/validity tests must still pass (they call `search` without `project`, so `current` resolves from the test process cwd — which is the repo, not matching the test notes' projects, so no spurious boost; if any existing test is sensitive, pass `project=None` is not enough since cwd resolves — instead those tests use notes with no `project` frontmatter, so `n.project = ?` is never true and the boost is a no-op. Confirm by running.)

**If an existing test breaks because the process cwd happens to match a test note's project:** make the ranking tests deterministic by `monkeypatch.setattr("os.getcwd", lambda: "/")` in tests that don't pass an explicit `project`, so `current is None`.

- [ ] **Step 8: Commit**

```bash
git add src/cairn/search/engine.py tests/search/test_search.py
git commit -m "feat(provenance): project boost (x1.4) + optional hard scope filter"
```

---

## Task 6: MCP tools + CLI surface + cross-project annotation

**Files:**
- Modify: `src/cairn/mcp/tools.py` — `search_tool`/`recall_tool` gain `project`/`scope`; add `project` + `cross_project` to each result.
- Modify: `src/cairn/cli.py` — `recall` command gains `--project`/`--scope`; render `[from: <project>]`.
- Test: `tests/mcp/` (find the existing tools test: `grep -rln "recall_tool\|search_tool" tests/`), `tests/test_cli.py`.

- [ ] **Step 1: Write the failing MCP test**

Find the existing MCP tools test file and add (reuse its index-fixture construction; build an index with one `project: otherrepo` note, and resolve current to `agentcairn`):

```python
def test_recall_tool_marks_cross_project(tmp_path, monkeypatch):
    from cairn.mcp.tools import recall_tool
    # build an index at index_path with a note whose frontmatter is project: otherrepo
    monkeypatch.setattr("os.getcwd", lambda: "/Users/x/git/agentcairn")
    out = recall_tool(str(index_path), "the query", embedder="fake", k=5)
    note = out["notes"][0]
    assert note["project"] == "otherrepo"
    assert note["cross_project"] is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/mcp/ -k cross_project -v`
Expected: FAIL — `KeyError: 'project'` / `'cross_project'`.

- [ ] **Step 3: Implement MCP tool changes**

In `src/cairn/mcp/tools.py`:

Add `from cairn.search import resolve_current_project` to the existing `from cairn.search import ...` line (it currently imports `get_note, open_search, search`).

`search_tool` — add params and thread them; annotate each hit:
```python
def search_tool(
    index_path: str,
    query: str,
    *,
    embedder: str = "fastembed",
    k: int = 10,
    rerank: bool = False,
    project: str | None = None,
    scope: str = "all",
) -> dict:
    """Progressive-disclosure hybrid search: compact id + snippet index.

    Recall prefers your current project (boosted, non-lossy): omit `project` to use
    the caller's working directory, or pass a repo name to target another project.
    `scope="project"` hard-limits results to the current project."""
    fetch = max(k * _FETCH_FACTOR, 25)
    current = resolve_current_project(project)
    con = _open(index_path)
    try:
        hits = search(
            con, query, embedder=_embedder(embedder), k=fetch, rerank=rerank,
            pool=max(200, fetch), project=current, scope=scope,
        )
    finally:
        con.close()
```
Then in the returned hit dicts add:
```python
                "score": round(h.score, 4),
                "project": h.project,
                "cross_project": bool(h.project and h.project != current),
                "validity": _validity_block(h.valid_from, h.valid_until, h.superseded_by, now),
```

`recall_tool` — same param additions; resolve `current` before opening; pass `project=current, scope=scope` into `search(...)`; and when building each note, add:
```python
                note["score"] = round(h.score, 4)
                note["project"] = note.get("project")  # already present from get_note
                note["cross_project"] = bool(note.get("project") and note.get("project") != current)
```
(`get_note` returns `project` after Task 4, so `note["project"]` is set; the explicit line documents intent and guarantees the key exists.)

- [ ] **Step 4: Run MCP test to verify pass**

Run: `uv run pytest tests/mcp/ -v`
Expected: PASS.

- [ ] **Step 5: Write the failing CLI test**

In `tests/test_cli.py`, add (reuse the CLI test pattern — `runner.invoke(app, [...])`, with a built index under a tmp vault/index; mirror the nearest existing `recall` CLI test, or an index-building helper):

```python
def test_cli_recall_marks_cross_project(tmp_path, monkeypatch):
    # build an index with a note project: otherrepo, set CAIRN_INDEX/CAIRN_VAULT as the
    # existing recall CLI tests do; resolve current to a different repo:
    monkeypatch.setattr("os.getcwd", lambda: "/Users/x/git/agentcairn")
    r = runner.invoke(app, ["recall", "the query", "--project", "agentcairn"])
    assert r.exit_code == 0, r.output
    assert "[from: otherrepo]" in r.output
```

**If building an index inside a CLI test is heavy, model it on the closest existing `recall`/`reindex` CLI test** (there is a recall command test pattern around `cli.py:196`). If no such helper exists, place this assertion logic in the MCP test instead and keep the CLI change covered by Step 7's manual check — but prefer a real CLI test.

- [ ] **Step 6: Implement CLI changes**

In `src/cairn/cli.py`, the `recall` command (around line 196): add options
```python
    project: str = typer.Option(None, "--project", help="Boost this project's memories (default: current dir)."),
    scope: str = typer.Option("all", "--scope", help="'all' (boost, non-lossy) or 'project' (hard-filter)."),
```
Resolve current and pass through:
```python
    from cairn.search import resolve_current_project
    current = resolve_current_project(project)
    ...
    hits = search(con, query, embedder=..., k=..., rerank=..., project=current, scope=scope)
```
(Match the existing `search(...)` call in `recall`; only add `project=current, scope=scope`.)
Then change the per-hit echo (lines 241-243) to add the marker:
```python
    for h in hits:
        mark = f"  [from: {h.project}]" if h.project and h.project != current else ""
        typer.echo(f"[{h.score:.3f}] {h.permalink}  ·  {h.heading_path}{mark}")
        typer.echo(f"        {h.snippet.strip()[:160]}")
```

- [ ] **Step 7: Run CLI tests + full suite + lint**

Run: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/cairn/mcp/tools.py src/cairn/cli.py tests/
git commit -m "feat(provenance): recall/search gain project+scope; mark cross-project hits"
```

---

## Task 7: Skill text + docs + final verification

**Files:**
- Modify: `plugin/skills/using-agentcairn-memory/SKILL.md` AND `src/cairn/assets/using-agentcairn-memory/SKILL.md` (must stay byte-identical — there is a test asserting this; edit BOTH identically).
- Modify: `README.md`, `CLAUDE.md`.

- [ ] **Step 1: Update the skill (both copies, identically)**

In the "Recall before you work" section of the skill, add one sentence noting provenance-aware recall, e.g.:
> Recall automatically prefers your current project's memories while still surfacing relevant cross-project ones (marked `[from: <project>]`); pass a project to target another repo, or scope a query to just this one.

Apply the **exact same edit** to both files:
- `plugin/skills/using-agentcairn-memory/SKILL.md`
- `src/cairn/assets/using-agentcairn-memory/SKILL.md`

- [ ] **Step 2: Verify the byte-identity test still passes**

Run: `uv run pytest tests/test_plugin_assets.py -k bundled_cursor_skill -v`
Expected: PASS (both copies identical).

- [ ] **Step 3: Update README + CLAUDE**

- README: in the recall/hybrid-intelligence section, add a bullet that recall is provenance-aware (boosts current-project memories non-lossily; `--scope project` to hard-filter; `--project` to target another repo).
- CLAUDE.md: one sentence in the recall/architecture section noting notes carry `project`/`harness` provenance and recall boosts the current project (resolved from an explicit arg or cwd).

- [ ] **Step 4: Final full verification**

Run:
```bash
uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests
```
Expected: all green.

- [ ] **Step 5: Dogfood**

```bash
# Re-sweep a couple of real sessions so new notes carry project/harness, then reindex:
uv run cairn sweep --vault "$HOME/agentcairn"
# Confirm provenance landed and recall behaves:
uv run cairn recall "agentcairn release process"           # same-project notes lead; cross-project marked [from: ...]
uv run cairn recall "agentcairn release process" --scope project   # only current-project notes
```
Confirm: new notes have `project:`/`harness:` frontmatter; recall surfaces cross-project hits with the marker under default scope and filters them under `--scope project`.

- [ ] **Step 6: Commit**

```bash
git add plugin/skills/using-agentcairn-memory/SKILL.md src/cairn/assets/using-agentcairn-memory/SKILL.md README.md CLAUDE.md
git commit -m "docs(provenance): document provenance-aware recall in skill + README/CLAUDE"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** §A persist → Task 1; §B index columns+migration+reconcile → Task 2; §C resolution → Task 3, boost/scope → Task 5 (with `project` surfaced in Task 4 as a prerequisite); §D tools/CLI/skill → Task 6 + Task 7; §E annotation (rows expose `project`) → Task 4 (surface) + Task 6 (mark). Non-goals (no git_branch, no backfill, no scoped vaults) respected — no task adds them. Dogfood + tests per spec → Task 7 + per-task tests.
- **Type consistency:** `resolve_current_project(explicit: str|None) -> str|None` defined in Task 3, used in Tasks 5/6. `Hit.project: str|None` added in Task 4, read in Tasks 5/6. SQL builders gain `project_boost`/`scope_project` booleans consistently in Tasks 3-5. Row dict key `"project"` introduced in Task 4 and consumed in Tasks 5/6. `search(..., project=, scope=)` signature defined in Task 5 and called by Task 6 tools/CLI.
- **Placeholder scan:** no TBD/TODO; every code step shows full code; the few "reuse the existing fixture/helper" notes point at concrete neighboring tests because the test-harness helpers vary — each is paired with a complete template and an explicit instruction to mirror the nearest existing test's construction.
- **Bind-param risk** (the one real footgun) is called out explicitly in Task 5 with the exact append order.
