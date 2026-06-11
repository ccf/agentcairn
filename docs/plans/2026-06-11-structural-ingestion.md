# Structural Ingestion Candidate-Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace agentcairn's text-pattern noise filter with positive-identification, fail-closed candidate selection driven by transcript structural metadata, so only genuinely human-authored turns become memories.

**Architecture:** A new harness-agnostic `events.py` (`EventKind` enum + `NormalizedEvent`). A per-harness classifier (`classify_claude_code`) maps raw JSONL entries to an `EventKind` using structural fields (`isMeta`/`toolUseResult`/`isCompactSummary`/`isVisibleInTranscriptOnly`/`origin`). `parse_transcript` returns `NormalizedEvent`s with text sanitized and provenance preserved; the pipeline ingests only `AUTHORED_USER`. The brittle `is_framing_noise` denylist is deleted; `sanitize_text` stays.

**Tech Stack:** Python 3.12, dataclasses + `enum`, pytest. Spec: `docs/specs/2026-06-11-structural-ingestion-design.md`.

---

## File structure

```
src/cairn/ingest/events.py    # CREATE: EventKind enum, NormalizedEvent, project_from_cwd
src/cairn/ingest/locate.py    # MODIFY: classify_claude_code(); parse_transcript -> list[NormalizedEvent]
src/cairn/ingest/models.py    # MODIFY: Transcript.events (drop Turn); Candidate +project; IngestReport +authored/+event_kinds
src/cairn/ingest/pipeline.py  # MODIFY: select_candidates(); per-kind tally in ingest_transcript
src/cairn/ingest/sanitize.py  # MODIFY: DELETE is_framing_noise + prefixes (keep sanitize_text)
src/cairn/ingest/__init__.py  # MODIFY: exports (drop Turn; add EventKind, NormalizedEvent)
src/cairn/cli.py              # MODIFY: surface per-kind tally in `cairn ingest`
src/cairn/__init__.py         # MODIFY: __version__ -> 0.7.0
CHANGELOG.md                  # MODIFY: 0.7.0 section
tests/ingest/test_events.py   # CREATE: classifier + selection + fail-closed + real-noise fixtures
tests/ingest/test_locate.py   # MODIFY: turns -> events
tests/ingest/test_pipeline.py # MODIFY: Turn -> NormalizedEvent; structural drop test
tests/ingest/test_sanitize.py # MODIFY: remove is_framing_noise tests
tests/test_cli.py             # MODIFY: ingest per-kind tally test
```

Run all Python from the repo root with `uv run`. Commit after each task. A pre-commit hook runs ruff + pytest; if ruff reformats, re-add and re-commit.

---

## Task 1: Normalized event model

**Files:**
- Create: `src/cairn/ingest/events.py`
- Test: `tests/ingest/test_events.py`

- [ ] **Step 1: Write the failing test** — create `tests/ingest/test_events.py`:

```python
# tests/ingest/test_events.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd


def test_event_kind_values():
    assert EventKind.AUTHORED_USER.value == "authored_user"
    assert EventKind.TOOL_RESULT.value == "tool_result"
    assert EventKind.META_INJECTION.value == "meta_injection"
    assert EventKind.COMPACT_SUMMARY.value == "compact_summary"
    assert EventKind.UNKNOWN.value == "unknown"


def test_project_from_cwd():
    assert project_from_cwd("/Users/ccf/git/agentcairn") == "agentcairn"
    assert project_from_cwd("/Users/ccf/git/agentcairn/") == "agentcairn"  # trailing slash
    assert project_from_cwd("/") is None
    assert project_from_cwd(None) is None
    assert project_from_cwd("") is None


def test_normalized_event_is_frozen_and_carries_provenance():
    e = NormalizedEvent(
        kind=EventKind.AUTHORED_USER,
        role="user",
        text="hi",
        timestamp="t",
        session_id="s",
        project="agentcairn",
        git_branch="main",
        source_path=Path("/x.jsonl"),
    )
    assert e.kind is EventKind.AUTHORED_USER
    assert e.project == "agentcairn"
    import dataclasses

    assert dataclasses.is_dataclass(e)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_events.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.events'`.

