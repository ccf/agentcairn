# Bi-temporal validity — Design Spec (v1.1)

**Date:** 2026-06-09
**Status:** approved (design); implementation to follow.

## 1. Goal & motivation

Let memory notes express **when a fact is valid** and **which fact supersedes which**, and make retrieval honor that — so the *current* fact wins recall and the agent can reason over time. Motivated by the v1 benchmark's sharpest gap: temporal/knowledge-update questions **retrieve well (~0.92 recall@5) but answer poorly (~0.30 QA)** — the system has no notion of which fact is current. Third and last v1.1 sub-project. Grounded in Zep/Graphiti's non-lossy invalidation model and SQL:2011 valid-time semantics (research brief, this session).

## 2. Scope (locked in brainstorming)

**In scope:** the three reserved frontmatter fields (`valid_from`/`valid_until`/`superseded_by`) end-to-end (parse → index → honor); **supersession** (current fact wins); **validity-window-aware retrieval** with **as-of-now** semantics; **soft-demote + annotate** (penalize superseded/expired in ranking, never hide — non-lossy — and expose validity in recall output for the agent).

**Deferred (NOT this plan):** point-in-time "as of T" queries; auto/LLM population of validity (the agent or a human sets the fields); the transaction-time axis (true bi-temporal — when-recorded vs when-true); single-winner dedup of overlapping windows; transitive supersession-chain following at query time.

## 3. Field semantics (frontmatter)

Three optional keys on any note. All absent → the note behaves exactly as today (feature is **inert by default**).

| Key | Type | Meaning |
|---|---|---|
| `valid_from` | YAML date or datetime | Fact became true. **Closed/inclusive** lower bound. |
| `valid_until` | YAML date or datetime | Fact stopped being true. **Open/exclusive** upper bound. |
| `superseded_by` | bare permalink string | This note was replaced; value names the replacement note's permalink. |

- **Interval is half-open `[valid_from, valid_until)`** (SQL:2011 / XTDB): `valid_from <= t AND t < valid_until`. Closed start, strict-less end. `valid_until: 2024-06-01` means "valid through end of May 31," not "including June 1."
- **Absent = open.** Missing `valid_from` → −∞; missing `valid_until` → +∞ (still valid — the common case). Stored as SQL `NULL`, never a magic sentinel date.
- **`superseded_by` is a bare permalink** (e.g. `fav-color-green-1a2b3c`), not a `[[wikilink]]` — so it joins directly against the `notes.permalink` PK with no link-resolution step.

## 4. "Current as of now" predicate + date handling

A note is **current** iff:
1. `valid_from IS NULL OR valid_from <= now`
2. `valid_until IS NULL OR now < valid_until` (strict — half-open)
3. `superseded_by IS NULL`

Failing (1) → `not_yet_valid`; failing (2) → `expired`; failing (3) → `superseded`. All three are demote conditions (§5) and annotation statuses (§7).

**Date/timezone handling (the dominant bug class — handle at the parse boundary):**
- New helper `parse_temporal(value) -> datetime | None` (in `src/cairn/temporal.py`): YAML yields `date`/`datetime`/`str`/`None`. Normalize ALL to a **tz-aware UTC `datetime`**: a naive value is **assumed UTC**; a date-only value maps to **00:00 UTC**; a string is parsed with `datetime.fromisoformat(s.replace("Z","+00:00"))` (Py 3.12). `None`/empty → `None`.
- Store in DuckDB as plain **`TIMESTAMP`** (UTC; NOT `TIMESTAMPTZ` — its binning is session-TZ-dependent and it raises on `TIMESTAMPTZ` vs `DATE`).
- `now = datetime.now(timezone.utc)` captured **once per query**, bound as a SQL parameter. Never `utcnow()`, never `now()` in a row loop, never `BETWEEN` (it is inclusive-inclusive — wrong for half-open).
- A **malformed** field (`parse_temporal` raises) is caught per-field → logged warning + stored `NULL` (treated as absent). One bad date never aborts a reconcile (non-lossy ingestion).

```python
# src/cairn/temporal.py
from datetime import date, datetime, timezone

def parse_temporal(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise TypeError(f"unparseable temporal value: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
```

## 5. Soft-demote (non-lossy ranking)

A **second multiplier** on the final score in `_hybrid_sql` AND `_bm25_only_sql`, alongside the existing graph-boost, joining `notes n ON n.permalink = c.note_permalink`:

```sql
* (CASE
     WHEN n.superseded_by IS NOT NULL THEN ?                              -- penalty
     WHEN n.valid_until IS NOT NULL AND n.valid_until <= ? THEN ?         -- expired
     WHEN n.valid_from IS NOT NULL AND n.valid_from > ? THEN ?            -- not-yet-valid
     ELSE 1.0
   END)
```

