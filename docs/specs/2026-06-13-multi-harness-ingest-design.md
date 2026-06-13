# Multi-Harness Transcript Ingestion — Adapter Framework + Codex (cycle 1)

**Status:** Approved (2026-06-13)
**Issue:** #36
**Affects:** `src/cairn/ingest/` (new `harness/` package; `locate.py`, `events.py`, `__init__.py`), `src/cairn/cli.py` (sweep/ingest harness selection), tests. No change to redaction, judge, consolidation, reindex, or the Markdown contract.

## Problem

Today only Claude Code transcripts (`~/.claude/projects/*/*.jsonl`) are ingested. `locate.py` hardcodes the Claude Code layout, container format (JSONL), and `classify_claude_code` (positive-ID, fail-closed `EventKind` classification). The module docstring already promises a dispatch-shaped API "for future harnesses (Codex/Cursor/Gemini)" but nothing implements the seam.

We want to ingest Codex transcripts too, behind a clean adapter interface that later cycles extend to Gemini and Cursor — **without** touching the downstream pipeline (redact → structural candidate selection → judge → consolidate → write → reindex).

## Goal / constraints

- A **`HarnessAdapter` seam**: one adapter per harness, all harness-specific knowledge behind it.
- **Refactor the Claude Code logic behind the seam with zero behavior change** — proven by the existing `test_locate.py` passing unchanged.
- A **Codex adapter** for `~/.codex/sessions/.../rollout-*.jsonl`.
- `cairn sweep` / `cairn ingest` **auto-detect all present harnesses** (a present harness = its root dir exists), unioning their transcripts. A `CAIRN_HARNESSES` env knob and `--harness` flag narrow the set.
- **Preserve every ingestion invariant per harness:** redaction-first (unchanged downstream), structural positive-ID candidate selection, fail-closed classification (an unmapped/unknown row → never a candidate), the sanitize + tag-prefix backstop for legacy/injected rows. Do **not** trust `role == "user"` to mean a human wrote it; do **not** trust a harness's current schema to describe its old transcripts.
- The judge, consolidation, and reindex stages consume `NormalizedEvent`s identically regardless of origin — they must not change.

## Decisions (brainstorm)

- **Cycle 1 = framework + Claude Code refactor + Codex adapter.** Gemini (JSON-array container) and Cursor (SQLite spike) are deferred to later cycles; the seam is designed to admit them but no adapter ships for them now.
- **Sweep auto-detects all present harnesses** (option a), with `CAIRN_HARNESSES` / `--harness` to narrow.
- **`harness` is added to `NormalizedEvent` now** (the adapter stamps it). Cheap, the dispatch already knows it, and it is the plumbing #28 provenance-aware recall needs (`source: memory://<harness>/session/<id>`). Carried now, written to frontmatter when #28 lands — same "plumbing, not yet surfaced" pattern as the existing provenance fields.

## Architecture

### The seam — `HarnessAdapter`

A new package `src/cairn/ingest/harness/`:

```
src/cairn/ingest/harness/
  __init__.py        # HarnessAdapter protocol, ParseCtx, REGISTRY, resolve/iter helpers
  claude_code.py     # ClaudeCodeAdapter (logic moved verbatim from locate.py)
  codex.py           # CodexAdapter
```

```python
# harness/__init__.py
from typing import Protocol, Iterator

@dataclass
class ParseCtx:
    """Mutable per-file context an adapter fills in as it scans a transcript:
    the session id / cwd / git branch discovered from header or per-row fields."""
    session_id: str | None = None
    cwd: str | None = None
    git_branch: str | None = None

class HarnessAdapter(Protocol):
    name: str                                              # "claude-code", "codex"
    def default_root(self) -> Path: ...                    # ~/.claude/projects, ~/.codex/sessions
    def is_present(self) -> bool: ...                       # default_root().is_dir()
    def find(self, *, root: Path | None, project: str | None) -> list[Path]: ...
    def iter_raw(self, path: Path) -> Iterator[dict]: ...   # container parse (JSONL vs JSON-array)
    def classify(self, raw: dict) -> EventKind: ...         # positive-ID, fail-closed
    def to_event(self, raw: dict, ctx: ParseCtx) -> NormalizedEvent | None: ...
```