- [ ] **Step 3: Create `src/cairn/ingest/events.py`**

```python
# src/cairn/ingest/events.py
# SPDX-License-Identifier: Apache-2.0
"""Harness-agnostic normalized transcript events.

Each harness adapter classifies its native entries into one of these kinds; the
pipeline distills only AUTHORED_USER. Classification is positive-identification
and fail-closed: anything not affirmatively recognized as authored prose is some
other kind (ultimately UNKNOWN) and never becomes a candidate."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class EventKind(str, Enum):
    AUTHORED_USER = "authored_user"  # the ONLY candidate source (Layer A)
    AUTHORED_ASSISTANT = "authored_assistant"  # retained in stream, not a candidate
    TOOL_RESULT = "tool_result"
    META_INJECTION = "meta_injection"  # slash-command markers, skill bodies, hooks, task-notifications
    COMPACT_SUMMARY = "compact_summary"
    SYSTEM = "system"
    UNKNOWN = "unknown"  # fail-closed bucket -> never a candidate


@dataclass(frozen=True)
class NormalizedEvent:
    kind: EventKind
    role: str
    text: str  # sanitized at parse
    timestamp: str | None
    # provenance (plumbing for #28; carried, not yet written to frontmatter)
    session_id: str | None
    project: str | None  # origin project identity, derived from cwd
    git_branch: str | None
    source_path: Path


def project_from_cwd(cwd: str | None) -> str | None:
    """Origin 'project' identity for provenance: the final path segment of cwd
    (the repo / working-dir name). None for missing/empty/root cwd."""
    if not cwd:
        return None
    return Path(cwd).name or None
```

- [ ] **Step 4: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_events.py -q`
Expected: 3 passed.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/events.py tests/ingest/test_events.py && git commit -m "feat(ingest): normalized EventKind + NormalizedEvent model"
```

---

## Task 2: Claude Code classifier (positive-ID, fail-closed)

**Files:**
- Modify: `src/cairn/ingest/locate.py`
- Test: `tests/ingest/test_locate.py`

- [ ] **Step 1: Append the failing tests** to `tests/ingest/test_locate.py`:

```python
def test_classify_authored_user():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {"type": "user", "message": {"role": "user", "content": "fix the bug please"}}
    assert classify_claude_code(obj) == EventKind.AUTHORED_USER


def test_classify_tool_result():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert classify_claude_code({"type": "user", "toolUseResult": {}, "message": {}}) == EventKind.TOOL_RESULT


def test_classify_meta_via_isMeta():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert classify_claude_code({"type": "user", "isMeta": True, "message": {}}) == EventKind.META_INJECTION


def test_classify_meta_via_origin_task_notification():
    # <task-notification> carries NO isMeta/toolUseResult — only an `origin` object.
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {"type": "user", "origin": {"kind": "task-notification"}, "message": {}}
    assert classify_claude_code(obj) == EventKind.META_INJECTION


def test_classify_compact_summary():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True, "message": {}}
    assert classify_claude_code(obj) == EventKind.COMPACT_SUMMARY


def test_classify_assistant_and_system():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert classify_claude_code({"type": "assistant", "message": {}}) == EventKind.AUTHORED_ASSISTANT
    assert classify_claude_code({"type": "system"}) == EventKind.SYSTEM


def test_classify_unknown_is_failclosed():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert classify_claude_code({"type": "last-prompt"}) == EventKind.UNKNOWN
    assert classify_claude_code({}) == EventKind.UNKNOWN
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_locate.py -k classify -q`
Expected: FAIL — `cannot import name 'classify_claude_code'`.

- [ ] **Step 3: Add `classify_claude_code` to `src/cairn/ingest/locate.py`** — add the import near the top (`from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd`) and this function (place it above `parse_transcript`):

