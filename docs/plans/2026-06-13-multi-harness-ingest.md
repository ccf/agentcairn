# Multi-Harness Transcript Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest Codex transcripts alongside Claude Code transcripts, behind a clean `HarnessAdapter` seam that later cycles extend to Gemini/Cursor — with zero change to the downstream pipeline (redact → select → judge → consolidate → write → reindex).

**Architecture:** A new `src/cairn/ingest/harness/` package defines a `HarnessAdapter` protocol (`default_root`/`is_present`/`find`/`iter_raw`/`classify`/`to_event`), a `REGISTRY`, and helpers. The current Claude Code logic moves verbatim into `ClaudeCodeAdapter` (behavior-preserving). A `CodexAdapter` maps `rollout-*.jsonl`. `locate.py`'s `find_transcripts`/`parse_transcript` dispatch through the registry and `find_transcripts` returns `list[TranscriptRef]` (path + harness) so auto-detect can union differently-rooted harnesses. The CLI auto-detects all present harnesses, narrowable by `--harness`/`CAIRN_HARNESSES`.

**Tech Stack:** Python 3.12+, `uv` (run via `uv run pytest`), Typer CLI, dataclasses, `typing.Protocol`. Tests: pytest under `tests/ingest/`.

**Spec:** `docs/specs/2026-06-13-multi-harness-ingest-design.md`. **Branch:** `feat/36-multi-harness-ingest` (spec already committed).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cairn/ingest/events.py` | add `harness: str` to `NormalizedEvent` |
| `src/cairn/ingest/harness/__init__.py` | **new** — `HarnessAdapter` protocol, `ParseCtx`, `TranscriptRef`, `REGISTRY`, `get_adapter`, `present_harnesses` |
| `src/cairn/ingest/harness/claude_code.py` | **new** — `ClaudeCodeAdapter` + the Claude Code helpers moved verbatim |
| `src/cairn/ingest/harness/codex.py` | **new** — `CodexAdapter` (rollout JSONL, payload dispatch, tag-backstop) |
| `src/cairn/ingest/locate.py` | dispatch through registry; `find_transcripts → list[TranscriptRef]` + auto-detect; `parse_transcript(ref|Path)`; keep `encode_cwd`/`classify_claude_code`/`_extract_text` importable |
| `src/cairn/ingest/__init__.py` | export `TranscriptRef`, `get_adapter`, `present_harnesses` |
| `src/cairn/cli.py` | `sweep`/`ingest`: `--harness`, `CAIRN_HARNESSES`, auto-detect default, `--transcripts-dir` guard, `_resolve_harnesses` |
| `tests/ingest/test_harness.py` | **new** — registry/auto-detect, adapter parity, Codex fixtures, provenance |
| `tests/ingest/test_locate.py` | update `find_transcripts` tests for `TranscriptRef`; flip unknown-harness name |

---

## Task 1: Add `harness` field to `NormalizedEvent`

**Files:**
- Modify: `src/cairn/ingest/events.py:29-39`
- Modify: `src/cairn/ingest/locate.py:152-163` (the one src constructor)
- Modify: `tests/ingest/test_events.py:25`, `tests/ingest/test_pipeline.py:17`, `tests/ingest/test_pipeline.py:756` (test constructors)
- Test: `tests/ingest/test_events.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/ingest/test_events.py`:

```python
def test_normalized_event_carries_harness():
    from pathlib import Path

    from cairn.ingest.events import EventKind, NormalizedEvent

    e = NormalizedEvent(
        kind=EventKind.AUTHORED_USER,
        role="user",
        text="hi",
        timestamp=None,
        session_id="s",
        project="p",
        git_branch=None,
        source_path=Path("/tmp/x.jsonl"),
        harness="claude-code",
    )
    assert e.harness == "claude-code"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_events.py::test_normalized_event_carries_harness -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'harness'`.

- [ ] **Step 3: Add the field**

In `src/cairn/ingest/events.py`, add `harness` as the last field of `NormalizedEvent` (no default — every constructor must stamp it explicitly so a forgotten stamp is a hard error):

```python
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
    harness: str  # which harness produced this event ("claude-code", "codex")
```

- [ ] **Step 4: Update the one src constructor**

In `src/cairn/ingest/locate.py`, the `NormalizedEvent(...)` call (currently ~line 152) gains `harness="claude-code",` as its last argument. (This file is rewritten in Task 4; this keeps it valid in the meantime.)

```python
            NormalizedEvent(
                kind=kind,
                role=msg.get("role", obj["type"]),
                text=text,
                timestamp=obj.get("timestamp"),
                session_id=obj.get("sessionId") or session_id,
                project=project_from_cwd(line_cwd or cwd),
                git_branch=obj.get("gitBranch") or git_branch,
                source_path=path,
                harness="claude-code",
            )
