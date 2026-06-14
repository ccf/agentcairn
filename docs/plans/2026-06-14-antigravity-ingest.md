# Antigravity Ingest Adapter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `antigravity` HarnessAdapter that ingests Antigravity CLI transcripts from `~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`, so `cairn sweep` auto-detects and captures Antigravity sessions.

**Architecture:** One new adapter module mirroring `CodexAdapter`, registered in `_bootstrap_registry`. JSONL container; positive-ID/fail-closed classification: `USER_INPUT`+`source=="USER_EXPLICIT"` → extract only the `<USER_REQUEST>` block → AUTHORED_USER; `PLANNER_RESPONSE` → AUTHORED_ASSISTANT; everything else skipped. Pipeline/CLI unchanged.

**Tech Stack:** Python 3.12+, `uv` (`uv run pytest` / `uv run ruff`), stdlib `json`/`re`/`pathlib`. Tests: `tests/ingest/test_harness.py`.

**Spec:** `docs/specs/2026-06-14-antigravity-ingest-design.md`. **Branch:** `feat/antigravity-ingest` (spec committed).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cairn/ingest/harness/antigravity.py` | **new** — `AntigravityAdapter` + `_user_request` extraction + `_conversation_cwd` reverse-map + `_uuid_of` |
| `src/cairn/ingest/harness/__init__.py` | register `AntigravityAdapter` in `_bootstrap_registry` |
| `tests/ingest/test_harness.py` | classify branches, `_user_request` leakage guard, end-to-end parse, find + project filter, registry |
| `README.md`, `CLAUDE.md` | note Antigravity as an ingested harness (and that Gemini CLI is not supported) |

---

## Task 1: AntigravityAdapter

**Files:**
- Create: `src/cairn/ingest/harness/antigravity.py`
- Modify: `src/cairn/ingest/harness/__init__.py`
- Test: `tests/ingest/test_harness.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_harness.py`:

```python
def _agy_line(type_, source, content=None, created_at="2026-06-14T14:19:09Z"):
    import json

    o = {"step_index": 0, "source": source, "type": type_, "status": "DONE", "created_at": created_at}
    if content is not None:
        o["content"] = content
    return json.dumps(o)


_AGY_USER = (
    "<USER_REQUEST>\nRemember: I always squash-merge agentcairn release branches.\n</USER_REQUEST>\n"
    "<ADDITIONAL_METADATA>\nThe current local time is: 2026-06-14T10:19:09-04:00.\n</ADDITIONAL_METADATA>\n"
    "<USER_SETTINGS_CHANGE>\nThe user changed setting `Model Selection` to Gemini 3.5 Flash.\n</USER_SETTINGS_CHANGE>"
)


def test_antigravity_user_request_extracts_only_request_block():
    from cairn.ingest.harness.antigravity import _user_request

    out = _user_request(_AGY_USER)
    assert out == "Remember: I always squash-merge agentcairn release branches."
    # leakage guard: injected framing must NOT survive
    assert "ADDITIONAL_METADATA" not in out
    assert "Model Selection" not in out
    assert "local time" not in out
    # absent block / non-string → empty
    assert _user_request("no request here") == ""
    assert _user_request(None) == ""