```python
def classify_claude_code(obj: dict) -> EventKind:
    """Positive-identification, fail-closed classification of a raw Claude Code
    JSONL entry. A user turn is AUTHORED_USER only when it carries NONE of the
    harness's injection markers. Order matters: compact-summary first (it also
    sets isVisibleInTranscriptOnly), then tool results, then meta/injected."""
    t = obj.get("type")
    if t == "user":
        if obj.get("isCompactSummary"):
            return EventKind.COMPACT_SUMMARY
        if "toolUseResult" in obj:
            return EventKind.TOOL_RESULT
        if obj.get("isMeta") or obj.get("isVisibleInTranscriptOnly") or obj.get("origin"):
            return EventKind.META_INJECTION
        return EventKind.AUTHORED_USER
    if t == "assistant":
        return EventKind.AUTHORED_ASSISTANT
    if t == "system":
        return EventKind.SYSTEM
    return EventKind.UNKNOWN
```

- [ ] **Step 4: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_locate.py -k classify -q`
Expected: 7 passed.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/locate.py tests/ingest/test_locate.py && git commit -m "feat(ingest): Claude Code structural classifier (positive-ID, fail-closed)"
```

---

## Task 3: parse_transcript → NormalizedEvent stream + model changes

**Files:**
- Modify: `src/cairn/ingest/locate.py`, `src/cairn/ingest/models.py`, `src/cairn/ingest/__init__.py`
- Test: `tests/ingest/test_locate.py`

- [ ] **Step 1: Update `src/cairn/ingest/models.py`** — replace the `Turn` dataclass with an events-bearing `Transcript`, and add `project` to `Candidate`. New file body for the top three dataclasses:

```python
from cairn.ingest.events import NormalizedEvent


@dataclass
class Transcript:
    session_id: str
    cwd: str | None
    git_branch: str | None
    path: Path
    events: list[NormalizedEvent] = field(default_factory=list)


@dataclass
class Candidate:
    """One unit considered for distillation, with provenance back to its origin."""

    text: str
    session_id: str
    cwd: str | None
    git_branch: str | None
    timestamp: str | None
    source_path: Path
    project: str | None = None  # origin project identity (provenance plumbing for #28)
```

Delete the old `@dataclass class Turn: ...` block entirely. Add the `from cairn.ingest.events import NormalizedEvent` import at the top of `models.py` (after `from pathlib import Path`).

- [ ] **Step 2: Replace `parse_transcript` in `src/cairn/ingest/locate.py`** with an events-producing version (keep `_CONTENT_TYPES`, `_extract_text`, `encode_cwd`, `find_transcripts` as-is):

```python
def parse_transcript(path: Path) -> Transcript:
    """Parse a jsonl transcript into a Transcript of NormalizedEvents. Skips
    metadata/bookkeeping lines and malformed lines. Each user/assistant content
    row is classified structurally and sanitized; provenance is preserved per row."""
    session_id = path.stem
    cwd: str | None = None
    git_branch: str | None = None
    events: list[NormalizedEvent] = []
    for raw in path.read_text(errors="replace").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue  # partial/corrupt line — transcripts are append-only
        if not isinstance(obj, dict):
            continue
        if obj.get("type") not in _CONTENT_TYPES:
            continue  # only user/assistant rows carry conversational content
        msg = obj.get("message")
        if not isinstance(msg, dict):
            continue
        text = _extract_text(msg.get("content"))
        if not text:
            continue
        if session_id == path.stem:
            session_id = obj.get("sessionId") or session_id
        line_cwd = obj.get("cwd")
        if cwd is None:
            cwd = line_cwd
        if git_branch is None:
            git_branch = obj.get("gitBranch")
        events.append(
            NormalizedEvent(
                kind=classify_claude_code(obj),
                role=msg.get("role", obj["type"]),
                text=text,
                timestamp=obj.get("timestamp"),
                session_id=obj.get("sessionId") or session_id,
                project=project_from_cwd(line_cwd or cwd),
                git_branch=obj.get("gitBranch") or git_branch,
                source_path=path,
            )
        )
    return Transcript(session_id=session_id, cwd=cwd, git_branch=git_branch, path=path, events=events)
```

(`_extract_text` already applies `sanitize_text` from the 0.6.1 work — no change needed there.)