- `default_root()` / `is_present()` abstract **discovery** — `is_present` drives auto-detect.
- `find()` returns transcript paths newest-first; a missing root yields `[]` (graceful, as today).
- `iter_raw()` is the **only** place container format differs: Claude Code and Codex yield JSONL lines (skipping blank/corrupt lines, since transcripts are append-only and the last line may be partial); Gemini (future) would yield array elements.
- `classify()` + `to_event()` carry the positive-ID, fail-closed + sanitize + tag-backstop invariants per harness. `to_event` returns `None` for a row with no usable text (so the caller skips it) and stamps `harness=self.name`.

### Registry + dispatch

`harness/__init__.py` holds `REGISTRY: dict[str, HarnessAdapter]` mapping name → adapter instance (`{"claude-code": ClaudeCodeAdapter(), "codex": CodexAdapter()}`).

Helpers:

```python
def get_adapter(name: str) -> HarnessAdapter:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(f"unsupported harness: {name!r} (have: {sorted(REGISTRY)})")

def present_harnesses(selected: list[str] | None = None) -> list[HarnessAdapter]:
    """Adapters whose root exists. `selected` (from CAIRN_HARNESSES/--harness) narrows
    and validates names; None means 'all registered, present ones'."""
    names = selected if selected is not None else list(REGISTRY)
    return [a for a in (get_adapter(n) for n in names) if a.is_present()]
```

`locate.py` keeps `find_transcripts` / `parse_transcript` as the public API (re-exported from `cairn.ingest`), now dispatching through the registry:

```python
def find_transcripts(
    *, harness: str | None = "claude-code", root: Path | None = None,
    project: str | None = None, harnesses: list[str] | None = None,
) -> list[TranscriptRef]:
    """Return transcript references newest-first.

    - harness=<name>: that single harness (back-compat default "claude-code").
    - harness=None: auto-detect — union of every present harness (or those named
      in `harnesses` / CAIRN_HARNESSES). `root` is ignored in auto-detect mode.
    """
```

Because auto-detect unions multiple harnesses with different roots, `find_transcripts` must return enough to route each path back to its adapter. It returns a list of `TranscriptRef(path: Path, harness: str)` (newest-first by mtime across all harnesses). `parse_transcript(ref)` accepts a `TranscriptRef` (or, for back-compat, a bare `Path` defaulting to `"claude-code"`) and selects the adapter:

```python
def parse_transcript(ref: TranscriptRef | Path, *, harness: str = "claude-code") -> Transcript:
    path, name = (ref.path, ref.harness) if isinstance(ref, TranscriptRef) else (ref, harness)
    adapter = get_adapter(name)
    ctx = ParseCtx()
    events, kind_counts = [], Counter()
    for raw in adapter.iter_raw(path):
        kind = adapter.classify(raw)
        kind_counts[kind.value] += 1
        ev = adapter.to_event(raw, ctx)   # updates ctx (session/cwd/branch) as a side effect
        if ev is not None:
            events.append(ev)
    return Transcript(
        session_id=ctx.session_id or path.stem, cwd=ctx.cwd,
        git_branch=ctx.git_branch, path=path, events=events,
        kind_counts=dict(kind_counts),
    )
```

> **Note:** `classify` is called on every raw row (it feeds `kind_counts`, a diagnostic the existing code keeps). `to_event` re-derives the kind internally (or accepts it) and returns the event; to avoid double classification, `to_event` may call `self.classify(raw)` once and the loop reads the kind from the returned event. Implementation detail left to the plan; the contract is: every raw row contributes to `kind_counts`, and only rows yielding text become events.

### `ClaudeCodeAdapter`

The current `locate.py` internals move **verbatim** into `claude_code.py`:
- `default_root()` → `Path.home() / ".claude" / "projects"`.
- `encode_cwd` (stays importable from `locate.py`/`cairn.ingest` — public API) and `find()` (the existing `find_transcripts` body: per-project encoded dir or all dirs, `*.jsonl`, mtime-sorted).
- `iter_raw()` → the JSONL read loop (`read_text(errors="replace").splitlines()`, `json.loads`, skip blank/corrupt/non-dict, skip rows whose `type` not in `{"user","assistant"}`).
- `classify()` → today's `classify_claude_code` unchanged.
- `to_event()` → the per-row body of today's `parse_transcript`: `_extract_text`, the `session_id`/`cwd`/`git_branch` first-seen capture (now into `ParseCtx`), `project_from_cwd`, building the `NormalizedEvent` with `harness="claude-code"`.

