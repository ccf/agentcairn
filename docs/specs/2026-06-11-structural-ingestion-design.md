# Structural ingestion candidate-selection — Design

**Status:** Approved (brainstorm) — 2026-06-11
**Scope:** Layer A only — deterministic, structure-based candidate selection. Layer B (semantic memory-worthiness) is a separate, later design.

## Problem

A vault audit (2026-06-11) found **~50%+ of ingested notes were not genuine memories**: harness-injected framing (`<task-notification>` background events, `<local-command-*>` slash-command output, `<command-*>` markers, Claude Code skill bodies, `/loop` skill text), tool-output dumps with raw ANSI escapes, and "This session is being continued from…" compaction summaries.

Root cause: `parse_transcript` keeps only the content **string** of each `user`/`assistant` entry and discards all of Claude Code's structural metadata; `pipeline._candidates()` then ingests **every** `role == "user"` turn and subtracts known noise via a text-pattern denylist (`is_framing_noise`, hardened reactively in 0.6.1/0.6.2). That denylist is brittle by construction — anything not enumerated leaks in, and every harness has its own injection vocabulary, so the approach does not generalize to Codex/Cursor/Gemini (#36).

## Key insight

Claude Code's JSONL already labels what each turn is. In one transcript, of 11,357 `user` entries: **9,516 carry `toolUseResult`** (tool outputs, not prose), **696 are `isMeta: true`** (harness injections), **25 are `isCompactSummary: true`**. A genuinely authored user message is the residual: `type == "user"` AND not `isMeta` AND no `toolUseResult` AND not `isCompactSummary` AND not `isVisibleInTranscriptOnly`. Skill/plugin content is even stamped on assistant turns via `attributionSkill`/`attributionPlugin`/`attributionMcpServer`.

So every noise class is **structurally flagged by the harness itself**. We should classify on structure, not text.

## Decisions (locked in brainstorm)