- [ ] **Step 3: Update `src/cairn/ingest/__init__.py`** — drop `Turn`, add `EventKind`/`NormalizedEvent`:

```python
# src/cairn/ingest/__init__.py
# SPDX-License-Identifier: Apache-2.0
from cairn.ingest.dedup import DedupLedger, content_hash
from cairn.ingest.distill import Distiller, ExtractiveDistiller, write_derived_note
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.locate import encode_cwd, find_transcripts, parse_transcript
from cairn.ingest.models import (
    Candidate,
    IngestReport,
    RedactionResult,
    Transcript,
)
from cairn.ingest.pipeline import ingest_transcript
from cairn.ingest.redact import redact

__all__ = [
    "Candidate",
    "DedupLedger",
    "Distiller",
    "EventKind",
    "ExtractiveDistiller",
    "IngestReport",
    "NormalizedEvent",
    "RedactionResult",
    "Transcript",
    "content_hash",
    "encode_cwd",
    "find_transcripts",
    "ingest_transcript",
    "parse_transcript",
    "redact",
    "write_derived_note",
]
```

- [ ] **Step 4: Update the two `.turns` assertions in `tests/ingest/test_locate.py`** to `.events`, and add kind checks. Replace `test_session_id_comes_from_first_accepted_turn`'s last assertion and `test_parse_transcript_extracts_turns_and_provenance` with:

```python
    # (in test_session_id_comes_from_first_accepted_turn)
    assert [e.text for e in tr.events] == ["real question here"]
```

```python
def test_parse_transcript_extracts_turns_and_provenance(tmp_path):
    t = tmp_path / "s.jsonl"
    _write_transcript(t)
    tr = parse_transcript(t)
    assert tr.session_id == "sess-1"
    assert tr.cwd == "/Users/x/proj"
    assert tr.git_branch == "main"
    from cairn.ingest.events import EventKind

    assert [(e.role, e.text, e.kind) for e in tr.events] == [
        ("user", "fix the bug", EventKind.AUTHORED_USER),
        ("assistant", "Fixed it.", EventKind.AUTHORED_ASSISTANT),
    ]
    assert tr.events[0].project == "proj"  # provenance from cwd
```

- [ ] **Step 5: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_locate.py -q`
Expected: all pass.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/locate.py src/cairn/ingest/models.py src/cairn/ingest/__init__.py tests/ingest/test_locate.py && git commit -m "feat(ingest): parse_transcript yields NormalizedEvent stream with provenance"
```

---

## Task 4: select_candidates + per-kind report + delete is_framing_noise

**Files:**
- Modify: `src/cairn/ingest/pipeline.py`, `src/cairn/ingest/models.py`, `src/cairn/ingest/sanitize.py`
- Test: `tests/ingest/test_pipeline.py`, `tests/ingest/test_sanitize.py`

- [ ] **Step 1: Extend `IngestReport` in `src/cairn/ingest/models.py`** — add `authored` and `event_kinds`, and surface them in `to_dict`:

```python
@dataclass
class IngestReport:
    candidates: int = 0
    redactions: int = 0
    deduped: int = 0  # skipped as already-seen
    gated_out: int = 0  # below importance threshold
    authored: int = 0  # AUTHORED_USER events selected before redact/dedup/gate
    event_kinds: dict[str, int] = field(default_factory=dict)  # all event kinds seen
    written: list[Path] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation (Paths stringified)."""
        return {
            "candidates": self.candidates,
            "redactions": self.redactions,
            "deduped": self.deduped,
            "gated_out": self.gated_out,
            "authored": self.authored,
            "event_kinds": self.event_kinds,
            "written": [str(p) for p in self.written],
        }
```

- [ ] **Step 2: Replace `_candidates`/`ingest_transcript` in `src/cairn/ingest/pipeline.py`** — swap the imports and body:

```python
from collections import Counter
from dataclasses import replace
from pathlib import Path

from cairn.ingest.dedup import DedupLedger, content_hash
from cairn.ingest.distill import Distiller, ExtractiveDistiller, write_derived_note
from cairn.ingest.events import EventKind
from cairn.ingest.importance import KEEP_THRESHOLD, is_important
from cairn.ingest.models import Candidate, IngestReport, Transcript
from cairn.ingest.redact import redact


def select_candidates(transcript: Transcript) -> list[Candidate]:
    """One candidate per genuinely-authored user event. Everything else (tool
    results, meta injections, summaries, assistant turns) is excluded by kind."""
    return [
        Candidate(
            text=e.text,
            session_id=e.session_id or transcript.session_id,
            cwd=transcript.cwd,
            git_branch=e.git_branch,
            timestamp=e.timestamp,
            source_path=e.source_path,
            project=e.project,
        )
        for e in transcript.events
        if e.kind == EventKind.AUTHORED_USER
    ]


def ingest_transcript(
    transcript: Transcript,
    *,
    vault_root: Path,
    ledger: DedupLedger,
    threshold: float = KEEP_THRESHOLD,
    distiller: Distiller | None = None,
    subdir: str = "memories",
    dry_run: bool = False,
) -> IngestReport:
    distiller = distiller or ExtractiveDistiller()
    report = IngestReport()
    report.event_kinds = dict(Counter(e.kind.value for e in transcript.events))
    candidates = select_candidates(transcript)
    report.authored = len(candidates)
    for cand in candidates:
        # 1. REDACT FIRST — everything downstream sees only redacted text.
        red = redact(cand.text)
        report.redactions += red.count
        cand = replace(cand, text=red.text)

        # 2. DEDUP on the redacted content (spec §9: dedup before gate).
        h = content_hash(cand.text)
        if ledger.seen(h):
            report.deduped += 1
            continue

        # 3. IMPORTANCE GATE.
        if not is_important(cand.text, threshold=threshold):
            report.gated_out += 1
            continue

        report.candidates += 1

        # 4. DISTILL (non-lossy).
        note = distiller.distill(cand)

        # 5. WRITE (skipped on dry-run; ledger untouched on dry-run).
        if dry_run:
            continue
        path = write_derived_note(note, vault_root, subdir=subdir)
        ledger.add(h)
        report.written.append(path)
    return report
```

- [ ] **Step 3: Delete the framing denylist from `src/cairn/ingest/sanitize.py`** — remove `_FRAMING_PREFIXES`, `_CONTINUED_PREFIX`, and the entire `is_framing_noise` function. Keep the module docstring's first concern, `_ANSI_RE`, `_CTRL_RE`, and `sanitize_text`. The file should end after `sanitize_text`. Update the docstring to drop the second bullet about `is_framing_noise`.

- [ ] **Step 4: Update `tests/ingest/test_sanitize.py`** — delete `test_framing_noise_detects_harness_turns` and `test_framing_noise_keeps_real_prose`, and remove `is_framing_noise` from the import (leave `from cairn.ingest.sanitize import sanitize_text`).

- [ ] **Step 5: Rewrite the structural tests in `tests/ingest/test_pipeline.py`** — replace the `Turn` import and `_transcript` helper and the framing test. New top of file + helper:

```python
import json

from cairn.ingest.dedup import DedupLedger
from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.models import IngestReport, Transcript
from cairn.ingest.pipeline import ingest_transcript

SECRET = "ghp_16C7e42F292c6912E7710c838347Ae178B4a"


def _ev(kind, text, ts="t0"):
    from pathlib import Path

    return NormalizedEvent(
        kind=kind,
        role="user",
        text=text,
        timestamp=ts,
        session_id="sess-1",
        project="proj",
        git_branch="main",
        source_path=Path("/tmp/sess-1.jsonl"),
    )


def _transcript(tmp_path) -> Transcript:
    return Transcript(
        session_id="sess-1",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "sess-1.jsonl",
        events=[
            _ev(EventKind.AUTHORED_USER, "thanks!"),  # authored but trivial -> gated out
            _ev(EventKind.AUTHORED_USER, f"We decided to always rotate the token; the old one was {SECRET}."),
            _ev(EventKind.AUTHORED_ASSISTANT, "Understood, rotating now."),  # not a candidate
        ],
    )
```

