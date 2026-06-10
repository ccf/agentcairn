# Bi-temporal Validity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support `valid_from`/`valid_until`/`superseded_by` frontmatter end-to-end (parse → index → soft-demote retrieval → annotate), with as-of-now semantics, per `docs/specs/2026-06-09-bitemporal-validity-design.md`.

**Architecture:** A new `cairn.temporal` module (date normalization + status). The `notes` table gains three trailing columns populated by `reconcile`. The hybrid/bm25 SQL gets a second score multiplier (alongside graph-boost) that demotes superseded/expired/not-yet-valid notes, gated by a `validity_aware` toggle (default-on, inert without validity fields). `Hit` carries the validity columns; the MCP tools annotate each result with a `validity` sub-dict + an `as_of` anchor. Non-lossy: demote (×0.5), never hide.

**Tech Stack:** Python 3.12 stdlib `datetime`, DuckDB. No new dependency.

---

## Conventions
- Run with `uv` from `/Users/ccf/git/agentcairn`. Branch `feat/v1.1-bitemporal` (already created; never `main`).
- SPDX header; `from __future__ import annotations`; ruff `E,F,I,UP,B` (B008 ignored), line-length 100. Keep `uv run ruff check .` + `uv run pre-commit run --all-files` green (pre-commit ruff v0.15.16 = CI).
- Baseline: core 196 passed, 3 skipped; benchmark 29 passed.

## Key current code (read before editing)
- `src/cairn/index/schema.py` — `notes(permalink PK, path, title, type, content_hash, mtime)`.
- `src/cairn/index/build.py:74` — `index_note` does `INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)` (POSITIONAL — must become explicit-column).
- `src/cairn/search/engine.py` — `Hit` dataclass (~line 51); `_hybrid_sql(dim, graph_boost)` + `_bm25_only_sql(graph_boost)` (the final SELECT multiplies the score by a graph-boost CASE; `FROM fused f JOIN chunks c ON c.chunk_id = f.chunk_id`); `hybrid_search`/`bm25_only`/`search`; `get_note` (~line 267, `SELECT permalink, path, title, type FROM notes`).
- `src/cairn/mcp/tools.py` — `search_tool`/`recall_tool` (use `Hit`/`get_note`), `build_context_tool` (uses `get_note`).

---

### Task 1: `cairn.temporal` — parse_temporal + validity_status

**Files:**
- Create: `src/cairn/temporal.py`
- Test: `tests/test_temporal.py`

**Context:** Normalize any YAML temporal value to a tz-aware UTC `datetime`, and compute a note's validity status against `now`. The half-open boundary (`valid_until == now` → expired) is the easiest thing to get backwards — pin it.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_temporal.py
# SPDX-License-Identifier: Apache-2.0
from datetime import date, datetime, timezone

import pytest

from cairn.temporal import parse_temporal, validity_status

UTC = timezone.utc


def test_parse_temporal_variants():
    assert parse_temporal(None) is None
    assert parse_temporal("") is None
    assert parse_temporal(date(2024, 1, 2)) == datetime(2024, 1, 2, tzinfo=UTC)  # date -> 00:00 UTC
    assert parse_temporal(datetime(2024, 1, 2, 8, 0)) == datetime(2024, 1, 2, 8, tzinfo=UTC)  # naive -> UTC
    assert parse_temporal("2024-01-02T08:00:00Z") == datetime(2024, 1, 2, 8, tzinfo=UTC)
    aware = datetime(2024, 1, 2, 8, 0, tzinfo=UTC)
    assert parse_temporal(aware) == aware


def test_parse_temporal_malformed_raises():
    with pytest.raises((TypeError, ValueError)):
        parse_temporal("not-a-date")
    with pytest.raises(TypeError):
        parse_temporal(123)


def test_validity_status_half_open_boundary():
    now = datetime(2024, 6, 1, tzinfo=UTC)
    # valid_until == now -> EXPIRED (strict end: now < valid_until is false)
    assert validity_status(None, now, None, now) == "expired"
    # valid_until just after now -> current
    assert validity_status(None, datetime(2024, 6, 1, 0, 0, 1, tzinfo=UTC), None, now) == "current"