`classify_claude_code`, `_extract_text`, `_LEGACY_TAG_PREFIXES`, `encode_cwd` stay importable where tests expect them (re-export from `claude_code.py` or keep thin wrappers in `locate.py`). The **classification and parse tests** in `test_locate.py` pass **unchanged** — that is the behavior-preservation proof. The `find_transcripts` tests are the one exception: its return type changes from `list[Path]` to `list[TranscriptRef]`, so those specific assertions update to read `.path` (a mechanical change, not a behavior change).

### `CodexAdapter`

`~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl`, one JSON object per line `{type, payload, timestamp}`.

- `default_root()` → `Path.home() / ".codex" / "sessions"`.
- `find()`:
  - no `project` → all `rollout-*.jsonl` under the date-tree (`root.rglob("rollout-*.jsonl")`), mtime-sorted newest-first.
  - `project` given → Codex stores cwd in `session_meta`/`turn_context`, not in the path, so a project filter requires reading each file's `session_meta.cwd` and keeping matches. (Acceptable: sweep's default is all-projects; the filter is a cheap header read.)
- `iter_raw()` → JSONL lines, same robustness as Claude Code (skip blank/corrupt/non-dict).
- `classify()` (positive-ID, fail-closed) on top-level `type`:
  - `session_meta` → `SYSTEM` (header; consumed by `to_event` for ctx).
  - `turn_context` → `SYSTEM` (per-turn config; cwd/user_instructions — not a candidate).
  - `event_msg` → `SYSTEM` (UI/turn bookkeeping).
  - `compacted` → `COMPACT_SUMMARY`.
  - `response_item` → dispatch on `payload.type` (+ `payload.role`):
    - `function_call`, `function_call_output`, `custom_tool_call`, `custom_tool_call_output`, `web_search_call` (role None) → `TOOL_RESULT`.
    - `reasoning` → `AUTHORED_ASSISTANT`.
    - `message` + role `assistant` (content `output_text`) → `AUTHORED_ASSISTANT`.
    - `message` + role `developer` (content `input_text`) → `META_INJECTION`.
    - `message` + role `user` (content `input_text`) → **candidate**, then **tag-backstop**: sanitize the text, and if it starts with a Codex injection marker demote to `META_INJECTION`; else `AUTHORED_USER`.
  - anything else → `UNKNOWN` (fail-closed).
