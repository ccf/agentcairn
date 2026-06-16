# Capture Compaction Summaries — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Capture each session's latest harness compaction summary (Claude Code + Codex) as one verbatim, model-generated, project-stamped `session-summary` memory — bypassing the durability judge, redacted, with one current summary per session via supersession.

**Architecture:** The adapters already emit `EventKind.COMPACT_SUMMARY` `NormalizedEvent`s with full text + `project`/`harness`/`session` provenance; they're dropped at `select_candidates`. This plan promotes the latest summary per session as a `Candidate(kind="summary")`, routes it through the existing redact→write pipeline while skipping the judge/gate, distils it as a distinct note, and supersedes the prior session-summary for that session.

**Tech Stack:** Python 3.11+, pytest, uv (`uv run pytest` / `uv run ruff`). All in `src/cairn/ingest/`.

**Reference:** Spec `docs/specs/2026-06-16-compaction-summary-capture-design.md`. Branch `feat/capture-compaction-summaries`.

**Confirmed facts:**
- `Candidate` (`models.py`) fields: `text, session_id, cwd, git_branch, timestamp, source_path, project=None, harness=None, judgment=None, importance=None, antecedent=None`.
- `select_candidates(transcript)` loops `transcript.events`, tracks `last_assistant` per session, emits a `Candidate` per `AUTHORED_USER`.
- `ingest_transcripts`: Phase A redact+dedup (builds `pending`), Phase B batched judge over `to_judge = [i not in judged]`, Phase C gate (builds `kept`), write pass distils+writes `kept` (and supersedes via consolidation when `consolidating`).
- `distill.py` `ExtractiveDistiller.distill(cand)` builds the frontmatter dict (`title,type,permalink,tags,created,source,importance` + `project`/`harness` when present) and the body; `mark_superseded(path, by_permalink)` sets `superseded_by`; `write_derived_note(note, vault_root, subdir="memories")`.
- `EventKind.COMPACT_SUMMARY` exists; Claude Code + Codex adapters emit it.

---

## Task 1: `Candidate.kind` + promote the latest summary per session

**Files:** `src/cairn/ingest/models.py`, `src/cairn/ingest/pipeline.py` (`select_candidates`); Test: `tests/ingest/test_pipeline.py` (or wherever `select_candidates` is tested).

- [ ] **Step 1: Write the failing test**

Add a test that builds a `Transcript` with several `AUTHORED_USER` events and **three** `COMPACT_SUMMARY` events in one session, and asserts `select_candidates` returns the user candidates plus **exactly one** summary candidate (`kind="summary"`) equal to the **latest** summary, with provenance populated. Mirror the existing `select_candidates` test's `NormalizedEvent`/`Transcript` construction. Skeleton:

```python
def test_select_candidates_promotes_latest_compaction_summary():
    from cairn.ingest.events import EventKind, NormalizedEvent
    from cairn.ingest.models import Transcript
    from cairn.ingest.pipeline import select_candidates
    from pathlib import Path

    def ev(kind, text, ts):
        return NormalizedEvent(kind=kind, role="user", text=text, timestamp=ts,
            session_id="s1", project="agentcairn", git_branch=None,
            source_path=Path("/x/t.jsonl"), harness="claude-code")

    t = Transcript(session_id="s1", cwd="/x", path=Path("/x/t.jsonl"), events=[
        ev(EventKind.AUTHORED_USER, "do the thing", "2026-06-16T00:00:00Z"),
        ev(EventKind.COMPACT_SUMMARY, "summary v1", "2026-06-16T01:00:00Z"),
        ev(EventKind.AUTHORED_USER, "another ask", "2026-06-16T02:00:00Z"),
        ev(EventKind.COMPACT_SUMMARY, "summary v2 LATEST", "2026-06-16T03:00:00Z"),
    ])
    cands = select_candidates(t)
    users = [c for c in cands if c.kind == "user"]
    summaries = [c for c in cands if c.kind == "summary"]
    assert len(users) == 2
    assert len(summaries) == 1
    assert summaries[0].text == "summary v2 LATEST"
    assert summaries[0].session_id == "s1" and summaries[0].project == "agentcairn"
    assert summaries[0].harness == "claude-code"
```
(Confirm the real `Transcript` constructor signature and adapt — it has `session_id`, `cwd`, `path`, `events`, `kind_counts`.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_pipeline.py -k promotes_latest_compaction -v`
Expected: FAIL — `Candidate` has no `kind`, and no summary candidate is produced.

- [ ] **Step 3: Add `Candidate.kind`** (`models.py`)

After `antecedent` (keep all fields with defaults so ordering is valid):
```python
    kind: str = "user"  # "user" (authored prompt) | "summary" (compaction session summary)
```

- [ ] **Step 4: Promote the latest summary in `select_candidates`** (`pipeline.py`)

Track the latest `COMPACT_SUMMARY` event per session in the loop, then emit summary candidates after. Inside the loop, before the `if e.kind != EventKind.AUTHORED_USER: continue`, add tracking:
```python
        if e.kind == EventKind.COMPACT_SUMMARY:
            latest_summary[sid] = e   # keep last by stream order
            continue
```
Declare `latest_summary: dict[str, NormalizedEvent] = {}` next to `last_assistant`. After the loop, append one summary candidate per session:
```python
    for sid, e in latest_summary.items():
        out.append(
            Candidate(
                text=e.text, session_id=sid, cwd=transcript.cwd, git_branch=e.git_branch,
                timestamp=e.timestamp, source_path=e.source_path, project=e.project,
                harness=e.harness, antecedent=None, kind="summary",
            )
        )
```
(Import `NormalizedEvent` for the type hint if needed; `EventKind` is already imported.)

- [ ] **Step 5: Run to verify pass + full suite**

Run: `uv run pytest tests/ingest/ -v && uv run ruff check src tests`
Expected: PASS (new test + existing ingest tests unaffected — user candidates unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/ingest/models.py src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): promote the latest compaction summary per session (Candidate.kind)"
```

---

## Task 2: Bypass the judge + force-keep summaries

**Files:** `src/cairn/ingest/pipeline.py` (`ingest_transcripts` Phase B + Phase C); Test: `tests/ingest/test_pipeline.py`.

- [ ] **Step 1: Write the failing test**

A summary candidate must be KEPT and WRITTEN even with a judge that rejects everything, and must never appear in the judge's input. Use the existing ingest test harness (a fake/embedding judge + a tmp vault + ledger). Skeleton (adapt to the real `ingest_transcripts` test fixtures):

```python
def test_compaction_summary_bypasses_judge_and_is_kept(tmp_path):
    # Build a transcript with one COMPACT_SUMMARY (+ optionally a junk user turn),
    # a judge stub that records its inputs and rejects everything (distilled=None).
    # ingest_transcripts(...) with that judge.
    # Assert: the session-summary note is written; the summary text was NOT in the
    # judge's recorded inputs.
    ...
    assert any("session-summary" in p.read_text() for p in report.written)  # or check note kind
    assert SUMMARY_TEXT not in judge_seen_inputs
```
Look at the existing `ingest_transcripts` tests to reuse their judge stub + vault/ledger setup; if a stub judge records inputs, assert against it, else assert the summary is written despite a reject-all judge.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_pipeline.py -k bypasses_judge -v`
Expected: FAIL — the summary is either judged (appears in inputs) or gated out.

- [ ] **Step 3: Exclude summaries from the judge (Phase B)**

In `ingest_transcripts`, where `to_judge` is built (`to_judge = [i for i in range(len(pending)) if i not in judged]`), also exclude summary candidates:
```python
    to_judge = [
        i for i in range(len(pending))
        if i not in judged and pending[i][0].kind != "summary"
    ]
```

- [ ] **Step 4: Force-keep summaries (Phase C)**

At the top of the gate loop body (`for idx, (cand, h) in enumerate(pending):`), before the `heuristic = score(...)` logic, short-circuit summaries:
```python
        if cand.kind == "summary":
            cand = replace(cand, importance=0.9)
            kept.append((cand, h))
            continue
```
(This keeps them unconditionally and skips the judge/heuristic branches. `replace` is already imported.)

- [ ] **Step 5: Run to verify pass + full ingest suite**

Run: `uv run pytest tests/ingest/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): compaction summaries bypass the judge and are always kept"
```

---

## Task 3: Session-summary note shape (distiller)

**Files:** `src/cairn/ingest/distill.py`; Test: `tests/ingest/test_distill.py`.

- [ ] **Step 1: Write the failing test**

```python
def test_distiller_session_summary_note_shape():
    from cairn.ingest.distill import ExtractiveDistiller
    from cairn.ingest.models import Candidate
    from pathlib import Path

    cand = Candidate(
        text="This session is being continued…\nSummary: did X, fixed Y.",
        session_id="sess-7", cwd="/x", git_branch=None, timestamp="2026-06-16T03:00:00Z",
        source_path=Path("/x/t.jsonl"), project="agentcairn", harness="claude-code",
        kind="summary",
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.frontmatter["kind"] == "session-summary"
    assert "session-summary" in note.frontmatter["tags"]
    assert note.frontmatter["project"] == "agentcairn"
    assert note.frontmatter["harness"] == "claude-code"
    assert note.frontmatter["source"] == "memory://session/sess-7"
    assert "did X, fixed Y." in note.body            # verbatim summary retained
    assert note.frontmatter["type"] == "memory"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_distill.py -k session_summary -v`
Expected: FAIL — no `kind` frontmatter / `session-summary` tag.

- [ ] **Step 3: Branch the distiller on `kind`**

In `ExtractiveDistiller.distill`, after computing `h`/`slug`/`title`, branch. For a summary, override the title and frontmatter and use a verbatim body:
```python
        if candidate.kind == "summary":
            proj = candidate.project or "session"
            day = (candidate.timestamp or "")[:10]
            title = f"Session summary · {proj}{(' · ' + day) if day else ''}"
            slug = f"session-summary-{_slugify(proj)}-{h[:8]}"
            frontmatter = {
                "title": title,
                "type": "memory",
                "kind": "session-summary",
                "permalink": slug,
                "tags": ["session-summary", "ingested"],
                "created": candidate.timestamp,
                "source": f"memory://session/{candidate.session_id}",
                "importance": round(candidate.importance, 3) if candidate.importance is not None else 0.9,
            }
            if candidate.project:
                frontmatter["project"] = candidate.project
            if candidate.harness:
                frontmatter["harness"] = candidate.harness
            body = f"- [context] Session summary (model-generated) #session-summary\n- [verbatim] {candidate.text.strip()}\n"
            return Note(permalink=slug, frontmatter=frontmatter, body=body)
```
Place this branch BEFORE the existing user-note construction (which stays unchanged). Confirm `Note`, `_slugify`, `content_hash` (`h`) are in scope (they are — used by the existing path).

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/ingest/test_distill.py -v`
Expected: PASS (new + existing distill tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/distill.py tests/ingest/test_distill.py
git commit -m "feat(ingest): session-summary note shape (verbatim, model-generated, provenanced)"
```

---

## Task 4: Supersede the prior session-summary per session

**Files:** `src/cairn/ingest/distill.py` (helper), `src/cairn/ingest/pipeline.py` (write pass); Test: `tests/ingest/test_pipeline.py`.

- [ ] **Step 1: Write the failing test**

Sweep a session with one compaction → one current summary note. Re-sweep the same session with a *different* (newer) compaction → the new note is current and the prior is `superseded_by` it; an unchanged re-sweep writes nothing. Skeleton:

```python
def test_resweep_supersedes_prior_session_summary(tmp_path):
    # sweep 1: transcript with COMPACT_SUMMARY "v1" -> 1 session-summary note, not superseded
    # sweep 2 (fresh ledger or new content): transcript with COMPACT_SUMMARY "v2" same session
    #   -> new note current; the v1 note now has superseded_by == new permalink
    ...
    v1 = next(p for p in vault_memories if "v1" in p.read_text())
    assert "superseded_by:" in v1.read_text()
```
(Use the real `ingest_transcripts` entry + a tmp vault; mirror existing supersession tests.)

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ingest/test_pipeline.py -k supersedes_prior_session -v`
Expected: FAIL — the prior summary is not superseded.

- [ ] **Step 3: Add the supersession helper** (`distill.py`)

```python
def supersede_prior_session_summaries(vault_root: Path, subdir: str, session_id: str, new_permalink: str) -> int:
    """Mark any existing session-summary note for `session_id` (other than
    `new_permalink`, not already superseded) as superseded_by the new one.
    Returns the count superseded. Tolerates malformed notes."""
    src = f"memory://session/{session_id}"
    n = 0
    base = Path(vault_root) / subdir
    for path in base.glob("*.md") if base.exists() else []:
        try:
            note = parse_note(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fm = note.frontmatter
        if (fm.get("kind") == "session-summary" and fm.get("source") == src
                and fm.get("permalink") != new_permalink and not fm.get("superseded_by")):
            try:
                mark_superseded(path, new_permalink)
                n += 1
            except Exception:
                pass
    return n
```
(`parse_note`, `mark_superseded`, `Path` are already imported in `distill.py`.)

- [ ] **Step 4: Call it in the write pass** (`pipeline.py`)

In the write loop, after `path = write_derived_note(note, vault_root, subdir=subdir)` and before/after `ledger.add(h)`, for summary candidates supersede the prior:
```python
        path = write_derived_note(note, vault_root, subdir=subdir)
        if cand.kind == "summary":
            from cairn.ingest.distill import supersede_prior_session_summaries
            report.superseded += supersede_prior_session_summaries(
                vault_root, subdir, cand.session_id, note.permalink
            )
        ledger.add(h)
```
(Hoist the import to the top of `pipeline.py` with the other `distill` imports if preferred.)

- [ ] **Step 5: Run to verify pass + full ingest suite**

Run: `uv run pytest tests/ingest/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/cairn/ingest/distill.py src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): one current session-summary per session (supersede prior on re-sweep)"
```

---

## Task 5: Redaction + isolation regression + full verification

**Files:** Test: `tests/ingest/`.

- [ ] **Step 1: Redaction test**

A secret embedded in a compaction summary must be redacted before the note is written:
```python
def test_summary_is_redacted(tmp_path):
    # COMPACT_SUMMARY text contains e.g. "sk-ABCDEF1234567890ABCDEF" (a key-shaped token)
    # ingest -> the written session-summary note must NOT contain the raw secret.
    ...
    assert "sk-ABCDEF1234567890ABCDEF" not in note_text
```
(Use a token the redactor recognizes — check `cairn.ingest.redact` for a matched pattern.)

- [ ] **Step 2: Isolation regression**

Confirm user-prompt capture is unchanged: a transcript with user turns AND a compaction produces the same user-derived notes as before plus the one summary note (assert user note count unaffected). If an existing ingest test asserts authored counts, confirm it still passes.

- [ ] **Step 3: Full suite + lint**

Run: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all green.

- [ ] **Step 4: Dogfood**

```bash
# Sweep a real Claude Code vault into a SCRATCH vault to inspect safely:
uv run cairn sweep --vault "$HOME/agentcairn" 2>&1 | tail -5
# Find session-summary notes:
grep -rl "kind: session-summary" "$HOME/agentcairn/memories" | head
# Inspect one: confirm verbatim summary + project/harness provenance + no secret leakage.
uv run cairn recall "what did this session accomplish" 2>&1 | head -15
```
Report: how many session-summary notes were written, that they carry `project`/`harness`, and that recall surfaces one for a session-level query.

- [ ] **Step 5: Commit**

```bash
git add tests/ingest/
git commit -m "test(ingest): summary redaction + user-capture isolation"
```

---

## Final verification

- [ ] `uv run pytest` green; `uv run ruff` clean.
- [ ] `select_candidates` yields the latest summary per session (`kind="summary"`); user candidates unchanged.
- [ ] Summaries bypass the judge (never in its input) and are always kept.
- [ ] Session-summary note: `kind: session-summary`, tag, verbatim body, `project`/`harness`, `source`.
- [ ] Re-sweep supersedes the prior session-summary; unchanged re-sweep is a no-op (deduped).
- [ ] Secrets in summaries are redacted; user-prompt capture is unaffected.
- [ ] Dogfood: real Claude Code sessions produce project-stamped session-summary notes; recall surfaces them.

## Self-Review (during planning)

- **Spec coverage:** `Candidate.kind` + promote-latest (T1 ↔ §A/§B), judge-bypass + force-keep (T2 ↔ §C), note shape (T3 ↔ §D), supersession (T4 ↔ §E), redaction + isolation + dogfood (T5 ↔ Testing). Recall integration (§F) needs no code (rides existing provenance/currency). Non-goals (atomic extraction, Cursor/Antigravity, Idea 2) untouched.
- **Consistency:** `Candidate.kind` ("user"/"summary") set in T1, read in T2 (judge exclusion + force-keep) and T4 (supersession trigger) and T3 (distiller branch). `kind: session-summary` frontmatter (T3) is the key the supersession helper matches on (T4). `source: memory://session/<id>` is the per-session key shared by T3/T4.
- **Placeholders:** none; each code step is complete. The few "mirror the existing test fixtures" notes point at concrete existing tests because the ingest test harness (judge stub, vault/ledger setup) is established and shouldn't be re-invented.
- **Correctness focus:** redaction stays mandatory (Phase A, unchanged); supersession is fail-safe (tolerates malformed notes, never aborts the sweep); idempotency via the existing content-hash ledger.
