# Provenance-Aware Recall (#28, half 1)

**Status:** Approved (2026-06-15)
**Issue:** #28 (memory provenance/attribution + optional scoped/shared vaults) — this spec covers **half 1 only**: provenance/attribution + provenance-aware recall. Half 2 (separate/scoped vaults, shared-vault multi-user attribution) is out of scope.
**Affects:** `src/cairn/ingest/distill.py` (persist origin to frontmatter), `src/cairn/index/` (schema columns + reconcile), `src/cairn/search/engine.py` (project resolution + boost/filter), `src/cairn/mcp/tools.py` + `src/cairn/cli.py` (tool/CLI params), result formatting, tests. No change to redaction, the judge, consolidation, or the Markdown contract beyond two additive frontmatter keys.

## Problem

A single global cross-project vault is agentcairn's correct default — a lesson from project A should surface in project B. But today recall is *blind to origin*: every note ranks the same regardless of which repo it came from, and there's no way to prefer current-project memories or scope a query to one repo. The infrastructure to fix this is **90% present but 0% used**: `NormalizedEvent` already carries `project`, `harness`, `session_id`, `git_branch`, `source_path` through the whole ingest pipeline, and `Candidate` preserves them — but `ExtractiveDistiller` writes only `source: memory://session/<id>` to frontmatter and drops the rest. The index has no origin columns, and recall has no notion of "current project."

This adds **provenance-aware recall**: persist origin onto each note, index it, and at recall time boost current-project memories (non-lossy — cross-project still surfaces) with an optional hard project scope.

## Research — current state (verified on disk)

- **Frontmatter today** (`src/cairn/ingest/distill.py:52-66`): `title`, `type`, `permalink`, `tags`, `created`, `source` (`memory://session/<session_id>`), `importance`. `project`/`harness`/`git_branch`/`source_path` are dropped at distill time.
- **Provenance is plumbed:** `NormalizedEvent` (`src/cairn/ingest/events.py:29-40`) and `Candidate` (`src/cairn/ingest/models.py:28-41`) both carry `project`, `harness`, `session_id`, `git_branch`, `source_path`. `pipeline.py:52` sets `project=e.project`.
- **`project_from_cwd`** (`src/cairn/ingest/events.py:43-48`): origin "project" = the final path segment of cwd (the repo / working-dir name); `None` for missing/empty/root cwd.
- **Index schema** (`src/cairn/index/schema.py:15-26`): `notes(permalink PK, path, title, type, content_hash, mtime, valid_from, valid_until, superseded_by)` — no origin columns. There is an existing migration pattern (`tests/index/test_schema.py` — `test_open_index_migrates_old_6col_notes_table`).
- **Ranking** (`src/cairn/search/engine.py:242-350`): BM25 + vector arms fused by RRF; a **graph boost** (×1.2, `engine.py:86-102`) and a **validity demote** (×0.5) are applied as multipliers in the fusion SQL and re-applied post-rerank. MCP `recall`/`search` tools (`src/cairn/mcp/tools.py:66-162`) take only `query`/`k` — no project context flows in.

## Goal / decisions (brainstorm)

1. **Current-project signal (option C):** an explicit `project` param on recall/search **overrides**; otherwise fall back to the server/CLI process `os.getcwd()` via `project_from_cwd`; if neither resolves (cwd is `/`, empty, or raises), the current project is `None` → no boost.
2. **Boost + optional hard scope:** a **soft ×1.4 multiplier** for same-project notes is the default (non-lossy — cross-project notes are untouched and still surface). An optional `scope` param adds a query-time hard filter (`scope="project"` → `WHERE project = ?`); default `scope="all"` (boost only). This is a query-time filter on the single vault — **not** a separate vault (half 2).
3. **Persist `project` + `harness`** as flat top-level frontmatter keys (Obsidian-friendly, matches existing flat schema), both as new indexed columns. Session provenance stays in the existing `source`. **Drop `git_branch`** (branch-level boost is too granular; YAGNI).
4. **No backfill:** `project` can't be reconstructed from a stored `session_id`, so provenance applies going-forward only. Old notes get `NULL` project — no boost, but still surface under the default `scope="all"`.
5. **Annotate cross-project origin lightly:** in `recall`/`build_context` output, show a marker only when a result's `project` is set and *differs* from the resolved current project; same-project and project-less results render unchanged.