def test_antigravity_classify_each_kind():
    from cairn.ingest.harness.antigravity import AntigravityAdapter

    a = AntigravityAdapter()
    assert a.name == "antigravity"
    # genuine user prose
    assert a.classify({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": _AGY_USER}) == EventKind.AUTHORED_USER
    # slash-command inside the request block → demoted
    assert a.classify(
        {"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST>\n/model\n</USER_REQUEST>"}
    ) == EventKind.META_INJECTION
    # non-explicit source → not authored
    assert a.classify({"type": "USER_INPUT", "source": "SYSTEM", "content": _AGY_USER}) == EventKind.META_INJECTION
    # empty request → demoted
    assert a.classify({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": "<USER_REQUEST></USER_REQUEST>"}) == EventKind.META_INJECTION
    assert a.classify({"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "done"}) == EventKind.AUTHORED_ASSISTANT
    assert a.classify({"type": "CONVERSATION_HISTORY", "source": "SYSTEM"}) == EventKind.UNKNOWN
    assert a.classify({"type": "WEIRD_FUTURE", "source": "x"}) == EventKind.UNKNOWN


def test_antigravity_parses_transcript(tmp_path):
    from cairn.ingest.locate import parse_transcript

    logs = tmp_path / "brain" / "abc-uuid-123" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    f = logs / "transcript.jsonl"
    f.write_text(
        "\n".join(
            [
                _agy_line("USER_INPUT", "USER_EXPLICIT", _AGY_USER),
                _agy_line("CONVERSATION_HISTORY", "SYSTEM"),
                _agy_line("PLANNER_RESPONSE", "MODEL", "Acknowledged."),
                _agy_line("USER_INPUT", "USER_EXPLICIT", "<USER_REQUEST>\n/help\n</USER_REQUEST>"),
            ]
        )
        + "\n"
    )
    # cwd map: brain/../cache/last_conversations.json maps {cwd: uuid}
    cache = tmp_path / "cache"
    cache.mkdir()
    import json as _j

    (cache / "last_conversations.json").write_text(_j.dumps({"/Users/x/proj": "abc-uuid-123"}))

    tr = parse_transcript(TranscriptRef(path=f, harness="antigravity"))
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == ["Remember: I always squash-merge agentcairn release branches."]
    assert all(e.harness == "antigravity" for e in tr.events)
    assert authored[0].session_id == "abc-uuid-123"
    assert authored[0].project == "proj"  # resolved from last_conversations.json
    # no injected framing leaked into any event text
    assert all("ADDITIONAL_METADATA" not in e.text for e in tr.events)


def test_antigravity_find_globs_and_project_filter(tmp_path):
    from cairn.ingest.harness.antigravity import AntigravityAdapter
    import json as _j

    a = AntigravityAdapter()
    for uuid in ("keep-uuid", "drop-uuid"):
        d = tmp_path / "brain" / uuid / ".system_generated" / "logs"
        d.mkdir(parents=True)
        (d / "transcript.jsonl").write_text(_agy_line("USER_INPUT", "USER_EXPLICIT", _AGY_USER) + "\n")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "last_conversations.json").write_text(
        _j.dumps({"/Users/x/keep": "keep-uuid", "/Users/x/other": "drop-uuid"})
    )
    root = tmp_path / "brain"
    assert {p.parent.parent.parent.name for p in a.find(root=root, project=None)} == {"keep-uuid", "drop-uuid"}
    kept = a.find(root=root, project="/Users/x/keep")
    assert [p.parent.parent.parent.name for p in kept] == ["keep-uuid"]


def test_antigravity_registered():
    assert get_adapter("antigravity").name == "antigravity"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_harness.py -k antigravity -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.ingest.harness.antigravity'`.

- [ ] **Step 3: Create the adapter**

Create `src/cairn/ingest/harness/antigravity.py` with EXACTLY:

```python
# src/cairn/ingest/harness/antigravity.py
# SPDX-License-Identifier: Apache-2.0
"""Antigravity adapter: ~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/
logs/transcript.jsonl (one JSON object per line). Antigravity CLI replaces Gemini
CLI (sunset 2026-06-18) and is both a desktop app and a CLI.

Positive-ID, fail-closed: only a USER_INPUT step's <USER_REQUEST> block is authored
user prose; injected <ADDITIONAL_METADATA>/<USER_SETTINGS_CHANGE> framing is dropped."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd
from cairn.ingest.harness import ParseCtx
from cairn.ingest.sanitize import sanitize_text

_AGY_ROOT = Path.home() / ".gemini" / "antigravity-cli" / "brain"

# Inner text of the first <USER_REQUEST>...</USER_REQUEST> block. Everything outside
# it (ADDITIONAL_METADATA, USER_SETTINGS_CHANGE, ...) is injected framing to drop.
_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>\s*(.*?)\s*</USER_REQUEST>", re.DOTALL)


def _user_request(content: object) -> str:
    """The authored prose of a USER_INPUT step: inner text of the first
    <USER_REQUEST> block, sanitized. '' if absent/empty/non-string."""
    if not isinstance(content, str):
        return ""
    m = _USER_REQUEST_RE.search(content)
    if not m:
        return ""
    return sanitize_text(m.group(1)).strip()


def _conversation_cwd(brain_root: Path) -> dict[str, str]:
    """Reverse of cache/last_conversations.json ({cwd: uuid}) -> {uuid: cwd},
    best-effort. Empty dict on any error. cache/ is a sibling of brain/."""
    cache = brain_root.parent / "cache" / "last_conversations.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {uuid: cwd for cwd, uuid in data.items() if isinstance(cwd, str) and isinstance(uuid, str)}


def _uuid_of(path: Path) -> str:
    """Conversation uuid for brain/<uuid>/.system_generated/logs/transcript.jsonl."""
    return path.parent.parent.parent.name


class AntigravityAdapter:
    name = "antigravity"

    def default_root(self) -> Path:
        return _AGY_ROOT

    def is_present(self) -> bool:
        return self.default_root().is_dir()

    def find(self, *, root: Path | None, project: str | None) -> list[Path]:
        base = Path(root) if root is not None else self.default_root()
        if not base.is_dir():
            return []
        files = list(base.glob("*/.system_generated/logs/transcript.jsonl"))
        if project is not None:
            target = project.rstrip("/") or "/"
            uuid_cwd = _conversation_cwd(base)
            files = [f for f in files if (uuid_cwd.get(_uuid_of(f)) or "").rstrip("/") == target]
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
                continue  # partial/corrupt line
            if isinstance(obj, dict):
                yield obj

    def classify(self, raw: dict) -> EventKind:
        t = raw.get("type")
        if t == "USER_INPUT":
            if raw.get("source") != "USER_EXPLICIT":
                return EventKind.META_INJECTION  # system-injected user step
            text = _user_request(raw.get("content")).lstrip()
            if not text or text.startswith("/"):
                return EventKind.META_INJECTION  # empty or slash-command
            return EventKind.AUTHORED_USER
        if t == "PLANNER_RESPONSE":
            return EventKind.AUTHORED_ASSISTANT
        return EventKind.UNKNOWN

    def to_event(self, raw: dict, kind: EventKind, ctx: ParseCtx) -> NormalizedEvent | None:
        if ctx.session_id is None:
            # Resolve session + cwd once per file (uuid from the path; cwd best-effort).
            ctx.session_id = _uuid_of(ctx.path)
            ctx.cwd = _conversation_cwd(ctx.path.parents[3]).get(ctx.session_id)
        t = raw.get("type")
        if t == "USER_INPUT":
            text = _user_request(raw.get("content"))
            role = "user"
        elif t == "PLANNER_RESPONSE":
            content = raw.get("content")
            text = sanitize_text(content).strip() if isinstance(content, str) else ""
            role = "assistant"
        else:
            return None
        if not text:
            return None
        return NormalizedEvent(
            kind=kind,
            role=role,
            text=text,
            timestamp=raw.get("created_at"),
            session_id=ctx.session_id,
            project=project_from_cwd(ctx.cwd),
            git_branch=None,  # Antigravity transcripts carry no git branch
            source_path=ctx.path,
            harness=self.name,
        )
```

Note: for the transcript path `brain/<uuid>/.system_generated/logs/transcript.jsonl`, `ctx.path.parents[3]` is the `brain` root and `_uuid_of` (`parent.parent.parent.name`) is `<uuid>` — these are consistent (`parents[2] == parent.parent.parent`).

- [ ] **Step 4: Register the adapter**

In `src/cairn/ingest/harness/__init__.py`, extend `_bootstrap_registry`:

```python
def _bootstrap_registry() -> None:
    from cairn.ingest.harness.antigravity import AntigravityAdapter
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter
    from cairn.ingest.harness.codex import CodexAdapter

    _register(ClaudeCodeAdapter())
    _register(CodexAdapter())
    _register(AntigravityAdapter())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_harness.py -q`
Expected: PASS (all harness tests incl. the new antigravity ones).

- [ ] **Step 6: Run the full ingest suite (no regressions)**

Run: `uv run pytest tests/ingest/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/cairn/ingest/harness/antigravity.py src/cairn/ingest/harness/__init__.py tests/ingest/test_harness.py
git commit -m "feat(ingest): Antigravity adapter — transcript.jsonl USER_REQUEST capture (#36)"
```

(If pre-commit reformats/aborts, `git add -A` and re-run the commit.)

---

## Task 2: Docs + full verification

**Files:**
- Modify: `README.md`, `CLAUDE.md`

- [ ] **Step 1: Update README.md**

Find where ingested harnesses are described (search `README.md` for "Codex" / "ingest" / "transcript" / "sweep" / "auto-detect"). Add **Antigravity** to the list of harnesses whose sessions are captured (Claude Code, Codex, **Antigravity**), and note that **Gemini CLI is not supported** (Google is sunsetting it 2026-06-18 in favor of Antigravity). Keep it to one or two sentences; match surrounding tone. Do not claim a Gemini adapter exists.

- [ ] **Step 2: Update CLAUDE.md**

In `CLAUDE.md`, find the capture-pipeline description that names the ingested harnesses and add **Antigravity** (`~/.gemini/antigravity-cli/brain/<uuid>/.system_generated/logs/transcript.jsonl`). One sentence; match voice; do not restructure.

- [ ] **Step 3: Full suite + linters**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: all green. (If ruff-format would rewrite, run `uv run ruff format .` and re-stage.)

- [ ] **Step 4: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: Antigravity is an ingested harness; Gemini CLI not supported"
```

---

## Self-Review

**1. Spec coverage:**
- Adapter sourced from `brain/*/.system_generated/logs/transcript.jsonl` → Task 1 `find`/`iter_raw`. ✓
- `USER_INPUT`+`USER_EXPLICIT` → `<USER_REQUEST>` extraction → AUTHORED_USER; slash/empty demoted; `PLANNER_RESPONSE` → assistant; else skipped (fail-closed) → Task 1 `classify`/`to_event` + tests. ✓
- Injected `<ADDITIONAL_METADATA>`/`<USER_SETTINGS_CHANGE>` never leak → `_user_request` + leakage-guard test. ✓
- cwd best-effort via `cache/last_conversations.json` reverse map; `None` fallback → `_conversation_cwd` + find/parse tests. ✓
- session_id = brain uuid → `_uuid_of` + parse test. ✓
- Registered + auto-detected → bootstrap + `test_antigravity_registered`. ✓
- Pipeline/CLI unchanged; drops Gemini CLI; ingest-only → no other modules touched; docs note (Task 2). ✓

**2. Placeholder scan:** No TBD/TODO; complete code in every step; commands have expected output. ✓

**3. Type consistency:** `_user_request(content)`, `_conversation_cwd(brain_root)`, `_uuid_of(path)`, `AntigravityAdapter` methods match the protocol (`name`/`default_root`/`is_present`/`find(*,root,project)`/`iter_raw`/`classify`/`to_event(raw,kind,ctx)`) used by `parse_transcript`. `parents[3]` (brain root) and `parent.parent.parent` (uuid) are the consistent path arithmetic for `brain/<uuid>/.system_generated/logs/transcript.jsonl`. ✓

**Note for the executor:** Task 1 is the judgment-heavy piece (classification + the `<USER_REQUEST>` extraction / leakage guard) — give it the full two-stage spec-then-quality review. Task 2 is docs — verify the diff. After Task 2, dogfood: a real `agy` session → `cairn sweep --harness antigravity` into a scratch vault → the genuine user turn is written and no `<ADDITIONAL_METADATA>`/`<USER_SETTINGS_CHANGE>` framing leaks. Release (0.13.0) is a separate follow-up.