- Penalty = module constant `_VALIDITY_PENALTY = 0.5` (tunable; deliberately **soft** — superseded/expired facts still surface, just below the current one — preserving the non-lossy/browsable guarantee). Avoid a near-zero value (that hides).
- **Inert** when no note has validity fields (all NULL → `ELSE 1.0`) — so corpora without validity (e.g. the LoCoMo benchmark) are unaffected, exactly like graph-boost.
- Gated by a new `validity_aware: bool = True` param threaded `search → hybrid_search/_hybrid_sql` and `bm25_only/_bm25_only_sql` (mirrors `graph_boost` precedent: default-on, toggleable; the benchmark passes it explicitly when needed). No new env var (YAGNI).
- `now` is bound as the SQL parameter for the expired/not-yet-valid comparisons (same `now` for ranking and annotation).

## 6. Index changes

Add three **trailing** columns to `notes` (`schema.py`):
```sql
CREATE TABLE IF NOT EXISTS notes (
  permalink VARCHAR PRIMARY KEY, path VARCHAR, title VARCHAR, type VARCHAR,
  content_hash VARCHAR, mtime DOUBLE,
  valid_from TIMESTAMP, valid_until TIMESTAMP, superseded_by VARCHAR
)
```
- **Fix the positional INSERT.** `index_note` (`build.py`) does `INSERT INTO notes VALUES (?, ?, ?, ?, ?, ?)` — switch to an **explicit column list** with the 9 columns/placeholders (self-documenting, future-safe), passing `parse_temporal(fm.get("valid_from"))`, `parse_temporal(fm.get("valid_until"))`, and `fm.get("superseded_by") or None`.
- Existing explicit-column SELECTs (`get_note`, reconcile's indexed-map) are unaffected by trailing columns. Content-hash change detection already re-indexes a note when frontmatter edits change its bytes — edited validity reconciles with no extra logic.
- No `Note` model change required (the frontmatter dict already carries the keys).

## 7. Annotation (recall / search / build_context output)

Surface validity so the **agent** sees currency directly:
- Extend `get_note`'s SELECT to include the three columns; add the three validity fields to `Hit` (carried out of the hybrid SQL, which already joins `notes` for the multiplier — no extra query).
- `search_tool` / `recall_tool` add a `validity` sub-dict per hit/note; `build_context_tool` adds it to `root` + neighbors:
  ```json
  "validity": {
    "status": "current",          // current | superseded | expired | not_yet_valid
    "valid_from": "2024-01-01T00:00:00Z",
    "valid_until": null,
    "superseded_by": "fav-color-green-1a2b3c"
  }
  ```
- `status` computed once with the §4 predicate against a single `now`. Notes with no validity fields → `status: "current"`, null bounds (or omit the bounds).
- Add an `as_of: "<now ISO>"` anchor at the top level of `recall`/`search` responses (so `valid_until` is interpretable as past/current/future).
- A shared helper `validity_status(valid_from, valid_until, superseded_by, now) -> str` (in `cairn.temporal`) is used by both the SQL-mirrored Python annotation and any tests.

## 8. Testing (all offline, `FakeEmbedder`)

- **`tests/test_temporal.py`:** `parse_temporal` (datetime, date→00:00, naive→UTC, tz-aware→UTC, `Z` string, `None`/empty→None, malformed→`TypeError`); `validity_status` truth table including the **half-open boundary** — a fact with `valid_until == now` reads **`expired`**, not current (the easiest thing to get backwards); `superseded_by` set → `superseded`; `valid_from > now` → `not_yet_valid`; all-None → `current`.
- **Index/reconcile:** a note with validity frontmatter populates the three columns (tz-normalized); a malformed `valid_from` → column NULL + the rest of the note still indexed (warned, not aborted).
- **Soft-demote ranking:** with two notes matching a query where one is `superseded_by` the other (or expired), the current note ranks above the superseded/expired one; `validity_aware=False` removes the penalty; a corpus with no validity fields is bit-identical to today (inert).
- **Annotation:** `recall`/`search`/`build_context` return the `validity` sub-dict with correct `status` and an `as_of` anchor.
- Engine regression: all existing search tests pass unchanged (default `validity_aware=True` is inert without fields).

## 9. Risks (baked into the design)

- **Naive-vs-aware / date-vs-datetime `TypeError`** — normalized at the parse boundary (§4); asserted in tests.
- **Half-open asymmetry** (`<=` start, `<` end) — boundary test pins it.
- **Positional INSERT** (`build.py`) — switched to explicit columns (§6).
- **Supersession chains** (`A→B→C`) — v1 demotes any note with `superseded_by` set (A and B), leaves C current; do NOT auto-follow transitively at query time (cycle risk). Annotation may resolve at most one hop for the replacement's title, guarded against self-reference (as `build_context_tool` already guards).
- **Overlapping/contradictory windows** — out of scope; soft-demote leaves both visible. A `cairn doctor` overlap check is a documented follow-up, not v1.
- **Non-lossy guarantee** — superseded/expired notes are demoted (×0.5), never deleted or filtered; still fully retrievable and browsable.