## Architecture

### A. Persist provenance (ingest → vault) — `src/cairn/ingest/distill.py`

In `ExtractiveDistiller.distill`, after building the existing frontmatter dict, add the origin keys **only when present** on the candidate (omit the key entirely when `None`, never emit `null`):

```python
if candidate.project:
    frontmatter["project"] = candidate.project
if candidate.harness:
    frontmatter["harness"] = candidate.harness
```

`source: memory://session/<session_id>` is unchanged. `git_branch`/`source_path` remain dropped. The note body and all other fields are untouched.

### B. Index the origin (vault → DuckDB) — `src/cairn/index/`

- **Schema** (`schema.py`): add two nullable columns to `notes`: `project VARCHAR`, `harness VARCHAR`.
- **Migration:** mirror the existing `valid_from`/`valid_until` migration — on opening an index whose `notes` table lacks the columns, `ALTER TABLE notes ADD COLUMN project VARCHAR` / `... harness VARCHAR`. Bump the index schema-version sentinel so a stale index rebuilds cleanly (the index is a rebuildable cache).
- **Reconcile/upsert:** where a note's frontmatter is read into the `notes` row, populate `project`/`harness` from `frontmatter.get("project")` / `.get("harness")` (`None` when absent). Tolerate absent keys (old notes → `NULL`).

### C. Resolve current project + rank — `src/cairn/search/engine.py`

**Resolution helper:**
```python
def resolve_current_project(explicit: str | None) -> str | None:
    """Current project for provenance-aware recall: explicit arg wins, else the
    process cwd's repo name, else None (no boost)."""
    if explicit:
        return explicit
    try:
        return project_from_cwd(os.getcwd())
    except OSError:
        return None
```
(`project_from_cwd` already returns `None` for `/`/empty.)

**`search()` signature** gains `project: str | None = None` and `scope: str = "all"`. It resolves `current = resolve_current_project(project)`, then:
- **Boost:** in the fusion SQL (both the hybrid path and the BM25-only path), add a multiplier — a note whose `project` equals `current` is multiplied by **1.4** (alongside the existing graph ×1.2 and validity ×0.5). Re-apply the same factor in the post-rerank scoring branch, exactly as graph/validity are re-applied.
- **Scope:** when `scope == "project"` **and** `current is not None`, add `WHERE project = :current` (i.e. `AND note.project = ?`). When `scope == "project"` but `current is None`, log a one-line warning and behave as `scope="all"` (nothing to filter on). When `current is None`, the boost multiplier is simply not applied (no-op CASE).

Notes with `NULL` project are never excluded under `scope="all"` and get no boost; under `scope="project"` they're excluded (documented).

### D. Tool / CLI surface

- **MCP tools** (`src/cairn/mcp/tools.py`): `recall_tool` and `search_tool` gain optional `project: str | None = None` and `scope: str = "all"` parameters, threaded into `search(...)`. Tool docstrings explain: omit `project` to use the caller's cwd; pass it to target another repo; `scope="project"` to hard-scope.
- **CLI** (`src/cairn/cli.py`): the `search`/`recall` commands gain `--project` and `--scope` options, passed through. The CLI naturally runs in the user's cwd, so the fallback resolution is correct there.
- **`using-agentcairn-memory` skill** (`plugin/skills/.../SKILL.md` + the bundled `src/cairn/assets/...` copy, kept byte-identical by the existing test): a sentence noting recall prefers your current project automatically and that cross-project hits are marked — so the agent knows cross-project results are intentional and citable.

### E. Annotate cross-project origin — result formatting

The search result row/object must **expose `project`** (selected from the `notes` table alongside the existing fields) so the formatter can read it. In the `recall`/`build_context` result rendering, when a result's `project` is truthy and `!= current`, append a light marker such as `  [from: <project>]` to that result's header/snippet line. Same-project or `NULL`-project results are unchanged. The renderer receives `current` from the search call. Presentation only — no ranking effect.