def test_validity_status_cases():
    now = datetime(2024, 6, 1, tzinfo=UTC)
    assert validity_status(None, None, None, now) == "current"                       # no fields
    assert validity_status(None, None, "other-note", now) == "superseded"            # superseded wins
    assert validity_status(datetime(2024, 7, 1, tzinfo=UTC), None, None, now) == "not_yet_valid"
    assert validity_status(datetime(2024, 1, 1, tzinfo=UTC), None, None, now) == "current"
    assert validity_status(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC), None, now) == "expired"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_temporal.py -v`
Expected: FAIL — `No module named 'cairn.temporal'`.

- [ ] **Step 3: Implement `src/cairn/temporal.py`**

```python
# src/cairn/temporal.py
# SPDX-License-Identifier: Apache-2.0
"""Valid-time helpers for bi-temporal validity. Normalize any YAML temporal value
to a tz-aware UTC datetime, and compute a note's validity status vs `now`.
Half-open interval [valid_from, valid_until): closed start, strict-less end."""

from __future__ import annotations

from datetime import date, datetime, timezone


def parse_temporal(value: object) -> datetime | None:
    """Normalize a frontmatter temporal value to a tz-aware UTC datetime.
    None/"" -> None. naive -> assumed UTC. date-only -> 00:00 UTC. str -> ISO-8601.
    Raises TypeError/ValueError on an unparseable value (caller treats as absent)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):  # NOTE: datetime is a date subclass — check datetime first
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise TypeError(f"unparseable temporal value: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def validity_status(
    valid_from: datetime | None,
    valid_until: datetime | None,
    superseded_by: str | None,
    now: datetime,
) -> str:
    """current | superseded | expired | not_yet_valid (as of `now`)."""
    if superseded_by:
        return "superseded"
    if valid_until is not None and not (now < valid_until):  # half-open: end is exclusive
        return "expired"
    if valid_from is not None and valid_from > now:
        return "not_yet_valid"
    return "current"
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_temporal.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/temporal.py tests/test_temporal.py
git commit -m "feat(temporal): parse_temporal + validity_status (half-open as-of-now)"
```

---

### Task 2: Index — `notes` validity columns + reconcile population

**Files:**
- Modify: `src/cairn/index/schema.py` (notes table), `src/cairn/index/build.py` (`index_note` INSERT)
- Test: `tests/index/test_reconcile.py` (add validity-population cases)

**Context:** Add three trailing columns; populate from frontmatter via `parse_temporal`. **Fix the positional INSERT** to an explicit column list. A malformed temporal field → NULL + the note still indexes (non-lossy).

- [ ] **Step 1: Write the failing test**

```python
# add to tests/index/test_reconcile.py
def test_reconcile_populates_validity_columns(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile

    v = tmp_path / "vault"
    v.mkdir()
    (v / "job.md").write_text(
        "---\ntitle: Job\npermalink: job\nvalid_from: 2024-01-01\n"
        "valid_until: 2024-06-01\nsuperseded_by: job2\n---\nworked at X\n"
    )
    (v / "ok.md").write_text(
        "---\ntitle: OK\npermalink: ok\nvalid_from: bad-date\n---\nplain note\n"  # malformed -> NULL
    )
    emb = FakeEmbedder(dim=8)
    con = open_index(str(tmp_path / "i.duckdb"), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(v), emb)
    row = con.execute(
        "SELECT valid_from, valid_until, superseded_by FROM notes WHERE permalink='job'"
    ).fetchone()
    assert row[0] is not None and row[1] is not None and row[2] == "job2"
    # malformed valid_from -> NULL, but the note is still indexed (non-lossy)
    ok = con.execute(
        "SELECT valid_from, superseded_by FROM notes WHERE permalink='ok'"
    ).fetchone()
    assert ok is not None and ok[0] is None and ok[1] is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/index/test_reconcile.py::test_reconcile_populates_validity_columns -v`
Expected: FAIL — column `valid_from` does not exist.

- [ ] **Step 3: Add the columns in `src/cairn/index/schema.py`**

Change the `notes` `CREATE TABLE` to (trailing 3 columns):
```python
    con.execute(
        "CREATE TABLE IF NOT EXISTS notes ("
        "  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR, type VARCHAR,"
        "  content_hash VARCHAR, mtime DOUBLE,"
        "  valid_from TIMESTAMP, valid_until TIMESTAMP, superseded_by VARCHAR)"
    )
```

- [ ] **Step 4: Fix the INSERT + populate in `src/cairn/index/build.py`**

Add the import: `from cairn.temporal import parse_temporal`. Add a tiny local helper (module-level) so a malformed value is swallowed to None:
```python
def _safe_temporal(value: object):
    try:
        return parse_temporal(value)
    except (TypeError, ValueError):
        return None
```
Replace the positional INSERT in `index_note` (currently `INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)` with 6 values: permalink, path, title, type, content_hash, mtime) with an **explicit column list** + the three validity values pulled from `note.frontmatter`:
```python
    fm = note.frontmatter
    con.execute(
        "INSERT INTO notes "
        "(permalink, path, title, type, content_hash, mtime, "
        " valid_from, valid_until, superseded_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            permalink,
            str(path),
            str(fm.get("title") or ""),
            str(fm.get("type") or ""),
            content_hash,            # keep the EXISTING content_hash expression used today
            mtime,                   # keep the EXISTING mtime expression used today
            _safe_temporal(fm.get("valid_from")),
            _safe_temporal(fm.get("valid_until")),
            (fm.get("superseded_by") or None),
        ],
    )
```
(Use the SAME `content_hash`/`mtime` values the current code passes — read them off the existing 6-value list; only the permalink/path/title/type are shown above. Keep behavior identical for the first 6 columns.)

- [ ] **Step 5: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/index/ -v`
Expected: PASS (new validity test + all existing index/reconcile tests — trailing columns don't disturb existing SELECTs).

- [ ] **Step 6: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/index/schema.py src/cairn/index/build.py tests/index/test_reconcile.py
git commit -m "feat(index): notes validity columns + reconcile population (explicit INSERT)"
```

---

### Task 3: Search — soft-demote multiplier + `validity_aware` toggle + Hit carries validity

**Files:**
- Modify: `src/cairn/search/engine.py`
- Test: `tests/search/test_search.py`

**Context:** Add a validity multiplier (alongside graph-boost) in `_hybrid_sql` and `_bm25_only_sql`, gated by `validity_aware`. Always JOIN `notes n` and return its validity columns so `Hit` carries them (annotation needs them regardless of the toggle). The multiplier demotes superseded/expired/not-yet-valid by `_VALIDITY_PENALTY = 0.5`; `now` is bound; inert when validity columns are all NULL.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/search/test_search.py
def test_validity_soft_demote(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile
    from cairn.search import open_search, search

    v = tmp_path / "v"
    v.mkdir()
    # two notes about the same topic; "old" is superseded by "new"
    (v / "old.md").write_text(
        "---\ntitle: Old\npermalink: old\nsuperseded_by: new\n---\nfavorite color is blue\n"
    )
    (v / "new.md").write_text("---\ntitle: New\npermalink: new\n---\nfavorite color is green\n")
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con0 = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con0, str(v), emb)
    con0.close()
    con = open_search(str(idx))
    try:
        on = {h.permalink: h.score for h in search(con, "favorite color", embedder=emb, validity_aware=True)}
        off = {h.permalink: h.score for h in search(con, "favorite color", embedder=emb, validity_aware=False)}
        hits = search(con, "favorite color", embedder=emb)
    finally:
        con.close()
    # superseded "old" is demoted when validity_aware (default on)
    assert on["old"] < off["old"]
    # Hit carries validity fields regardless of the toggle
    h_old = next(h for h in hits if h.permalink == "old")
    assert h_old.superseded_by == "new"
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/search/test_search.py::test_validity_soft_demote -v`
Expected: FAIL — `search()` has no `validity_aware`; `Hit` has no `superseded_by`.

- [ ] **Step 3: Implement in `src/cairn/search/engine.py`**

1. Add a module constant `_VALIDITY_PENALTY = 0.5` and `from datetime import datetime, timezone`.
2. Extend `Hit` with three optional fields (keep them last, defaulted, so existing positional constructions still work):
```python
@dataclass
class Hit:
    chunk_id: str
    permalink: str
    heading_path: str
    snippet: str
    score: float
    valid_from: str | None = None
    valid_until: str | None = None
    superseded_by: str | None = None
```
3. In `_hybrid_sql` and `_bm25_only_sql`: add `JOIN notes n ON n.permalink = c.note_permalink`; add `n.valid_from, n.valid_until, n.superseded_by` to the SELECT (after the snippet); and append a **validity multiplier** to the score expression that is present only when `validity_aware` is true. Signature becomes `_hybrid_sql(dim, graph_boost=True, validity_aware=True)` / `_bm25_only_sql(graph_boost=True, validity_aware=True)`. The validity fragment (penalty inlined as the trusted float constant; two `?` for `now`):
```python
validity = (
    f" * (CASE WHEN n.superseded_by IS NOT NULL THEN {_VALIDITY_PENALTY}"
    f" WHEN n.valid_until IS NOT NULL AND n.valid_until <= ? THEN {_VALIDITY_PENALTY}"
    f" WHEN n.valid_from IS NOT NULL AND n.valid_from > ? THEN {_VALIDITY_PENALTY}"
    f" ELSE 1.0 END)"
    if validity_aware else ""
)
```
   The final SELECT becomes (note: `n.*` columns added, both multipliers applied):
   `SELECT f.chunk_id, c.note_permalink, c.heading_path, left(c.text,240) AS snippet, n.valid_from, n.valid_until, n.superseded_by, f.rrf_score{graph_boost_mult}{validity} AS score FROM fused f JOIN chunks c ON c.chunk_id=f.chunk_id JOIN notes n ON n.permalink=c.note_permalink ORDER BY score DESC LIMIT ?`
4. Thread `validity_aware` + bind `now` through `hybrid_search`/`bm25_only`/`search`. **Param ordering is positional by appearance in the SQL** — when `validity_aware`, insert `now, now` (the two CASE comparisons) into the bind list at the position where the validity fragment appears (after the existing score-expression binds, before the final `LIMIT`). Build the bind list conditionally:
```python
def hybrid_search(con, query, qvec, *, dim, limit=10, pool=200, graph_boost=True, validity_aware=True):
    now = datetime.now(timezone.utc)
    sql = _hybrid_sql(dim, graph_boost, validity_aware)
    params = [query, pool, qvec, pool]
    if validity_aware:
        params += [now, now]
    params.append(limit)
    rows = con.execute(sql, params).fetchall()
    # row now has 3 extra trailing-but-before-score columns: valid_from, valid_until, superseded_by
    # build dicts including those three (as ISO strings for the temporal ones, or None)
```
   Apply the analogous change to `bm25_only` (its base params are `[query, pool]` then `+[now, now]` if validity_aware, then `limit`). Update the row→dict shaping in both to include `valid_from`/`valid_until`/`superseded_by` (stringify the timestamps to ISO with `.isoformat()` when not None).
5. `search()` gains `validity_aware: bool = True`, passes it to `hybrid_search`/`bm25_only`, and constructs each `Hit` with the three validity fields from the row dicts. The rerank path preserves them when mapping back.

(Read the current exact SELECT/param code and adapt — keep the graph-boost behavior identical; only ADD the notes JOIN, the 3 columns, the conditional validity multiplier, and the `now` binds.)

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/search/ -v`
Expected: PASS (new validity test + ALL existing search tests — with no validity fields the multiplier is `ELSE 1.0`, so scores are unchanged; graph-boost test still passes).

- [ ] **Step 5: Verify benchmark unaffected**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest benchmarks/tests/ -q`
Expected: 29 passed (the benchmark corpora have no validity fields → inert).

- [ ] **Step 6: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/search/engine.py tests/search/test_search.py
git commit -m "feat(search): validity soft-demote multiplier + Hit validity fields"
```

---

### Task 4: Annotate recall/search/build_context output

**Files:**
- Modify: `src/cairn/search/engine.py` (`get_note` SELECT), `src/cairn/mcp/tools.py`
- Test: `tests/mcp/test_tools.py`

**Context:** Extend `get_note` to return the validity columns; add a `validity` sub-dict (`status` + the three fields) to each result in `search_tool`/`recall_tool`/`build_context_tool`, and an `as_of` anchor (top-level) in `search_tool`/`recall_tool`. `status` via `cairn.temporal.validity_status` against a single `now`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/mcp/test_tools.py
def _vault_with_validity(tmp_path):
    from cairn.embed import FakeEmbedder
    from cairn.index import open_index, reconcile

    v = tmp_path / "vault"
    v.mkdir()
    (v / "old.md").write_text(
        "---\ntitle: Old\npermalink: old\nsuperseded_by: new\n---\ncolor blue\n"
    )
    (v / "new.md").write_text("---\ntitle: New\npermalink: new\n---\ncolor green\n")
    idx = tmp_path / "i.duckdb"
    emb = FakeEmbedder(dim=8)
    con = open_index(str(idx), dim=emb.dim, model_id=emb.model_id)
    reconcile(con, str(v), emb)
    con.close()
    return idx


def test_search_tool_annotates_validity(tmp_path):
    from cairn.mcp.tools import search_tool

    out = search_tool(str(_vault_with_validity(tmp_path)), "color", embedder="fake", k=10)
    assert "as_of" in out
    byperm = {h["permalink"]: h for h in out["hits"]}
    assert byperm["old"]["validity"]["status"] == "superseded"
    assert byperm["old"]["validity"]["superseded_by"] == "new"
    assert byperm["new"]["validity"]["status"] == "current"


def test_recall_tool_annotates_validity(tmp_path):
    from cairn.mcp.tools import recall_tool

    out = recall_tool(str(_vault_with_validity(tmp_path)), "color", embedder="fake", k=5)
    assert "as_of" in out
    assert all("validity" in n for n in out["notes"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/mcp/test_tools.py -k validity -v`
Expected: FAIL — no `validity`/`as_of` keys.

- [ ] **Step 3: Implement**

In `src/cairn/search/engine.py` `get_note`: extend the SELECT to `SELECT permalink, path, title, type, valid_from, valid_until, superseded_by FROM notes WHERE permalink = ?` and include `valid_from`/`valid_until`/`superseded_by` (ISO strings or None) in the returned dict.

In `src/cairn/mcp/tools.py` add a helper and wire it in:
```python
from datetime import datetime, timezone

from cairn.temporal import validity_status


def _parse_iso(s):
    return datetime.fromisoformat(s) if s else None


def _validity_block(valid_from, valid_until, superseded_by, now):
    return {
        "status": validity_status(_parse_iso(valid_from), _parse_iso(valid_until), superseded_by, now),
        "valid_from": valid_from,
        "valid_until": valid_until,
        "superseded_by": superseded_by,
    }
```
- `search_tool`: capture `now = datetime.now(timezone.utc)` once; for each hit add `"validity": _validity_block(h.valid_from, h.valid_until, h.superseded_by, now)`; add top-level `"as_of": now.isoformat()`.
- `recall_tool`: same `now`; each hydrated note dict (from `get_note`) gets `"validity": _validity_block(note["valid_from"], note["valid_until"], note["superseded_by"], now)`; add `"as_of"`.
- `build_context_tool`: add `"validity"` to `root` (and to each resolved neighbor) using `get_note`'s validity fields and a single `now`.

- [ ] **Step 4: Run to verify it passes**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/mcp/ -v`
Expected: PASS (new annotation tests + existing MCP tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add src/cairn/search/engine.py src/cairn/mcp/tools.py tests/mcp/test_tools.py
git commit -m "feat(mcp): annotate recall/search/build_context with validity + as_of"
```

---

### Task 5: Docs

**Files:**
- Modify: `README.md`, `src/cairn/cli.py` (recall help mentions validity)
- Test: none (docs); pre-commit + a grep.

- [ ] **Step 1: Update `README.md`**

In the v1.1 roadmap, mark bi-temporal shipped:
```markdown
  - ✅ **Bi-temporal validity** — frontmatter `valid_from`/`valid_until`/`superseded_by`; recall soft-demotes superseded/expired facts (non-lossy) and annotates currency. *(shipped)*
```
Add a short "Temporal memory" note near "How it works" describing the three fields + as-of-now soft-demote (2-3 sentences).

- [ ] **Step 2: CLI help**

In `src/cairn/cli.py` `recall`, append to the command's docstring/help that results are validity-aware (current facts rank above superseded/expired; set `superseded_by`/`valid_until` in note frontmatter).

- [ ] **Step 3: Full suite + pre-commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q && uv run pytest benchmarks/tests/ -q && uv run ruff check . && uv run pre-commit run --all-files`
Expected: core green (~205 passed, 3 skipped); benchmark 29 passed; ruff clean; pre-commit green.

- [ ] **Step 4: Commit**

```bash
cd /Users/ccf/git/agentcairn
git add README.md src/cairn/cli.py
git commit -m "docs: bi-temporal validity (shipped in v1.1)"
```

---

## Self-Review Notes (for the controller)
- **Spec coverage:** §3 fields → Task 2; §4 predicate+date handling → Task 1; §5 soft-demote+toggle → Task 3; §6 index+positional-INSERT fix → Task 2; §7 annotation → Task 4; §8 testing → tests across tasks; §9 risks (half-open boundary test, positional INSERT fix, malformed→NULL, non-lossy demote, no transitive chains) → addressed.
- **Type consistency:** `parse_temporal(value) -> datetime|None`, `validity_status(valid_from, valid_until, superseded_by, now) -> str`, `Hit(..., valid_from, valid_until, superseded_by)`, `_hybrid_sql(dim, graph_boost, validity_aware)`, `search(..., validity_aware=True)`, `_validity_block(...)` — consistent across tasks.
- **Critical invariants:** the validity multiplier is INERT when no note has validity fields (all NULL → ×1.0) → existing search tests + the LoCoMo benchmark are unchanged; `validity_aware` defaults True but is toggleable (mirrors `graph_boost`); demote is ×0.5 (non-lossy — never hide/filter); `now` captured once per query and bound (never `utcnow()`/`BETWEEN`). Verify the conditional bind-param ordering against the actual SQL; the existing-tests-pass gate catches a mis-bind.