```

- [ ] **Step 5: Update the three test constructors**

Add `harness="claude-code",` as the last argument to the `NormalizedEvent(...)` calls in `tests/ingest/test_events.py:25`, `tests/ingest/test_pipeline.py:17`, and `tests/ingest/test_pipeline.py:756`. (Read each call first; append the kwarg before the closing paren.)

- [ ] **Step 6: Run the full ingest test suite**

Run: `uv run pytest tests/ingest/ -q`
Expected: PASS (all existing tests + the new one). If any other `NormalizedEvent(` site appears, add `harness=...` there too.

- [ ] **Step 7: Commit**

```bash
git add src/cairn/ingest/events.py src/cairn/ingest/locate.py tests/ingest/test_events.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): carry harness on NormalizedEvent (#36)"
```

---

## Task 2: Harness package — protocol, registry, helpers

**Files:**
- Create: `src/cairn/ingest/harness/__init__.py`
- Test: `tests/ingest/test_harness.py`

This task defines the seam and helpers. `REGISTRY` starts **empty**; adapters register in Tasks 3 and 5. Tests use fake adapters inserted into `REGISTRY` via monkeypatch.

- [ ] **Step 1: Write the failing tests**

Create `tests/ingest/test_harness.py`:

```python
# tests/ingest/test_harness.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from cairn.ingest.events import EventKind, NormalizedEvent
from cairn.ingest.harness import (
    ParseCtx,
    TranscriptRef,
    get_adapter,
    present_harnesses,
)
from cairn.ingest import harness as harness_pkg


class _FakeAdapter:
    def __init__(self, name, root, files=()):
        self.name = name
        self._root = root
        self._files = list(files)

    def default_root(self):
        return self._root

    def is_present(self):
        return self._root.is_dir()

    def find(self, *, root, project):
        return list(self._files)

    def iter_raw(self, path):
        return iter(())

    def classify(self, raw):
        return EventKind.UNKNOWN

    def to_event(self, raw, kind, ctx):
        return None


def test_get_adapter_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("definitely-not-a-harness")


def test_get_adapter_returns_registered(monkeypatch, tmp_path):
    fake = _FakeAdapter("fake", tmp_path)
    monkeypatch.setitem(harness_pkg.REGISTRY, "fake", fake)
    assert get_adapter("fake") is fake


def test_present_harnesses_filters_by_root(monkeypatch, tmp_path):
    present = _FakeAdapter("present", tmp_path)  # tmp_path exists
    absent = _FakeAdapter("absent", tmp_path / "nope")  # missing dir
    monkeypatch.setitem(harness_pkg.REGISTRY, "present", present)
    monkeypatch.setitem(harness_pkg.REGISTRY, "absent", absent)
    names = [a.name for a in present_harnesses(["present", "absent"])]
    assert names == ["present"]


def test_present_harnesses_unknown_name_raises(monkeypatch):
    with pytest.raises(ValueError):
        present_harnesses(["definitely-not-a-harness"])


def test_parsectx_and_ref_shapes(tmp_path):
    ref = TranscriptRef(path=tmp_path / "a.jsonl", harness="fake")
    assert ref.path.name == "a.jsonl" and ref.harness == "fake"
    ctx = ParseCtx(path=tmp_path / "a.jsonl")
    assert ctx.session_id is None and ctx.cwd is None and ctx.git_branch is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_harness.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.harness'`.

- [ ] **Step 3: Create the package**

Create `src/cairn/ingest/harness/__init__.py`:

```python
# src/cairn/ingest/harness/__init__.py
# SPDX-License-Identifier: Apache-2.0
"""Harness adapter seam. One adapter per agent harness; everything
harness-specific (transcript location, container format, structural
classification) lives behind a HarnessAdapter. The ingest pipeline downstream
consumes NormalizedEvents identically regardless of origin.

Classification stays positive-identification and fail-closed per harness: a row
not affirmatively recognized as authored user prose never becomes a candidate."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from cairn.ingest.events import EventKind, NormalizedEvent


@dataclass(frozen=True)
class TranscriptRef:
    """A transcript path tagged with the harness that produced it, so a
    cross-harness (auto-detect) sweep can route each path back to its adapter."""

    path: Path
    harness: str


@dataclass
class ParseCtx:
    """Mutable per-file context an adapter fills in as it scans a transcript:
    session id / cwd / git branch discovered from a header row or per-row fields.
    `path` is the transcript path (for NormalizedEvent.source_path)."""

    path: Path
    session_id: str | None = None
    cwd: str | None = None
    git_branch: str | None = None


@runtime_checkable
class HarnessAdapter(Protocol):
    name: str

    def default_root(self) -> Path: ...
    def is_present(self) -> bool: ...
    def find(self, *, root: Path | None, project: str | None) -> list[Path]: ...
    def iter_raw(self, path: Path) -> Iterator[dict]: ...
    def classify(self, raw: dict) -> EventKind: ...
    def to_event(
        self, raw: dict, kind: EventKind, ctx: ParseCtx
    ) -> NormalizedEvent | None: ...


# Populated by adapter modules at import time (see _register below).
REGISTRY: dict[str, HarnessAdapter] = {}


def _register(adapter: HarnessAdapter) -> None:
    REGISTRY[adapter.name] = adapter


def get_adapter(name: str) -> HarnessAdapter:
    """Resolve a harness name to its adapter; ValueError lists known names."""
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unsupported harness: {name!r} (have: {sorted(REGISTRY)})"
        ) from None


def present_harnesses(selected: list[str] | None = None) -> list[HarnessAdapter]:
    """Adapters whose root currently exists. `selected` (from --harness /
    CAIRN_HARNESSES) narrows and validates names; None means 'all registered'.
    Unknown names raise ValueError (via get_adapter)."""
    names = selected if selected is not None else list(REGISTRY)
    return [a for a in (get_adapter(n) for n in names) if a.is_present()]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_harness.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/harness/__init__.py tests/ingest/test_harness.py
git commit -m "feat(ingest): HarnessAdapter seam + registry (#36)"
```

---

## Task 3: `ClaudeCodeAdapter` (move Claude Code logic verbatim)

**Files:**
- Create: `src/cairn/ingest/harness/claude_code.py`
- Modify: `src/cairn/ingest/harness/__init__.py` (import to register)
- Test: `tests/ingest/test_harness.py`

The Claude Code classification/extraction logic moves verbatim from `locate.py`. `locate.py` itself is rewired in Task 4; for now both coexist (the adapter is the new home of truth).

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_harness.py`:

```python
def test_claude_code_adapter_classify_and_event(tmp_path):
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter()
    assert a.name == "claude-code"
    raw = {
        "type": "user",
        "sessionId": "sess-1",
        "cwd": "/Users/x/proj",
        "gitBranch": "main",
        "timestamp": "2026-06-08T10:00:00Z",
        "message": {"role": "user", "content": "fix the bug"},
    }
    kind = a.classify(raw)
    assert kind == EventKind.AUTHORED_USER
    ctx = ParseCtx(path=tmp_path / "s.jsonl")
    ev = a.to_event(raw, kind, ctx)
    assert ev.text == "fix the bug"
    assert ev.kind == EventKind.AUTHORED_USER
    assert ev.harness == "claude-code"
    assert ev.project == "proj"
    assert ctx.session_id == "sess-1" and ctx.cwd == "/Users/x/proj"


def test_claude_code_adapter_skips_textless_row(tmp_path):
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter()
    raw = {"type": "user", "sessionId": "skipme", "message": {"role": "user", "content": ""}}
    ctx = ParseCtx(path=tmp_path / "s.jsonl")
    assert a.to_event(raw, a.classify(raw), ctx) is None
    assert ctx.session_id is None  # a skipped row must not set provenance


def test_claude_code_adapter_registered():
    assert get_adapter("claude-code").name == "claude-code"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_harness.py -k claude_code -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.harness.claude_code'`.

- [ ] **Step 3: Create the adapter**

Create `src/cairn/ingest/harness/claude_code.py` (logic lifted verbatim from the current `locate.py`):

```python
# src/cairn/ingest/harness/claude_code.py
# SPDX-License-Identifier: Apache-2.0
"""Claude Code adapter: ~/.claude/projects/<encoded-cwd>/<session>.jsonl.

Classification is positive-identification and fail-closed: a user turn is
AUTHORED_USER only when it carries NONE of the harness's injection markers."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_CLAUDE_ROOT = Path.home() / ".claude" / "projects"
_CONTENT_TYPES = {"user", "assistant"}

# Backstop for legacy transcripts (Claude Code <=2.1.150): injected slash-command
# and tool rows carried NO structural flags, so they are structurally identical to
# authored prose. Structure stays primary; this prefix list is ONLY for rows with
# no markers, and lists the harness's own injection tags — never user vocabulary.
_LEGACY_TAG_PREFIXES = (
    "<command-",
    "<local-command",
    "<bash-input",
    "<bash-stdout",
    "<bash-stderr",
    "<task-notification",
    "<system-reminder",
    "<user-prompt-submit-hook",
)


def encode_cwd(cwd: str) -> str:
    """Claude Code encodes a project dir by replacing every '/' with '-'.
    e.g. '/Users/ccf/git/agentcairn' -> '-Users-ccf-git-agentcairn'. Trailing
    slashes are stripped first, so '/Users/x/proj/' maps to the same dir as
    '/Users/x/proj'."""
    normalized = cwd.rstrip("/") or "/"
    return normalized.replace("/", "-")


def _extract_text(content: object) -> str:
    """User content is a str; assistant content is a list of blocks. Keep only
    plain text (drop thinking/tool_use/tool_result). Terminal escape sequences
    and stray control bytes are stripped so they never reach the vault."""
    if isinstance(content, str):
        return sanitize_text(content).strip()
    if isinstance(content, list):
        parts = [
            b["text"]
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return sanitize_text("\n".join(parts)).strip()
    return ""


def classify_claude_code(obj: dict) -> EventKind:
    """Positive-ID, fail-closed classification of a raw Claude Code JSONL entry.
    Order matters: compact-summary first, then tool results, then meta/injected.
    A tag-prefix backstop covers legacy transcripts whose injected rows predate
    the structural flags."""
    t = obj.get("type")
    if t == "user":
        if obj.get("isCompactSummary"):
            return EventKind.COMPACT_SUMMARY
        if "toolUseResult" in obj:
            return EventKind.TOOL_RESULT
        if obj.get("isMeta") or obj.get("isVisibleInTranscriptOnly") or obj.get("origin"):
            return EventKind.META_INJECTION
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, str) and sanitize_text(content).lstrip().startswith(
            _LEGACY_TAG_PREFIXES
        ):
            return EventKind.META_INJECTION
        return EventKind.AUTHORED_USER
    if t == "assistant":
        return EventKind.AUTHORED_ASSISTANT
    if t == "system":
        return EventKind.SYSTEM
    return EventKind.UNKNOWN


class ClaudeCodeAdapter:
    name = "claude-code"

    def default_root(self) -> Path:
        return _CLAUDE_ROOT

    def is_present(self) -> bool:
        return self.default_root().is_dir()

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        base = Path(root) if root is not None else self.default_root()
        if not base.is_dir():
            return []
        if project is not None:
            dirs = [base / encode_cwd(project)]
        else:
            dirs = [d for d in base.iterdir() if d.is_dir()]
        files = [f for d in dirs if d.is_dir() for f in d.glob("*.jsonl")]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def iter_raw(self, path: Path) -> Iterator[dict]:
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
            yield obj

    def classify(self, raw: dict) -> EventKind:
        return classify_claude_code(raw)

    def to_event(
        self, raw: dict, kind: EventKind, ctx: ParseCtx
    ) -> NormalizedEvent | None:
        msg = raw.get("message")
        if not isinstance(msg, dict):
            return None
        text = _extract_text(msg.get("content"))
        if not text:
            return None  # a skipped row must not set provenance
        if ctx.session_id is None:
            ctx.session_id = raw.get("sessionId")
        line_cwd = raw.get("cwd")
        if ctx.cwd is None:
            ctx.cwd = line_cwd
        if ctx.git_branch is None:
            ctx.git_branch = raw.get("gitBranch")
        return NormalizedEvent(
            kind=kind,
            role=msg.get("role", raw["type"]),
            text=text,
            timestamp=raw.get("timestamp"),
            session_id=raw.get("sessionId") or ctx.session_id or ctx.path.stem,
            project=project_from_cwd(line_cwd or ctx.cwd),
            git_branch=raw.get("gitBranch") or ctx.git_branch,
            source_path=ctx.path,
            harness=self.name,
        )
```

- [ ] **Step 4: Register the adapter**

At the bottom of `src/cairn/ingest/harness/__init__.py`, register it (after the helpers; import inside the module to avoid a circular import at module top):

```python
def _bootstrap_registry() -> None:
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter

    _register(ClaudeCodeAdapter())


_bootstrap_registry()
```

(`claude_code.py` imports `ParseCtx` from `cairn.ingest.harness`; calling `_bootstrap_registry()` at the end of `__init__` — after `ParseCtx`/`_register` are defined — avoids the cycle.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_harness.py -q`
Expected: PASS (all harness tests, including the new claude-code ones).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/ingest/harness/claude_code.py src/cairn/ingest/harness/__init__.py tests/ingest/test_harness.py
git commit -m "feat(ingest): ClaudeCodeAdapter behind the seam (#36)"
```

---

## Task 4: Rewire `locate.py` to dispatch through the registry

**Files:**
- Modify: `src/cairn/ingest/locate.py` (full rewrite)
- Modify: `tests/ingest/test_locate.py` (find_transcripts tests → `TranscriptRef`; flip unknown-harness name)
- Test: `tests/ingest/test_locate.py`, `tests/ingest/test_harness.py`

`find_transcripts` now returns `list[TranscriptRef]`. `parse_transcript` accepts a `TranscriptRef` or a bare `Path` (back-compat → claude-code). `encode_cwd`/`classify_claude_code`/`_extract_text` re-export from the adapter so existing imports keep working.

- [ ] **Step 1: Update the existing `find_transcripts` tests for `TranscriptRef`**

In `tests/ingest/test_locate.py`, change the three project-filter assertions from `p.name`/`p` to `.path`:

```python
def test_find_transcripts_project_filter_tolerates_trailing_slash(tmp_path):
    proj = tmp_path / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text("{}\n")
    found = find_transcripts(root=tmp_path, project="/Users/x/proj/")
    assert [r.path.name for r in found] == ["a.jsonl"]
    assert all(r.harness == "claude-code" for r in found)


def test_find_transcripts_empty_when_missing(tmp_path):
    assert find_transcripts(root=tmp_path / "nope") == []


def test_find_transcripts_filters_by_project(tmp_path):
    proj = tmp_path / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text("{}\n")
    other = tmp_path / "-Users-x-other"
    other.mkdir(parents=True)
    (other / "b.jsonl").write_text("{}\n")
    found = find_transcripts(root=tmp_path, project="/Users/x/proj")
    assert [r.path.name for r in found] == ["a.jsonl"]
```

- [ ] **Step 2: Flip the unknown-harness test (Codex is now supported)**

Replace `test_parse_transcript_unknown_harness_raises` in `tests/ingest/test_locate.py`:

```python
def test_find_transcripts_unknown_harness_raises():
    import pytest

    with pytest.raises(ValueError):
        find_transcripts(harness="definitely-not-a-harness")
```

- [ ] **Step 3: Add an auto-detect union test**

Append to `tests/ingest/test_harness.py`:

```python
def test_find_transcripts_auto_detect_unions(monkeypatch, tmp_path):
    from cairn.ingest.locate import find_transcripts
    from cairn.ingest import harness as hp

    a_dir = tmp_path / "a"
    a_dir.mkdir()
    fa = a_dir / "1.jsonl"
    fa.write_text("{}\n")
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    fb = b_dir / "2.jsonl"
    fb.write_text("{}\n")

    monkeypatch.setitem(hp.REGISTRY, "ha", _FakeAdapter("ha", a_dir, files=[fa]))
    monkeypatch.setitem(hp.REGISTRY, "hb", _FakeAdapter("hb", b_dir, files=[fb]))

    refs = find_transcripts(harness=None, harnesses=["ha", "hb"])
    assert {r.path.name for r in refs} == {"1.jsonl", "2.jsonl"}
    assert {r.harness for r in refs} == {"ha", "hb"}
```

- [ ] **Step 4: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_locate.py tests/ingest/test_harness.py -q`
Expected: FAIL — `find_transcripts` still returns `Path` (so `.path`/`harness=None`/`harnesses=` attributes/kwargs error), and `find_transcripts(harness="definitely-not-a-harness")` does not yet raise via the registry.

- [ ] **Step 5: Rewrite `locate.py`**

Replace the entire contents of `src/cairn/ingest/locate.py`:

```python
# src/cairn/ingest/locate.py
# SPDX-License-Identifier: Apache-2.0
"""Locate and parse harness transcripts out-of-band.

Dispatch-shaped: a HarnessAdapter (cairn.ingest.harness) owns each harness's
transcript location, container format, and structural classification. This
module is the stable public entry point — find_transcripts() returns
TranscriptRefs (path + harness) and parse_transcript() routes each to its
adapter. Transcripts are append-only; corrupt/partial lines are skipped."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from cairn.ingest.harness import (
    ParseCtx,
    TranscriptRef,
    get_adapter,
    present_harnesses,
)

# Re-exports for back-compat (tests and callers import these from locate).
from cairn.ingest.harness.claude_code import (  # noqa: F401
    classify_claude_code,
    encode_cwd,
)
from cairn.ingest.models import Transcript


def find_transcripts(
    *,
    harness: str | None = "claude-code",
    root: Path | None = None,
    project: str | None = None,
    harnesses: list[str] | None = None,
) -> list[TranscriptRef]:
    """Return transcript references newest-first.

    - harness=<name> (default "claude-code"): that single harness; `root`
      overrides its default location.
    - harness=None: auto-detect — union of every present harness (or those named
      in `harnesses`). `root` is ignored in auto-detect mode.
    A missing root yields no refs for that harness (graceful, never raises)."""
    if harness is not None:
        adapter = get_adapter(harness)  # ValueError on unknown name
        return [
            TranscriptRef(path=p, harness=adapter.name)
            for p in adapter.find(root=root, project=project)
        ]
    refs: list[TranscriptRef] = []
    for adapter in present_harnesses(harnesses):
        refs += [
            TranscriptRef(path=p, harness=adapter.name)
            for p in adapter.find(root=None, project=project)
        ]
    refs.sort(key=lambda r: r.path.stat().st_mtime, reverse=True)
    return refs


def parse_transcript(ref: TranscriptRef | Path, *, harness: str = "claude-code") -> Transcript:
    """Parse a transcript into a Transcript of NormalizedEvents via its adapter.
    Accepts a TranscriptRef (carries its harness) or a bare Path (back-compat;
    defaults to `harness`). Skips bookkeeping and malformed lines; each content
    row is classified structurally and sanitized; provenance is preserved."""
    if isinstance(ref, TranscriptRef):
        path, name = ref.path, ref.harness
    else:
        path, name = ref, harness
    adapter = get_adapter(name)
    ctx = ParseCtx(path=path)
    events = []
    kind_counts: Counter = Counter()
    for raw in adapter.iter_raw(path):
        kind = adapter.classify(raw)
        kind_counts[kind.value] += 1
        ev = adapter.to_event(raw, kind, ctx)
        if ev is not None:
            events.append(ev)
    return Transcript(
        session_id=ctx.session_id or path.stem,
        cwd=ctx.cwd,
        git_branch=ctx.git_branch,
        path=path,
        events=events,
        kind_counts=dict(kind_counts),
    )
```

- [ ] **Step 6: Run the full locate + harness suites**

Run: `uv run pytest tests/ingest/test_locate.py tests/ingest/test_harness.py -q`
Expected: PASS. The classification/parse tests (`test_classify_*`, `test_parse_transcript_*`, `test_session_id_*`) pass unchanged — the behavior-preservation proof. The `find_transcripts` tests pass with `.path`.

- [ ] **Step 7: Commit**

```bash
git add src/cairn/ingest/locate.py tests/ingest/test_locate.py tests/ingest/test_harness.py
git commit -m "feat(ingest): dispatch find/parse through adapter registry (#36)"
```

---

## Task 5: `CodexAdapter`

**Files:**
- Create: `src/cairn/ingest/harness/codex.py`
- Modify: `src/cairn/ingest/harness/__init__.py` (register Codex)
- Test: `tests/ingest/test_harness.py`

Codex layout (verified against real `rollout-*.jsonl`): `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`, one JSON object per line `{type, payload, timestamp}`. Top-level types seen: `session_meta` (has `id`, `cwd`), `turn_context` (has `cwd`), `event_msg` (UI noise), `compacted`, `response_item`. `response_item.payload` has `{type, role, content}`: tool calls (role None), `reasoning`, `message`+`assistant`(output_text), `message`+`developer`(input_text), `message`+`user`(input_text). Genuine user turns are laced with injected `# AGENTS.md instructions` / `<INSTRUCTIONS>` blocks that the tag-backstop demotes.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_harness.py`:

```python
def _codex_line(type_, payload):
    import json

    return json.dumps({"type": type_, "payload": payload, "timestamp": "2026-03-08T13:35:29Z"})


def _msg(role, text, block_type):
    return {"type": "message", "role": role, "content": [{"type": block_type, "text": text}]}


def test_codex_adapter_classifies_each_kind():
    from cairn.ingest.harness.codex import CodexAdapter

    a = CodexAdapter()
    assert a.name == "codex"
    C = lambda p: a.classify({"type": "response_item", "payload": p})
    assert C({"type": "function_call"}) == EventKind.TOOL_RESULT
    assert C({"type": "function_call_output"}) == EventKind.TOOL_RESULT
    assert C({"type": "custom_tool_call"}) == EventKind.TOOL_RESULT
    assert C({"type": "web_search_call"}) == EventKind.TOOL_RESULT
    assert C({"type": "reasoning"}) == EventKind.AUTHORED_ASSISTANT
    assert C(_msg("assistant", "done", "output_text")) == EventKind.AUTHORED_ASSISTANT
    assert C(_msg("developer", "<permissions instructions>x", "input_text")) == EventKind.META_INJECTION
    assert C(_msg("user", "Review the code base please", "input_text")) == EventKind.AUTHORED_USER
    # injected AGENTS.md / INSTRUCTIONS blocks arrive as role=user -> tag-backstop demotes them
    assert C(_msg("user", "# AGENTS.md instructions for /repo", "input_text")) == EventKind.META_INJECTION
    assert C(_msg("user", "<INSTRUCTIONS>\n# Primer", "input_text")) == EventKind.META_INJECTION
    assert a.classify({"type": "compacted", "payload": {}}) == EventKind.COMPACT_SUMMARY
    assert a.classify({"type": "session_meta", "payload": {}}) == EventKind.SYSTEM
    assert a.classify({"type": "turn_context", "payload": {}}) == EventKind.SYSTEM
    assert a.classify({"type": "event_msg", "payload": {}}) == EventKind.SYSTEM
    assert a.classify({"type": "weird_future_type", "payload": {}}) == EventKind.UNKNOWN


def test_codex_adapter_parses_session_and_user_turn(tmp_path):
    from cairn.ingest.harness.codex import CodexAdapter
    from cairn.ingest.locate import parse_transcript

    day = tmp_path / "2026" / "03" / "08"
    day.mkdir(parents=True)
    f = day / "rollout-2026-03-08T09-35-29-abc.jsonl"
    f.write_text(
        "\n".join(
            [
                _codex_line("session_meta", {"id": "sess-codex", "cwd": "/Users/x/insights"}),
                _codex_line("turn_context", {"cwd": "/Users/x/insights"}),
                _codex_line("event_msg", {"foo": "bar"}),  # UI noise -> no event
                _codex_line("response_item", _msg("user", "# AGENTS.md instructions", "input_text")),
                _codex_line("response_item", _msg("developer", "<permissions instructions>", "input_text")),
                _codex_line("response_item", _msg("user", "Review the roadmap and advise", "input_text")),
                _codex_line("response_item", _msg("assistant", "Here is my take.", "output_text")),
                _codex_line("response_item", {"type": "function_call", "name": "shell"}),
            ]
        )
        + "\n"
    )
    tr = parse_transcript(TranscriptRef(path=f, harness="codex"))
    assert tr.session_id == "sess-codex"
    assert tr.cwd == "/Users/x/insights"
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == ["Review the roadmap and advise"]
    assert all(e.harness == "codex" for e in tr.events)
    assert authored[0].project == "insights"


def test_codex_adapter_find_rglobs_and_filters_project(tmp_path):
    from cairn.ingest.harness.codex import CodexAdapter

    a = CodexAdapter()
    day = tmp_path / "2026" / "03" / "08"
    day.mkdir(parents=True)
    keep = day / "rollout-keep.jsonl"
    keep.write_text(_codex_line("session_meta", {"id": "s1", "cwd": "/Users/x/insights"}) + "\n")
    drop = day / "rollout-drop.jsonl"
    drop.write_text(_codex_line("session_meta", {"id": "s2", "cwd": "/Users/x/other"}) + "\n")
    assert {p.name for p in a.find(root=tmp_path, project=None)} == {"rollout-keep.jsonl", "rollout-drop.jsonl"}
    assert [p.name for p in a.find(root=tmp_path, project="/Users/x/insights")] == ["rollout-keep.jsonl"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_harness.py -k codex -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.harness.codex'`.

- [ ] **Step 3: Create the adapter**

Create `src/cairn/ingest/harness/codex.py`:

```python
# src/cairn/ingest/harness/codex.py
# SPDX-License-Identifier: Apache-2.0
"""Codex adapter: ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl.

Each line is {type, payload, timestamp}. Conversational content lives in
`response_item` rows; `session_meta`/`turn_context` seed session/cwd; `event_msg`
is UI noise. Classification is positive-ID and fail-closed: a role=user message
is AUTHORED_USER only when it does NOT start with an injected harness block
(# AGENTS.md, <INSTRUCTIONS>, ...) — Codex laces real user turns with those."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_CODEX_ROOT = Path.home() / ".codex" / "sessions"

# response_item payload.type values that are tool I/O (role is None).
_TOOL_TYPES = {
    "function_call",
    "function_call_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "web_search_call",
}

# Top-level types that only seed context / are UI bookkeeping (never candidates).
_BOOKKEEPING_TYPES = {"session_meta", "turn_context", "event_msg"}

# Tag-backstop: blocks Codex injects into role=user messages. Positive-ID prose
# only — anything starting with one of these is harness-injected, not authored.
_CODEX_TAG_PREFIXES = (
    "# AGENTS.md",
    "<INSTRUCTIONS>",
    "<turn_aborted",
    "<user_instructions",
    "<environment_context",
)


def _payload(raw: dict) -> dict:
    p = raw.get("payload")
    return p if isinstance(p, dict) else {}


def _extract_codex_text(payload: dict) -> str:
    """Join the input_text/output_text blocks of a Codex message payload,
    sanitized. Other block types are dropped."""
    blocks = payload.get("content")
    if not isinstance(blocks, list):
        return ""
    parts = [
        b["text"]
        for b in blocks
        if isinstance(b, dict)
        and b.get("type") in ("input_text", "output_text", "text")
        and isinstance(b.get("text"), str)
    ]
    return sanitize_text("\n".join(parts)).strip()


class CodexAdapter:
    name = "codex"

    def default_root(self) -> Path:
        return _CODEX_ROOT

    def is_present(self) -> bool:
        return self.default_root().is_dir()

    def _session_cwd(self, path: Path) -> str | None:
        """Read the session_meta cwd from a transcript's header (cheap: stops at
        the first session_meta line). None if absent/unreadable."""
        try:
            for line in path.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(obj, dict) and obj.get("type") == "session_meta":
                    return _payload(obj).get("cwd")
        except OSError:
            return None
        return None

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        base = Path(root) if root is not None else self.default_root()
        if not base.is_dir():
            return []
        files = list(base.rglob("rollout-*.jsonl"))
        if project is not None:
            target = project.rstrip("/") or "/"
            files = [f for f in files if (self._session_cwd(f) or "").rstrip("/") == target]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def iter_raw(self, path: Path) -> Iterator[dict]:
        for line in path.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue  # partial/corrupt line — transcripts are append-only
            if not isinstance(obj, dict):
                continue
            t = obj.get("type")
            if t == "event_msg":
                continue  # UI/turn bookkeeping — carries no ctx and no candidate
            if t == "response_item" or t in ("session_meta", "turn_context", "compacted"):
                yield obj

    def classify(self, raw: dict) -> EventKind:
        t = raw.get("type")
        if t == "compacted":
            return EventKind.COMPACT_SUMMARY
        if t in _BOOKKEEPING_TYPES:
            return EventKind.SYSTEM
        if t == "response_item":
            p = _payload(raw)
            pt = p.get("type")
            if pt in _TOOL_TYPES:
                return EventKind.TOOL_RESULT
            if pt == "reasoning":
                return EventKind.AUTHORED_ASSISTANT
            if pt == "message":
                role = p.get("role")
                if role == "assistant":
                    return EventKind.AUTHORED_ASSISTANT
                if role == "developer":
                    return EventKind.META_INJECTION
                if role == "user":
                    text = _extract_codex_text(p).lstrip()
                    if text.startswith(_CODEX_TAG_PREFIXES):
                        return EventKind.META_INJECTION
                    return EventKind.AUTHORED_USER
            return EventKind.UNKNOWN
        return EventKind.UNKNOWN

    def to_event(
        self, raw: dict, kind: EventKind, ctx: ParseCtx
    ) -> NormalizedEvent | None:
        t = raw.get("type")
        if t == "session_meta":
            p = _payload(raw)
            if ctx.session_id is None:
                ctx.session_id = p.get("id")
            if ctx.cwd is None:
                ctx.cwd = p.get("cwd")
            return None
        if t == "turn_context":
            if ctx.cwd is None:
                ctx.cwd = _payload(raw).get("cwd")
            return None
        if t != "response_item":
            return None  # compacted: counted in kind_counts, not a candidate
        p = _payload(raw)
        if p.get("type") != "message":
            return None  # reasoning/tool I/O retained in counts, no candidate text
        text = _extract_codex_text(p)
        if not text:
            return None
        return NormalizedEvent(
            kind=kind,
            role=p.get("role") or "user",
            text=text,
            timestamp=raw.get("timestamp"),
            session_id=ctx.session_id or ctx.path.stem,
            project=project_from_cwd(ctx.cwd),
            git_branch=None,  # Codex transcripts carry no git branch
            source_path=ctx.path,
            harness=self.name,
        )
```

- [ ] **Step 4: Register Codex**

In `src/cairn/ingest/harness/__init__.py`, extend `_bootstrap_registry`:

```python
def _bootstrap_registry() -> None:
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter
    from cairn.ingest.harness.codex import CodexAdapter

    _register(ClaudeCodeAdapter())
    _register(CodexAdapter())


_bootstrap_registry()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_harness.py -q`
Expected: PASS (all harness tests including Codex). Note the assistant-message and reasoning rows are retained in the event stream with their kinds but only the genuine user turn is `AUTHORED_USER`.

- [ ] **Step 6: Commit**

```bash
git add src/cairn/ingest/harness/codex.py src/cairn/ingest/harness/__init__.py tests/ingest/test_harness.py
git commit -m "feat(ingest): CodexAdapter — rollout JSONL + tag-backstop (#36)"
```

---

## Task 6: CLI — `--harness`, `CAIRN_HARNESSES`, auto-detect

**Files:**
- Modify: `src/cairn/cli.py` (`sweep` ~542-612, `ingest` ~652-724; add `_resolve_harnesses` helper near `_default_index` ~117)
- Test: `tests/test_cli.py` (or `tests/ingest/test_harness.py` for the helper)

Resolution order: `--harness` flag > `CAIRN_HARNESSES` env > None (auto-detect all present). `--transcripts-dir` is only valid when exactly one harness is explicitly selected.

- [ ] **Step 1: Write the failing test for the resolver**

Append to `tests/ingest/test_harness.py`:

```python
def test_resolve_harnesses_precedence():
    from cairn.cli import _resolve_harnesses

    # explicit flag wins, comma-split + trimmed
    assert _resolve_harnesses("claude-code, codex", {"CAIRN_HARNESSES": "codex"}) == ["claude-code", "codex"]
    # env used when no flag
    assert _resolve_harnesses(None, {"CAIRN_HARNESSES": "codex"}) == ["codex"]
    # nothing -> None (auto-detect all present)
    assert _resolve_harnesses(None, {}) is None
    # empty/whitespace flag -> treated as unset
    assert _resolve_harnesses("  ", {}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_harness.py::test_resolve_harnesses_precedence -v`
Expected: FAIL — `ImportError: cannot import name '_resolve_harnesses' from 'cairn.cli'`.

- [ ] **Step 3: Add the resolver helper**

In `src/cairn/cli.py`, after `_default_index` (~line 117), add:

```python
def _resolve_harnesses(
    harness_opt: str | None, env: Mapping[str, str]
) -> list[str] | None:
    """Resolve which harnesses to ingest. --harness flag wins, else
    CAIRN_HARNESSES, else None (auto-detect every present harness). A comma list
    is split and trimmed; an all-whitespace/empty value is treated as unset."""
    raw = harness_opt if (harness_opt and harness_opt.strip()) else env.get("CAIRN_HARNESSES")
    if not raw or not raw.strip():
        return None
    return [h.strip() for h in raw.split(",") if h.strip()]
```

Add `from collections.abc import Mapping` to the imports at the top of `cli.py` if not already present.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_harness.py::test_resolve_harnesses_precedence -v`
Expected: PASS.

- [ ] **Step 5: Wire `sweep` to auto-detect**

In `src/cairn/cli.py`, add a `--harness` option to `sweep` (after the `project` option):

```python
    harness: str = typer.Option(
        None,
        "--harness",
        help="Comma list of harnesses to ingest (default: CAIRN_HARNESSES or "
        "auto-detect every present harness, e.g. 'claude-code,codex').",
    ),
```

Replace the transcript-discovery lines in `sweep` (currently `paths = find_transcripts(root=transcripts_dir, project=project)` / `transcripts = [parse_transcript(tp) for tp in paths]`) with:

```python
    selected = _resolve_harnesses(harness, cairn_env())
    if transcripts_dir is not None and (selected is None or len(selected) != 1):
        raise typer.BadParameter("--transcripts-dir requires exactly one --harness")
    if transcripts_dir is not None:
        refs = find_transcripts(harness=selected[0], root=transcripts_dir, project=project)
    else:
        refs = find_transcripts(harness=None, harnesses=selected, project=project)
    transcripts = [parse_transcript(ref) for ref in refs]
```

- [ ] **Step 6: Wire `ingest` the same way**

In `src/cairn/cli.py`, add the identical `--harness` option to `ingest`, and replace its `paths = find_transcripts(root=transcripts_dir, project=project)` / `if not paths:` / `transcripts = [parse_transcript(tp) for tp in paths]` block with:

```python
    selected = _resolve_harnesses(harness, cairn_env())
    if transcripts_dir is not None and (selected is None or len(selected) != 1):
        raise typer.BadParameter("--transcripts-dir requires exactly one --harness")
    if transcripts_dir is not None:
        refs = find_transcripts(harness=selected[0], root=transcripts_dir, project=project)
    else:
        refs = find_transcripts(harness=None, harnesses=selected, project=project)
    if not refs:
        typer.echo("No transcripts found.")
        return
    transcripts = [parse_transcript(ref) for ref in refs]
```

- [ ] **Step 7: Write a CLI end-to-end test**

Add `test_sweep_auto_detects_codex` to `tests/test_cli.py` (mirror the existing CliRunner pattern in that file; if `tests/test_cli.py` does not exist, create it). It points `CAIRN_HARNESSES` at codex via a fake root and asserts the command runs. Use the embedder `fake` and a tmp vault:

```python
def test_transcripts_dir_requires_single_harness(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from cairn.cli import app

    monkeypatch.delenv("CAIRN_HARNESSES", raising=False)
    vault = tmp_path / "vault"
    vault.mkdir()
    res = CliRunner().invoke(
        app,
        ["sweep", "--vault", str(vault), "--transcripts-dir", str(tmp_path), "--embedder", "fake"],
    )
    assert res.exit_code != 0
    assert "exactly one --harness" in res.output
```

- [ ] **Step 8: Run the CLI + full ingest suite**

Run: `uv run pytest tests/test_cli.py tests/ingest/ -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py tests/ingest/test_harness.py
git commit -m "feat(cli): --harness + auto-detect all present harnesses (#36)"
```

---

## Task 7: Exports, docs, full verification

**Files:**
- Modify: `src/cairn/ingest/__init__.py`
- Modify: `README.md`, `CLAUDE.md` (mention Codex support)
- Test: full suite

- [ ] **Step 1: Export the new public types**

In `src/cairn/ingest/__init__.py`, add the import and `__all__` entries:

```python
from cairn.ingest.harness import TranscriptRef, get_adapter, present_harnesses
from cairn.ingest.locate import encode_cwd, find_transcripts, parse_transcript
```

Add `"TranscriptRef"`, `"get_adapter"`, `"present_harnesses"` to `__all__` (keep it alphabetized as the file already is).

- [ ] **Step 2: Verify the package imports cleanly**

Run: `uv run python -c "from cairn.ingest import TranscriptRef, get_adapter, present_harnesses, find_transcripts; print(sorted(get_adapter('codex').name for _ in [0]))"`
Expected: prints `['codex']` with no ImportError.

- [ ] **Step 3: Update README and CLAUDE.md**

In `README.md`, find the capture/ingest description that names Claude Code transcripts and note Codex is now supported (and that sweep auto-detects present harnesses). In `CLAUDE.md`, update the "Capture pipeline" paragraph's opening to say transcripts come from Claude Code **and Codex** (auto-detected), behind a `HarnessAdapter` seam. Keep edits to one or two sentences each — match the surrounding tone.

- [ ] **Step 4: Run the entire test suite + linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green. (If ruff-format rewrites files, re-stage and the pre-commit hook will pass on the next commit.)

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/__init__.py README.md CLAUDE.md
git commit -m "feat(ingest): export harness API + document Codex support (#36)"
```

---

## Self-Review

**1. Spec coverage:**
- HarnessAdapter seam (protocol/ParseCtx/registry) → Task 2. ✓
- Claude Code refactor behind seam, behavior-preserving → Task 3 + Task 4 (existing classify/parse tests unchanged). ✓
- Codex adapter (rollout JSONL, payload dispatch, tag-backstop, session_meta/turn_context ctx) → Task 5. ✓
- `find_transcripts → list[TranscriptRef]` + auto-detect union; `parse_transcript(ref|Path)` → Task 4. ✓
- CLI auto-detect all present + `CAIRN_HARNESSES`/`--harness` + `--transcripts-dir` guard → Task 6. ✓
- `harness` on `NormalizedEvent` (provenance plumbing) → Task 1. ✓
- Downstream pipeline (redact/judge/consolidate/reindex) untouched → no task modifies those modules. ✓
- Tests: parity, Codex fixtures, registry/auto-detect, provenance → Tasks 3/4/5/6. ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code; commands have expected output. ✓

**3. Type consistency:** `TranscriptRef(path, harness)`, `ParseCtx(path, session_id, cwd, git_branch)`, `to_event(raw, kind, ctx)`, `find(root=, project=)`, `find_transcripts(harness=, root=, project=, harnesses=)`, `_resolve_harnesses(harness_opt, env)` — names/signatures consistent across Tasks 2–6. `NormalizedEvent.harness` added in Task 1 and stamped by both adapters. ✓

**Note for the executor:** Tasks 1–2 and 4–7 are mechanical given the complete code above (verify the diff directly). Task 5 (CodexAdapter) carries the most classification judgment — give it the full two-stage spec-then-quality review. A final full-branch review precedes finishing the branch (then the cut-a-release ritual is a separate follow-up, not part of this plan).