1. **Scope: Layer A only** — structural candidate selection. Semantic "is this *durable*?" (Layer B) is deferred.
2. **Positive-identification, fail-closed** — ingest only turns affirmatively classified as human-authored prose. Tool results, meta injections, summaries, and *anything unrecognized* are not candidates. An unmapped entry type or a new harness yields **zero** candidates until explicitly mapped (the safe, loud failure direction).
3. **Typed event taxonomy** — classify into a `kind` enum, not a bool, for observability, a clean seam for Layer B, and a legible fail-closed default (`UNKNOWN`).
4. **Preserve provenance now (plumbing only)** — carry origin (project/session/branch) through the normalized model so #28 can build on it later; no recall-side behavior in this work.
5. **Architecture: normalized event stream + per-harness adapter** — harness specifics live only in one `classify()`; everything downstream is harness-blind. No registry ceremony yet (YAGNI until #36 adds the second harness).

## Architecture

### New module: `src/cairn/ingest/events.py`

```python
class EventKind(str, Enum):
    AUTHORED_USER      = "authored_user"        # the ONLY candidate source (Layer A)
    AUTHORED_ASSISTANT = "authored_assistant"   # retained in stream, not a candidate
    TOOL_RESULT        = "tool_result"
    META_INJECTION     = "meta_injection"        # slash-command markers, skill text, hooks
    COMPACT_SUMMARY    = "compact_summary"
    SYSTEM             = "system"
    UNKNOWN            = "unknown"                # fail-closed bucket → never a candidate


@dataclass(frozen=True)
class NormalizedEvent:
    kind: EventKind
    role: str
    text: str                 # sanitized (sanitize_text) at parse
    timestamp: str | None
    # provenance (plumbing for #28; carried, not yet written to frontmatter)
    session_id: str | None
    project: str | None       # derived from cwd
    git_branch: str | None
    source_path: Path
```

### Claude Code classifier (only place harness specifics live)

Operates on the **raw JSONL object**, positive-ID, fail-closed:

- `type == "user"`:
  - `isCompactSummary` truthy → `COMPACT_SUMMARY`
  - `toolUseResult` key present → `TOOL_RESULT`
  - `isMeta` or `isVisibleInTranscriptOnly` truthy → `META_INJECTION`
  - else → **`AUTHORED_USER`**
- `type == "assistant"` → `AUTHORED_ASSISTANT`
- `type == "system"` → `SYSTEM`
- any other `type` (the `last-prompt`/`mode`/`ai-title`/`attachment`/… bookkeeping lines) → **not emitted** as an event

Property: every audited noise class is excluded **without being named** — tool dumps → `TOOL_RESULT`; `<task-notification>`/`<command-*>`/skill "Base directory…" → `META_INJECTION`; "continued from…" → `COMPACT_SUMMARY`.

### Parsing & selection changes

- `parse_transcript` becomes harness-dispatched (matching `locate.py`'s existing shape) and returns `list[NormalizedEvent]` with metadata preserved and `text` run through the existing `sanitize_text`.
- `pipeline._candidates()` → `select_candidates(events)` = `[e for e in events if e.kind == EventKind.AUTHORED_USER]`, carrying provenance into `Candidate`.
- `models.Turn` is replaced by `NormalizedEvent`; `Transcript` holds events. `Candidate` retains `session_id`/`cwd`/`git_branch`/`source_path` (already present) plus a `project` derived from `cwd`.

### Deletions

- **`is_framing_noise()` and its tag-prefix denylist are removed.** Structure subsumes them; the `<task-notification>`-style lists that drift out of date no longer exist. (Removing this brittle code is a goal of the work, not a side effect.)
- **`sanitize_text()` is kept**, applied to all event text — escapes can still appear pasted *inside* a genuinely authored message (defense-in-depth, cheap).
- The **importance gate is unchanged** (an orthogonal quality heuristic; its prior ANSI-word-count inflation is moot now that escapes are stripped and tool output is never ingested).

## Observability

`IngestReport` gains a per-kind tally. `cairn ingest`/`sweep` (and `--json`) print, e.g.:

```
authored: 142 kept · skipped: 9,516 tool_result, 696 meta_injection, 25 compact_summary, 0 unknown
```

`unknown > 0` is a deliberate **loud signal** that the harness schema drifted or a new entry type appeared — prompting a classifier update rather than silent noise accumulation.

## Provenance (plumbing only)

`NormalizedEvent` and `Candidate` carry `session_id`, `project` (from `cwd`), `git_branch`, `source_path` end-to-end. This work does **not** write origin to note frontmatter or alter recall ranking — that is #28. The sole guarantee here is that the parse refactor does not discard provenance, so #28 becomes a frontmatter + recall change, not another parse rewrite.

## Error handling

- Malformed/partial JSONL lines are skipped (as today; transcripts are append-only).
- Metadata read defensively (`.get()`); absence of a positive authored signal ⇒ not authored (fail-closed).
- Unknown entry `type`s within content rows ⇒ `UNKNOWN`, counted, never a candidate.

## Testing (all offline, no keys)

- **Classifier units:** a hand-built fixture `.jsonl` with one entry per kind (authored user; user+`toolUseResult`; user+`isMeta`; user+`isCompactSummary`; assistant; system; a bookkeeping line) → assert each maps to the expected `EventKind`.
- **Regression fixtures from the real noise:** a `/context` ANSI dump, a `<task-notification>`, a skill "Base directory…" line → assert all classify as non-`AUTHORED_USER`.
- **Fail-closed property:** an entry with an unrecognized shape → `UNKNOWN`, not a candidate.
- **Selection:** `select_candidates` returns only authored-user text with provenance populated.
- **Invariants preserved:** existing pipeline tests (redact-before-write, dedup, dry-run, report JSON-serializability) re-pass against the new model.

## Rollout & sequencing

- **Minor bump → 0.7.0.** Behavior change to ingestion; **vault note format unchanged** (`- [context] … #ingested`), so **no index/schema migration**.
- This is the engine for the **3(b) vault rebuild**: ship → clear the dedup ledger → re-`sweep` on-disk transcripts → genuine authored memories re-created, structural noise excluded at source, **cross-project authored memories preserved** (a global vault is the correct default; provenance is a feature, not a filter).
- **Sequencing:** land the **redaction over-redaction fix first** (separate brainstorm — the entropy heuristic swallows paths/URLs/branches) so the rebuild does not re-create path-damaged memories. Net order: this design → redaction design → implement both → one clean rebuild.

## Out of scope (YAGNI / later)

- **Layer B** — semantic/LLM memory-worthiness judging of structurally-authored turns (e.g., ephemeral "watch loop for PR #6" notes).
- **Redaction heuristic fix** — its own design.
- **#28** — provenance-aware recall, project-scoped/shared vaults, multi-user attribution.
- **#36 adapters** — Codex/Cursor/Gemini classifiers; this design defines the seam they plug into, but adds no new adapter.
- **Ingesting `AUTHORED_ASSISTANT`** as candidates — retained in the stream but not a candidate source in Layer A.