## Data flow

```
ingest:  NormalizedEvent(project,harness,…) → Candidate → distill →
         frontmatter{…, project, harness} → note .md
index:   reconcile reads frontmatter → notes(…, project, harness)
recall:  recall(query, project?, scope?) →
           current = explicit ?? project_from_cwd(os.getcwd()) ?? None
           fusion SQL: ×1.4 where notes.project == current   (boost, non-lossy)
                       [+ WHERE project == current  if scope=="project"]
           post-rerank: re-apply ×1.4
           format: mark results where project != current  → "[from: <project>]"
```

## Error handling

- No resolved project (`current is None`): boost is a no-op; `scope="project"` logs and falls back to `"all"`; never raises.
- `os.getcwd()` raises (deleted cwd) → caught → `None`.
- Pre-migration index (no columns): migration adds them on open; reconcile tolerates missing frontmatter keys (`NULL`).
- `NULL`-project notes: always surface under `scope="all"`; excluded under `scope="project"`.
- Unknown `scope` value: treat any value other than `"project"` as `"all"` (lenient).

## Testing / verification

- **Distiller** (`tests/ingest/test_distill.py`): `project`/`harness` persisted to frontmatter when on the Candidate; omitted (key absent, not `null`) when `None`; `source` unchanged.
- **Index** (`tests/index/test_schema.py` + reconcile test): new columns created; populated from frontmatter on reconcile; old-index open migrates (adds columns) without data loss; a note with no project → `NULL`.
- **Resolution** (`tests/search/`): explicit arg wins over cwd; cwd fallback derives the repo name; `/`/empty/raising cwd → `None`.
- **Ranking** (`tests/search/test_search.py`, mirroring the graph-boost/validity tests): with two otherwise-equal notes, the same-project one ranks above the cross-project one; the cross-project note is still in the results (non-lossy); `scope="project"` returns only current-project notes; `project=None`/unresolved → ordering unchanged (no-op); boost re-applied through the rerank path.
- **Annotation:** a cross-project result carries the `[from: <project>]` marker; a same-project and a `NULL`-project result do not.
- `uv run pytest` green; `uv run ruff check`/`format` clean.
- **Dogfood:** re-sweep a couple of real sessions so new notes carry `project`/`harness`; reindex; run `cairn recall <query>` from inside `agentcairn` and confirm same-project notes lead while a known cross-project memory still appears (marked); `cairn recall <query> --scope project` returns only `agentcairn` notes.

## File-by-file

| File | Change |
|---|---|
| `src/cairn/ingest/distill.py` | persist `project` + `harness` to frontmatter when present |
| `src/cairn/index/schema.py` | add `project`/`harness` columns + migration; bump schema version |
| `src/cairn/index/` (reconcile/upsert) | populate `project`/`harness` from frontmatter |
| `src/cairn/search/engine.py` | `resolve_current_project`; `search()` `project`/`scope` params; ×1.4 boost + optional filter (fusion SQL + rerank) |
| `src/cairn/mcp/tools.py` | `recall_tool`/`search_tool` gain `project`/`scope`; docstrings |
| `src/cairn/cli.py` | `search`/`recall` gain `--project`/`--scope` |
| result formatting (recall/build_context) | light `[from: <project>]` marker for cross-project hits |
| `plugin/skills/using-agentcairn-memory/SKILL.md` + `src/cairn/assets/.../SKILL.md` | note current-project preference + cross-project marker (kept byte-identical) |
| `tests/ingest/test_distill.py`, `tests/index/test_schema.py`, `tests/search/test_search.py` | distiller persistence, index columns+migration, resolution, boost/scope/annotation |

## Non-goals

- **Separate or project-scoped vaults**, and **shared-vault multi-user `author` attribution** (issue #28 half 2).
- **`git_branch`** persistence/boost.
- **Backfilling** existing notes (provenance is going-forward only).
- No change to redaction, the judge, consolidation, reindex chunking, or recall fusion math beyond the additive multiplier/filter.

## Open questions

None.