Then replace `test_pipeline_drops_harness_framing_turns` and `test_pipeline_strips_ansi_from_written_notes` with a single structural test:

```python
def test_pipeline_ingests_only_authored_user_events(tmp_path):
    """Tool results, meta injections, summaries, and assistant turns are excluded
    by KIND — no text patterns involved. The per-kind tally is reported."""
    vault = tmp_path / "vault"
    vault.mkdir()
    ledger = DedupLedger(tmp_path / "led.sha256")
    tr = Transcript(
        session_id="s",
        cwd="/Users/x/proj",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.TOOL_RESULT, "Context Usage 49.8k/1m tokens; system prompt 6.7k"),
            _ev(EventKind.META_INJECTION, "<task-notification> background task done"),
            _ev(EventKind.COMPACT_SUMMARY, "This session is being continued from a previous conversation."),
            _ev(EventKind.AUTHORED_USER, "We decided to always rebase-merge and delete the branch."),
        ],
    )
    report = ingest_transcript(tr, vault_root=vault, ledger=ledger)
    assert report.authored == 1
    assert report.candidates == 1
    assert report.event_kinds == {
        "tool_result": 1,
        "meta_injection": 1,
        "compact_summary": 1,
        "authored_user": 1,
    }
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "rebase-merge" in blob
    assert "task-notification" not in blob and "Context Usage" not in blob
```

Leave `test_pipeline_redacts_before_write_and_gates`, `test_pipeline_dedup_skips_on_second_run`, `test_pipeline_dry_run_writes_nothing`, and `test_ingest_report_to_dict_is_json_serializable` — but the `to_dict` test now also has `authored`/`event_kinds` keys; update its assertions to include `assert parsed["authored"] == 0` and `assert parsed["event_kinds"] == {}` (an `IngestReport()` built directly has those defaults). The Plan-5 export test (`test_ingest_package_exports_plan5_seams`) is unaffected.

- [ ] **Step 6: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/ -q`
Expected: all pass.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/pipeline.py src/cairn/ingest/models.py src/cairn/ingest/sanitize.py tests/ingest/ && git commit -m "feat(ingest): structural select_candidates + per-kind report; drop is_framing_noise"
```

---

## Task 5: Surface the per-kind tally in `cairn ingest`

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Append the failing test** to `tests/test_cli.py`:

```python
def test_ingest_reports_per_kind_skips(tmp_path, monkeypatch):
    import json as _j

    # a transcript with one authored user turn + one tool-result + one task-notification
    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    lines = [
        _j.dumps({"type": "user", "sessionId": "s", "cwd": "/Users/x/proj",
                  "message": {"role": "user", "content": "we decided to always rebase-merge the branch"}}),
        _j.dumps({"type": "user", "sessionId": "s", "toolUseResult": {},
                  "message": {"role": "user", "content": "tool output blah blah blah blah blah"}}),
        _j.dumps({"type": "user", "sessionId": "s", "origin": {"kind": "task-notification"},
                  "message": {"role": "user", "content": "<task-notification> done done done done"}}),
    ]
    (proj / "t.jsonl").write_text("\n".join(lines) + "\n")
    vault = tmp_path / "vault"
    r = runner.invoke(
        app,
        ["ingest", "--vault", str(vault), "--transcripts-dir", str(tmp_path / "projects"),
         "--ledger", str(tmp_path / "led.sha256")],
    )
    assert r.exit_code == 0, r.output
    assert "1 authored" in r.output
    assert "tool_result" in r.output and "meta_injection" in r.output
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k per_kind -q`
Expected: FAIL — output lacks "authored"/"tool_result".

- [ ] **Step 3: Update the `ingest` command in `src/cairn/cli.py`** — replace the totals loop + final echo (the block from `totals: dict[str, int] = {` through the closing `typer.echo(...)`):