- Codex tag-backstop prefixes (the harness's own injected blocks, never user vocabulary):
  ```python
  _CODEX_TAG_PREFIXES = (
      "# AGENTS.md",          # injected repo-instructions block
      "<INSTRUCTIONS>",       # injected instruction wrapper
      "<turn_aborted",        # aborted-turn marker
      "<user_instructions",   # user_instructions wrapper (if present as a user row)
      "<environment_context", # environment/context wrapper
  )
  ```
  (Final prefix list validated against real `rollout-*.jsonl` fixtures during implementation; the principle is positive-ID prose only, demote anything that begins with a harness-injected tag.)
- `to_event()`:
  - `session_meta` → set `ctx.session_id` (its `session_id`) and `ctx.cwd` (its `cwd`); return `None`.
  - `turn_context` → if `ctx.cwd` still unset, fill from its `cwd`; return `None`.
  - content rows → extract text from the `payload.content` blocks (`input_text`/`output_text`), sanitize, build a `NormalizedEvent(harness="codex", role=payload.role or "system", project=project_from_cwd(ctx.cwd), session_id=ctx.session_id, git_branch=None, ...)`. Codex has no git branch in the transcript → `None`.

### `cairn sweep` / `cairn ingest` — auto-detect

Both commands gain a `--harness` option (repeatable or comma-list) and honor `CAIRN_HARNESSES`. Resolution order: `--harness` flag > `CAIRN_HARNESSES` env > None (auto-detect all present).

```python
selected = _resolve_harnesses(harness_opt, cairn_env())   # None | list[str]
refs = find_transcripts(harness=None, harnesses=selected, project=project)
transcripts = [parse_transcript(ref) for ref in refs]
```

`--transcripts-dir` keeps overriding the root **only when a single harness is explicitly selected** (it is meaningless across a union of differently-rooted harnesses); if `--transcripts-dir` is given with auto-detect, error with a clear message ("`--transcripts-dir` requires `--harness <one>`"). Everything after `transcripts = [...]` (judge, consolidation, reindex) is unchanged.

### Provenance

Add `harness: str` to `NormalizedEvent` (after `source_path`, with adapters stamping their `name`). It is carried, not yet written to frontmatter — identical to the existing `session_id`/`project`/`git_branch` provenance plumbing for #28.

## Data flow

```
present_harnesses(selected)                 # auto-detect by is_present()
  → adapter.find(root, project) per harness  # union, newest-first → list[TranscriptRef]
  → parse_transcript(ref)                     # adapter.iter_raw → classify → to_event
      → Transcript(events=[NormalizedEvent(harness=…, kind=…)])
  → ingest_transcripts(...)                   # UNCHANGED: redact → select (AUTHORED_USER only)
                                              #   → judge → distill → consolidate → write
  → reconcile/reindex                         # UNCHANGED
```

## Error handling

- Unknown harness name (flag/env) → `ValueError` listing registered names (fails the command early, before any IO).
- Missing harness root → that adapter contributes `[]` (graceful); auto-detect simply skips non-present harnesses.
- Corrupt/partial JSONL line → skipped (append-only transcripts), as today.
- Unrecognized row type/shape → `UNKNOWN`/`SYSTEM`, never a candidate (fail-closed).
- `--transcripts-dir` with auto-detect (no single `--harness`) → explicit usage error.

## Testing / verification

- **`test_locate.py` classification/parse tests pass unchanged** — the canonical proof the Claude Code refactor is behavior-preserving. Only the `find_transcripts` tests update mechanically for the `TranscriptRef` return type (`.path`).
- **ClaudeCodeAdapter parity test:** a fixture transcript through the adapter yields a `Transcript` identical (events, kinds, session/cwd/branch) to the pre-refactor output; `harness == "claude-code"` on every event.
- **CodexAdapter fixtures:** a real-shaped `rollout-*.jsonl` exercising each branch — `session_meta` (seeds ctx), `turn_context`, `event_msg`, `compacted`, `response_item` for function_call / custom_tool_call / web_search_call / reasoning / assistant-message / developer-message / genuine-user-message, and the tag-backstop cases (`# AGENTS.md`, `<turn_aborted>`, `<INSTRUCTIONS>`). Assert: only genuine user prose → `AUTHORED_USER`; injected user rows → `META_INJECTION`; tools → `TOOL_RESULT`; `harness == "codex"`; ctx session_id/cwd populated from `session_meta`.
- **Registry / auto-detect test:** with two fake-present adapters, `find_transcripts(harness=None)` unions both newest-first; `CAIRN_HARNESSES`/`harnesses=` narrows; unknown name → `ValueError`; absent harness contributes nothing.
- **Provenance test:** `NormalizedEvent.harness` set correctly per adapter.
- `uv run pytest` green; `uv run ruff check` / format clean.
- Dogfood: `cairn sweep` with both Claude Code and Codex present on disk writes notes from both; spot-check a Codex-origin note is genuine user prose (no injected `AGENTS.md` content leaked in).

## File-by-file

| File | Change |
|---|---|
| `src/cairn/ingest/events.py` | add `harness: str` to `NormalizedEvent` |
| `src/cairn/ingest/harness/__init__.py` | **new** — `HarnessAdapter` protocol, `ParseCtx`, `TranscriptRef`, `REGISTRY`, `get_adapter`/`present_harnesses` |
| `src/cairn/ingest/harness/claude_code.py` | **new** — `ClaudeCodeAdapter` (Claude Code logic moved verbatim) |
| `src/cairn/ingest/harness/codex.py` | **new** — `CodexAdapter` (rollout JSONL, payload dispatch, tag-backstop) |
| `src/cairn/ingest/locate.py` | dispatch through registry; `find_transcripts` returns `list[TranscriptRef]` + auto-detect; `parse_transcript(ref)`; keep `encode_cwd`/`classify_claude_code`/`_extract_text` importable |
| `src/cairn/ingest/__init__.py` | export `TranscriptRef`, adapter/registry helpers as needed |
| `src/cairn/cli.py` | `sweep`/`ingest`: `--harness` option, `CAIRN_HARNESSES`, auto-detect default, `--transcripts-dir` guard |
| `tests/...` | adapter parity, Codex fixtures, registry/auto-detect, provenance |

## Non-goals

- **No Gemini or Cursor adapter this cycle** (seam admits them; later cycles ship them).
- No change to redaction, the judge, consolidation, the Markdown contract, or reindex.
- No frontmatter provenance surfacing (that is #28; we only carry `harness`).
- No new dependencies.

## Open questions

None.