```python
    from collections import Counter

    totals = {"authored": 0, "candidates": 0, "redactions": 0, "deduped": 0, "gated_out": 0, "written": 0}
    kinds: Counter = Counter()
    for tp in paths:
        rep = ingest_transcript(
            parse_transcript(tp),
            vault_root=vault,
            ledger=led,
            threshold=threshold,
            dry_run=dry_run,
        )
        totals["authored"] += rep.authored
        totals["candidates"] += rep.candidates
        totals["redactions"] += rep.redactions
        totals["deduped"] += rep.deduped
        totals["gated_out"] += rep.gated_out
        totals["written"] += len(rep.written)
        kinds.update(rep.event_kinds)
    prefix = "[dry-run] " if dry_run else ""
    typer.echo(
        f"{prefix}{totals['authored']} authored · {totals['candidates']} candidates · "
        f"{totals['redactions']} redactions · {totals['deduped']} deduped · "
        f"{totals['gated_out']} gated · {totals['written']} written"
    )
    skipped = {k: v for k, v in kinds.items() if k != "authored_user"}
    if skipped:
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(skipped.items(), key=lambda kv: -kv[1]))
        typer.echo(f"  skipped (non-authored): {breakdown}")
```

- [ ] **Step 4: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k 'per_kind or ingest' -q`
Expected: pass.
Regression: `cd /Users/ccf/git/agentcairn && uv run pytest -q`.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/cli.py tests/test_cli.py && git commit -m "feat(cli): surface per-kind skip tally in 'cairn ingest'"
```

---

## Task 6: Real-noise regression fixtures + fail-closed property

**Files:**
- Test: `tests/ingest/test_events.py`

- [ ] **Step 1: Append regression + property tests** to `tests/ingest/test_events.py` (these lock the empirically-verified shapes and the fail-closed guarantee end-to-end through parse):

```python
def test_real_noise_shapes_are_not_authored(tmp_path):
    """Lock the audited noise classes by their REAL structural shape — none may
    classify as AUTHORED_USER, and none may survive parse->select as a candidate."""
    import json
    from pathlib import Path

    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code, parse_transcript
    from cairn.ingest.pipeline import select_candidates

    noise = [
        {"type": "user", "origin": {"kind": "task-notification"}, "sessionId": "s",
         "message": {"role": "user", "content": "<task-notification> bg task done"}},
        {"type": "user", "isMeta": True, "sessionId": "s",
         "message": {"role": "user", "content": "Base directory for this skill: /Users/ccf/.claude/skills/x"}},
        {"type": "user", "toolUseResult": {}, "sessionId": "s",
         "message": {"role": "user", "content": "[1mContext Usage[22m 49.8k/1m tokens"}},
        {"type": "user", "isCompactSummary": True, "isVisibleInTranscriptOnly": True, "sessionId": "s",
         "message": {"role": "user", "content": "This session is being continued from a previous conversation."}},
    ]
    for obj in noise:
        assert classify_claude_code(obj) != EventKind.AUTHORED_USER

    authored = {"type": "user", "sessionId": "s", "cwd": "/Users/x/proj",
                "message": {"role": "user", "content": "we decided to always rebase-merge the branch"}}
    t = tmp_path / "mixed.jsonl"
    t.write_text("\n".join(json.dumps(o) for o in [*noise, authored]) + "\n")
    tr = parse_transcript(t)
    cands = select_candidates(tr)
    assert [c.text for c in cands] == ["we decided to always rebase-merge the branch"]
    # ANSI was stripped from the (excluded) tool-result during parse, too
    assert all("" not in e.text for e in tr.events)


def test_unknown_entry_shape_fails_closed(tmp_path):
    import json

    from cairn.ingest.locate import parse_transcript
    from cairn.ingest.pipeline import select_candidates

    # an unrecognized type, and a user row with a future/unknown injection flag we
    # don't model — the unknown-type row isn't emitted; the user row stays authored
    # ONLY if it lacks every known marker (here it has one) -> excluded.
    t = tmp_path / "weird.jsonl"
    t.write_text(
        "\n".join(
            [
                json.dumps({"type": "future-thing", "sessionId": "s", "message": {"role": "x", "content": "hi"}}),
                json.dumps({"type": "user", "isMeta": True, "sessionId": "s",
                            "message": {"role": "user", "content": "injected content here"}}),
            ]
        )
        + "\n"
    )
    assert select_candidates(parse_transcript(t)) == []
```

- [ ] **Step 2: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_events.py -q`
Expected: 5 passed.
```bash
cd /Users/ccf/git/agentcairn && git add tests/ingest/test_events.py && git commit -m "test(ingest): real-noise regression fixtures + fail-closed property"
```

---

## Task 7: Release 0.7.0

**Files:**
- Modify: `CHANGELOG.md`, `src/cairn/__init__.py`

- [ ] **Step 1: Add the CHANGELOG section** — in `CHANGELOG.md`, replace `## [Unreleased]\n` with:

```markdown
## [Unreleased]

## [0.7.0] - 2026-06-11

### Changed
- **Ingestion now selects candidates by transcript structure, not text patterns.** A new normalized `EventKind` taxonomy + a positive-identification, fail-closed Claude Code classifier (keyed on `isMeta`/`toolUseResult`/`isCompactSummary`/`isVisibleInTranscriptOnly`/`origin`) means only genuinely human-authored turns become memories. This deterministically excludes tool output, slash-command/skill injections, `<task-notification>` events, and compaction summaries — without enumerating their text. An unmapped entry type or new harness yields zero candidates (safe, loud) rather than noise. `cairn ingest` now reports a per-kind skip tally; event provenance (origin project) is preserved through the pipeline for future use.

### Removed
- The text-pattern `is_framing_noise` denylist (0.6.1/0.6.2) — subsumed by structural classification. `sanitize_text` (escape/control stripping) stays.
```

Then update the link refs at the bottom: change `[Unreleased]: …/compare/v0.6.2...HEAD` to `…/compare/v0.7.0...HEAD` and add `[0.7.0]: https://github.com/ccf/agentcairn/compare/v0.6.2...v0.7.0` above the `[0.6.2]` line.

- [ ] **Step 2: Bump the version** — in `src/cairn/__init__.py`, change `__version__ = "0.6.2"` to `__version__ = "0.7.0"`.

- [ ] **Step 3: Verify, commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q`
Expected: all pass.
```bash
cd /Users/ccf/git/agentcairn && git add CHANGELOG.md src/cairn/__init__.py && git commit -m "chore(release): 0.7.0 — structural ingestion candidate-selection"
```

(Tag/push/PyPI/GitHub-Release follow the cut-a-release ritual after the PR merges; the vault rebuild runs after the redaction fix lands, gated on the `cairn ingest --dry-run` verification.)

---

## Self-review (against the spec)

- **§ Normalized model** (`EventKind`, `NormalizedEvent`, provenance): Task 1. ✓
- **§ Classifier** (positive-ID, fail-closed, the `origin` discriminator): Task 2. ✓
- **§ Parse → events, sanitize, provenance, harness-dispatched**: Task 3 (parse) + existing `find_transcripts` harness guard. ✓
- **§ select_candidates = AUTHORED_USER; pipeline invariants preserved**: Task 4. ✓
- **§ Delete is_framing_noise; keep sanitize_text**: Task 4 Step 3–4. ✓
- **§ Observability (per-kind tally, IngestReport + CLI + --json)**: Task 4 (report `to_dict`) + Task 5 (CLI). ✓
- **§ Real-noise fixtures + fail-closed property**: Task 6. ✓
- **§ Rollout 0.7.0, no migration**: Task 7. ✓
- **§ Out of scope** (Layer B, redaction fix, #28 recall, #36 adapters): none added. ✓

**Type/name consistency:** `EventKind.{AUTHORED_USER,…,UNKNOWN}`; `NormalizedEvent{kind,role,text,timestamp,session_id,project,git_branch,source_path}`; `project_from_cwd(cwd)`; `classify_claude_code(obj)->EventKind`; `Transcript.events`; `Candidate.project`; `IngestReport.{authored,event_kinds}`; `select_candidates(transcript)->list[Candidate]`. Used identically across tasks. `Turn` fully removed (models, `__init__`, tests). No placeholders.
