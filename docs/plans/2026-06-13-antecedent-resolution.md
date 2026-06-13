# Antecedent-Resolved Distillation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the LLM judge resolve a referent in a confirmation-style user turn ("lock A") using the immediately-preceding assistant proposal as transient, redacted context, so the distilled memory is self-contained.

**Architecture:** Additive layer over the existing `redact → dedup → judge → gate → distill → write` pipeline. `select_candidates` captures the nearest preceding `AUTHORED_ASSISTANT` per session onto `Candidate.antecedent`; Phase A redacts it; the `Judge.judge` protocol gains an optional backward-compatible `contexts=` kwarg that only `LLMJudge` consumes (rendering a "PRIOR ASSISTANT MESSAGE" block + a resolve-only prompt rule). `[verbatim]` and the keep-iff-distilled rule are unchanged; the antecedent is never stored. Bumping `_JUDGE_CACHE_VERSION` invalidates stale verdicts so a re-gate re-resolves.

**Tech Stack:** Python 3.12, `uv`, pytest. Spec: `docs/specs/2026-06-13-antecedent-resolution-design.md`.

**Conventions:**
- Run tests with `uv run pytest`.
- Pre-commit runs ruff + ruff-format + pytest on commit. **ruff-format reformats files on the first commit attempt, which aborts that commit** — when that happens, `git add -A` and re-run the same `git commit` (it passes the second time).
- Commit message trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cairn/ingest/models.py` | `Candidate` dataclass | add `antecedent: str \| None = None` |
| `src/cairn/ingest/pipeline.py` | candidate selection + orchestration | `select_candidates` captures antecedent (`_ANTECEDENT_CHARS`); Phase A redacts it; Phase B passes `contexts=` |
| `src/cairn/ingest/judge.py` | judge tiers + cache | `Judge`/`EmbeddingJudge`/`LLMJudge` gain `contexts=`; LLM renders block + resolve-only prompt; bump `_JUDGE_CACHE_VERSION` |
| `src/cairn/__init__.py` | version | `0.9.6` |
| `CHANGELOG.md` | release notes | `## [0.9.6]` |

---

## Task 1: `Candidate.antecedent` field

**Files:**
- Modify: `src/cairn/ingest/models.py` (the `Candidate` dataclass, around lines 27–39)
- Test: `tests/ingest/test_models.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_models.py` (create the file if it does not exist, with `from pathlib import Path` and `from cairn.ingest.models import Candidate` at the top):

```python
def test_candidate_antecedent_defaults_none_and_accepts_value():
    from pathlib import Path

    from cairn.ingest.models import Candidate

    base = dict(
        text="lock A",
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        timestamp="t0",
        source_path=Path("/tmp/s.jsonl"),
    )
    assert Candidate(**base).antecedent is None  # defaulted, existing constructors unaffected
    assert Candidate(**base, antecedent="Approach A: the orderbook rep").antecedent == (
        "Approach A: the orderbook rep"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_models.py::test_candidate_antecedent_defaults_none_and_accepts_value -v`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'antecedent'`.

- [ ] **Step 3: Add the field**

In `src/cairn/ingest/models.py`, in the `Candidate` dataclass, add after the `importance` field (keep it last so all positional uses are unaffected):

```python
    antecedent: str | None = None  # nearest preceding assistant turn (resolution
    # context for the LLM judge ONLY; redacted; never stored in the note)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/models.py tests/ingest/test_models.py
git commit -m "feat(ingest): add Candidate.antecedent field"
```

---

## Task 2: `select_candidates` captures the nearest preceding assistant turn

**Files:**
- Modify: `src/cairn/ingest/pipeline.py` (`select_candidates`, lines 23–38; add `_ANTECEDENT_CHARS` constant)
- Test: `tests/ingest/test_pipeline.py`

Current `select_candidates` is a list comprehension over `AUTHORED_USER` events. It becomes a loop that tracks the last assistant turn per session.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_pipeline.py` (it already imports `EventKind`, `NormalizedEvent`, `Transcript`, and defines `_ev(kind, text, ts="t0")`):

```python
def _ev_sid(kind, text, sid, ts="t0"):
    """Like _ev but with an explicit session_id (default _ev uses None)."""
    from pathlib import Path

    return NormalizedEvent(
        kind=kind,
        role="user" if kind == EventKind.AUTHORED_USER else "assistant",
        text=text,
        timestamp=ts,
        session_id=sid,
        project="p",
        git_branch="main",
        source_path=Path("/tmp/s.jsonl"),
    )


def test_select_candidates_attaches_nearest_preceding_assistant():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, "I propose approach A: the orderbook representation."),
            _ev(EventKind.TOOL_RESULT, "some tool output"),  # must NOT clear the antecedent
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    (cand,) = select_candidates(t)
    assert cand.text == "lock A"
    assert cand.antecedent == "I propose approach A: the orderbook representation."


def test_select_candidates_no_antecedent_before_any_assistant():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[_ev(EventKind.AUTHORED_USER, "first turn, no prior assistant")],
    )
    (cand,) = select_candidates(t)
    assert cand.antecedent is None


def test_select_candidates_does_not_cross_session_boundary():
    from cairn.ingest.pipeline import select_candidates

    t = Transcript(
        session_id="s1",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev_sid(EventKind.AUTHORED_ASSISTANT, "proposal in session one", "s1"),
            _ev_sid(EventKind.AUTHORED_USER, "user turn in session two", "s2"),
        ],
    )
    (cand,) = select_candidates(t)
    assert cand.antecedent is None  # the s1 proposal must not resolve an s2 turn


def test_select_candidates_truncates_long_antecedent():
    from cairn.ingest.pipeline import _ANTECEDENT_CHARS, select_candidates

    long_proposal = "x" * (_ANTECEDENT_CHARS + 500)
    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=__import__("pathlib").Path("/tmp/s.jsonl"),
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, long_proposal),
            _ev(EventKind.AUTHORED_USER, "go with it"),
        ],
    )
    (cand,) = select_candidates(t)
    assert len(cand.antecedent) == _ANTECEDENT_CHARS  # HEAD-truncated
    assert cand.antecedent == long_proposal[:_ANTECEDENT_CHARS]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_pipeline.py -k "antecedent or cross_session" -v`
Expected: FAIL — `select_candidates` ignores `antecedent` (attribute is always `None`) and `_ANTECEDENT_CHARS` does not exist (ImportError).

- [ ] **Step 3: Rewrite `select_candidates` + add the constant**

In `src/cairn/ingest/pipeline.py`, add the constant just below the imports (after the existing `from cairn.ingest.redact import redact` line):

```python
_ANTECEDENT_CHARS = 2000  # HEAD-truncate the assistant antecedent: the proposal's
# option list is near the top, and this caps the extra judge-input tokens per turn.
```

Replace the entire `select_candidates` function (lines 23–38) with:

```python
def select_candidates(transcript: Transcript) -> list[Candidate]:
    """One candidate per genuinely-authored user event. Everything else (tool
    results, meta injections, summaries, assistant turns) is excluded by kind.
    Each user candidate also carries its `antecedent`: the nearest preceding
    AUTHORED_ASSISTANT turn in the SAME session (HEAD-truncated), used downstream
    only as resolution context for the LLM judge — never stored in the note."""
    out: list[Candidate] = []
    last_assistant: str | None = None
    last_assistant_session: str | None = None
    for e in transcript.events:
        sid = e.session_id or transcript.session_id
        if e.kind == EventKind.AUTHORED_ASSISTANT:
            last_assistant = e.text
            last_assistant_session = sid
            continue
        if e.kind != EventKind.AUTHORED_USER:
            continue  # tool results / meta / etc. do not clear the antecedent
        antecedent = last_assistant if last_assistant_session == sid else None
        if antecedent is not None:
            antecedent = antecedent[:_ANTECEDENT_CHARS]
        out.append(
            Candidate(
                text=e.text,
                session_id=sid,
                cwd=transcript.cwd,
                git_branch=e.git_branch,
                timestamp=e.timestamp,
                source_path=e.source_path,
                project=e.project,
                antecedent=antecedent,
            )
        )
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_pipeline.py -v`
Expected: PASS (the four new tests plus all existing pipeline tests — the candidate fields are otherwise unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): capture nearest preceding assistant turn as Candidate.antecedent"
```

---

## Task 3: Phase A redacts the antecedent

**Files:**
- Modify: `src/cairn/ingest/pipeline.py` (Phase A loop in `ingest_transcripts`, the per-candidate block that currently does `red = redact(cand.text)`)
- Test: `tests/ingest/test_pipeline.py`

The antecedent is assistant text and may echo secrets. It must be redacted before it reaches the judge. It does NOT participate in the dedup hash (`content_hash` stays over the candidate text only).

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_pipeline.py`:

```python
def test_phase_a_redacts_antecedent_before_judge(tmp_path):
    """An antecedent containing a secret must be redacted before the judge sees
    it, and the redaction must be counted in report.redactions."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    seen_contexts = []

    class SpyJudge:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            seen_contexts.extend(contexts or [None] * len(texts))
            return [Judgment(durability=0.0) for _ in texts]  # gate out; we only inspect input

    secret = "sk-ant-api03-" + "A" * 40 + "-deadbeefcafe1234567890AB_cd-ef"
    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, f"Use this key: {secret} for option A."),
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [t], vault_root=vault, ledger=DedupLedger(tmp_path / "l.sha256"), judge=SpyJudge()
    )
    assert seen_contexts, "judge received no contexts"
    assert secret not in (seen_contexts[0] or "")  # raw secret never reaches the judge
    assert "[REDACTED:" in (seen_contexts[0] or "")
    assert rep.redactions >= 1  # antecedent redaction counted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_pipeline.py::test_phase_a_redacts_antecedent_before_judge -v`
Expected: FAIL — either `SpyJudge.judge()` is called without a `contexts` kwarg (TypeError, fixed in Task 6) **or** the antecedent is unredacted. (If this task is implemented before Task 6, the test still fails on the unredacted antecedent once the kwarg exists; if run now it fails on the missing `contexts` plumbing. Either way it is red.)

> Note: this test also depends on Task 6 (pipeline passing `contexts=`). Implement Task 3's redaction change now; the test goes green after Task 6. To verify Task 3 in isolation, temporarily assert on `cand.antecedent` redaction via a unit check, or proceed and confirm green at Task 6. The committed code for Task 3 is the redaction block below.

- [ ] **Step 3: Redact the antecedent in Phase A**

In `src/cairn/ingest/pipeline.py`, in the Phase A per-candidate loop, locate:

```python
            red = redact(cand.text)
            report.redactions += red.count
            cand = replace(cand, text=red.text)
            h = content_hash(cand.text)
```

Insert the antecedent redaction immediately after `cand = replace(cand, text=red.text)` and before `h = content_hash(cand.text)`:

```python
            if cand.antecedent is not None:
                ared = redact(cand.antecedent)
                report.redactions += ared.count
                cand = replace(cand, antecedent=ared.text)
```

(The dedup hash `h = content_hash(cand.text)` is unchanged — antecedent is not part of identity.)

- [ ] **Step 4: Run test to verify it passes**

This test goes green only once Task 6 wires `contexts=` through. After completing Task 6, run:
`uv run pytest tests/ingest/test_pipeline.py::test_phase_a_redacts_antecedent_before_judge -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): redact the antecedent in Phase A before it reaches the judge"
```

---

## Task 4: `Judge` protocol + `EmbeddingJudge` gain a backward-compatible `contexts` kwarg

**Files:**
- Modify: `src/cairn/ingest/judge.py` (`Judge` protocol ~line 29–30; `EmbeddingJudge.judge` ~line 92)
- Test: `tests/ingest/test_judge.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_judge.py`:

```python
def test_embedding_judge_accepts_and_ignores_contexts():
    judge = EmbeddingJudge(StubEmbedder())
    texts = ["D: we decided to always rebase-merge", "E: check CI on PR #76"]
    without = judge.judge(texts)
    with_ctx = judge.judge(texts, contexts=["some prior assistant proposal", None])
    # the embedding tier produces no distillation, so the antecedent is irrelevant
    assert [j.durability for j in with_ctx] == [j.durability for j in without]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_judge.py::test_embedding_judge_accepts_and_ignores_contexts -v`
Expected: FAIL with `TypeError: judge() got an unexpected keyword argument 'contexts'`.

- [ ] **Step 3: Add the kwarg to the protocol and EmbeddingJudge**

In `src/cairn/ingest/judge.py`, change the `Judge` protocol method:

```python
class Judge(Protocol):
    def judge(
        self, texts: list[str], *, contexts: list[str | None] | None = None
    ) -> list[Judgment]: ...
```

Change `EmbeddingJudge.judge` signature (the body is unchanged — it does not read `contexts`):

```python
    def judge(
        self, texts: list[str], *, contexts: list[str | None] | None = None
    ) -> list[Judgment]:
        if not texts:
            return []
        out: list[Judgment] = []
        clipped = [_judge_input(t) for t in texts]
```

(Leave the rest of the method exactly as it is.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_judge.py -v`
Expected: PASS (new test + all existing embedding-judge tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/judge.py tests/ingest/test_judge.py
git commit -m "feat(judge): add backward-compatible contexts kwarg; embedding tier ignores it"
```

---

## Task 5: `LLMJudge` renders the prior-assistant block + resolve-only prompt + threads `contexts`

**Files:**
- Modify: `src/cairn/ingest/judge.py` (`_PROMPT` ~line 118; `LLMJudge.judge` ~line 166; `LLMJudge._judge_llm` ~line 193)
- Test: `tests/ingest/test_judge.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/ingest/test_judge.py`:

```python
def test_resolve_only_instruction_present_in_prompt():
    from cairn.ingest.judge import _PROMPT

    assert "PRIOR ASSISTANT MESSAGE" in _PROMPT
    assert "resolve" in _PROMPT.lower()
    assert "acknowledgement" in _PROMPT.lower() or "contentless" in _PROMPT.lower()


def test_llm_judge_renders_prior_assistant_block_only_when_present(monkeypatch):
    import cairn.ingest.judge as jmod

    bodies = []

    def fake_request(payload, api_key, timeout):
        body = payload["messages"][0]["content"]
        bodies.append(body)
        # answer both numbered inputs
        return {
            "content": [
                {
                    "type": "text",
                    "text": '[{"i":0,"durability":0.1,"title":null,"distilled":null},'
                    '{"i":1,"durability":0.1,"title":null,"distilled":null}]',
                }
            ]
        }

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    judge.judge(["lock A", "we should always rebase-merge"], contexts=["Propose approach A", None])
    body = bodies[0]
    assert "PRIOR ASSISTANT MESSAGE (context only): Propose approach A" in body
    assert "DEVELOPER MESSAGE: lock A" in body
    # item 1 had no antecedent -> rendered plainly, no block
    assert "[1] we should always rebase-merge" in body


def test_llm_judge_contexts_index_aligned_across_chunks(monkeypatch):
    """contexts must stay aligned with texts within each _BATCH_SIZE chunk."""
    import cairn.ingest.judge as jmod

    seen_blocks = []

    def fake_request(payload, api_key, timeout):
        body = payload["messages"][0]["content"]
        # record which numbered items carried a block in this chunk
        for line in body.splitlines():
            if "PRIOR ASSISTANT MESSAGE" in line:
                seen_blocks.append(line)
        import re

        idxs = [int(m) for m in re.findall(r"^\[(\d+)\]", body, flags=re.M)]
        items = [{"i": i, "durability": 0.1, "title": None, "distilled": None} for i in idxs]
        return {"content": [{"type": "text", "text": __import__("json").dumps(items)}]}

    monkeypatch.setattr(jmod, "_anthropic_request", fake_request)
    judge = jmod.LLMJudge(api_key="k", model="m", timeout=5.0)
    n = jmod._BATCH_SIZE + 5  # force two chunks
    texts = [f"msg {i}" for i in range(n)]
    contexts = [f"ctx {i}" if i % 2 == 0 else None for i in range(n)]
    out = judge.judge(texts, contexts=contexts)
    assert len(out) == n
    # every even-index item (in both chunks) produced a block; odd ones did not
    assert len(seen_blocks) == len([c for c in contexts if c])
    assert all(f"ctx {i}" in "\n".join(seen_blocks) for i in range(n) if i % 2 == 0)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/ingest/test_judge.py -k "resolve_only or prior_assistant_block or index_aligned" -v`
Expected: FAIL — `_PROMPT` lacks the instruction; `LLMJudge.judge`/`_judge_llm` don't accept or render `contexts`.

- [ ] **Step 3: Add the prompt rule, the kwarg, and the rendering**

In `src/cairn/ingest/judge.py`, replace `_PROMPT` (lines 118–128) with:

```python
_PROMPT = """You judge whether each numbered message from a developer's coding-agent \
session is a DURABLE memory (decision, preference, lesson, durable fact, strategic \
direction) or EPHEMERAL chatter (task coordination, status checks, one-off process \
instructions). For each, return durability in [0,1] (1 = clearly durable), a short \
descriptive title (<=70 chars), and a crisp 1-2 sentence distillation of the durable \
fact. For ephemeral messages use null title/distilled.

Some items include a "PRIOR ASSISTANT MESSAGE", provided only as context. Use it \
ONLY to resolve a referent that appears in the developer's message — e.g. "A", \
"option (i)", "all three", "that approach", "the second one". When you resolve such \
a referent, write the title and distillation so they stand alone (name what "A" \
was). If the developer's message carries no such referent, or is itself ephemeral, \
ignore the prior message entirely and judge the developer's message exactly as you \
would without it. Never manufacture a decision from a contentless acknowledgement \
("yes", "do it", "ok").
Return ONLY a JSON array: [{"i": <index>, "durability": <float>, "title": <str|null>, \
"distilled": <str|null>}, ...] with one entry per input, in order.

Messages:
"""
```

Change `LLMJudge.judge` to accept and chunk `contexts` index-aligned with `texts`:

```python
    def judge(
        self, texts: list[str], *, contexts: list[str | None] | None = None
    ) -> list[Judgment]:
        if not texts:
            return []
        out: list[Judgment] = []
        for start in range(0, len(texts), _BATCH_SIZE):
            chunk = texts[start : start + _BATCH_SIZE]
            chunk_ctx = (
                contexts[start : start + _BATCH_SIZE] if contexts is not None else None
            )
            try:
                out.extend(self._judge_llm(chunk, chunk_ctx))
            except Exception:
                self.degraded += len(chunk)
                # The fallback itself may fail (e.g. embedder dies mid-run); that
                # must degrade THIS chunk to neutral, not nuke earlier chunks'
                # successful results by escaping judge().
                fell_back: list[Judgment] | None = None
                if self._fallback is not None:
                    try:
                        fell_back = self._fallback.judge(chunk)
                    except Exception:
                        fell_back = None
                if fell_back is None:
                    fell_back = [Judgment(durability=0.5) for _ in chunk]
                # Mark every fallback verdict degraded so the pipeline gates it by
                # the fallback's rule (not the LLM keep rule) and never caches it
                # at the LLM tier — a real LLM verdict must replace it next run.
                out.extend(replace(j, degraded=True) for j in fell_back)
        return out
```

Change `_judge_llm` to render the optional block (replace the first line that builds `numbered`):

```python
    def _judge_llm(
        self, texts: list[str], contexts: list[str | None] | None = None
    ) -> list[Judgment]:
        lines: list[str] = []
        for i, t in enumerate(texts):
            ctx = contexts[i] if contexts is not None else None
            if ctx:
                lines.append(
                    f"[{i}] PRIOR ASSISTANT MESSAGE (context only): {_judge_input(ctx)}\n"
                    f"    DEVELOPER MESSAGE: {_judge_input(t)}"
                )
            else:
                lines.append(f"[{i}] {_judge_input(t)}")
        numbered = "\n".join(lines)
        payload = {
            "model": self._model,
            "max_tokens": 8192,
            "messages": [{"role": "user", "content": _PROMPT + numbered}],
        }
```

(Leave the rest of `_judge_llm` — timeout scaling, request, parsing — unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_judge.py -v`
Expected: PASS (new tests + all existing LLM-judge tests, including `test_llm_judge_chunks_large_batches` and the degradation tests, which call `judge(texts)` with no `contexts` and must still work).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/judge.py tests/ingest/test_judge.py
git commit -m "feat(judge): LLMJudge resolves referents from a redacted prior-assistant block"
```

---

## Task 6: Pipeline Phase B passes `contexts=` to the judge

**Files:**
- Modify: `src/cairn/ingest/pipeline.py` (Phase B judge call in `ingest_transcripts`)
- Test: `tests/ingest/test_pipeline.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_pipeline.py`:

```python
def test_pipeline_passes_antecedent_as_judge_context_and_writes_resolved(tmp_path):
    """The judge receives each candidate's antecedent as context, and a resolved
    distillation is written self-contained; [verbatim] stays the user's words."""
    from cairn.ingest.judge import Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    received = {}

    class ResolvingJudge:
        degraded = 0

        def judge(self, texts, *, contexts=None):
            received["texts"] = list(texts)
            received["contexts"] = list(contexts or [])
            # simulate the LLM resolving "lock A" using the antecedent
            return [
                Judgment(
                    durability=0.8,
                    title="Lock approach A: orderbook representation",
                    distilled="Approach A — the orderbook representation — is the locked direction.",
                )
                for _ in texts
            ]

    t = Transcript(
        session_id="s",
        cwd="/Users/x/p",
        git_branch="main",
        path=tmp_path / "s.jsonl",
        events=[
            _ev(EventKind.AUTHORED_ASSISTANT, "Approach A is the orderbook representation."),
            _ev(EventKind.AUTHORED_USER, "lock A"),
        ],
    )
    vault = tmp_path / "v"
    vault.mkdir()
    rep = ingest_transcripts(
        [t], vault_root=vault, ledger=DedupLedger(tmp_path / "l.sha256"), judge=ResolvingJudge()
    )
    assert received["contexts"] == ["Approach A is the orderbook representation."]
    assert len(rep.written) == 1
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "orderbook representation" in blob  # resolved distillation is self-contained
    assert "- [verbatim] lock A" in blob  # verbatim is still the user's literal turn
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_pipeline.py::test_pipeline_passes_antecedent_as_judge_context_and_writes_resolved -v`
Expected: FAIL — `received["contexts"]` is empty because the pipeline calls `judge.judge([...])` with no `contexts=`.

- [ ] **Step 3: Pass contexts in Phase B**

In `src/cairn/ingest/pipeline.py`, in `ingest_transcripts` Phase B, locate:

```python
            results = judge.judge([pending[i][0].text for i in to_judge])
```

Replace with:

```python
            results = judge.judge(
                [pending[i][0].text for i in to_judge],
                contexts=[pending[i][0].antecedent for i in to_judge],
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/ingest/test_pipeline.py -v`
Expected: PASS — including `test_phase_a_redacts_antecedent_before_judge` from Task 3 (now that `contexts=` is threaded), and all existing pipeline tests (their judges either accept `contexts` via the protocol default or are plain stubs — see note below).

> **Watch-out:** some existing pipeline tests define a local `class SpyJudge` / `LLMish` with `def judge(self, texts):` (no `contexts` kwarg). Passing `contexts=` to those will raise `TypeError`. For each such stub that the suite still exercises, add `, *, contexts=None` to its `judge` signature (a one-line, behavior-preserving change). Run the full file and fix any `TypeError` this way.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/pipeline.py tests/ingest/test_pipeline.py
git commit -m "feat(ingest): pass candidate antecedents to the judge as resolution context"
```

---

## Task 7: Bump `_JUDGE_CACHE_VERSION` (2 → 3)

**Files:**
- Modify: `src/cairn/ingest/judge.py` (`_JUDGE_CACHE_VERSION`)
- Test: `tests/ingest/test_judge.py`

The prompt changed, so cached verdicts from v2 are stale and must be re-judged.

- [ ] **Step 1: Write the failing test**

Append to `tests/ingest/test_judge.py`:

```python
def test_judge_cache_version_is_3_and_discards_v2(tmp_path):
    import json

    from cairn.ingest.judge import _JUDGE_CACHE_VERSION, JudgedCache

    assert _JUDGE_CACHE_VERSION == 3  # bumped for the antecedent-resolution prompt
    p = tmp_path / "j.jsonl"
    p.write_text(json.dumps({"h": "old", "d": 0.9, "tier": "llm", "v": 2}) + "\n")
    assert JudgedCache(p).get("old") is None  # v2 verdict discarded, will be re-judged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/ingest/test_judge.py::test_judge_cache_version_is_3_and_discards_v2 -v`
Expected: FAIL — `_JUDGE_CACHE_VERSION` is still `2`, so the v2 row loads instead of being discarded.

- [ ] **Step 3: Bump the constant**

In `src/cairn/ingest/judge.py`, update the version constant and extend its comment:

```python
# Bump when a change to the judge (prompt, model defaults, output handling, or a
# degradation/caching bug fix) means previously-cached verdicts can no longer be
# trusted. Rows from an older version — and legacy rows with no version at all —
# are discarded on load, so the candidate is re-judged instead of reusing stale
# data. v2: invalidate the silent-timeout era (judge_timeout=10 degraded every
# batch and cached embedding-fallback verdicts as tier "llm"; see 0.9.4).
# v3: the prompt now resolves referents from a prior-assistant block (0.9.6).
_JUDGE_CACHE_VERSION = 3
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/ingest/test_judge.py -v`
Expected: PASS (the existing version-discard test that constructs a `v: _JUDGE_CACHE_VERSION - 1` row still passes — it computes the stale version relative to the constant).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/ingest/judge.py tests/ingest/test_judge.py
git commit -m "fix(judge): bump cache version to 3 (prompt change) so stale verdicts re-judge"
```

---

## Task 8: Version bump 0.9.6 + CHANGELOG

**Files:**
- Modify: `src/cairn/__init__.py`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Bump the version**

In `src/cairn/__init__.py`, change:

```python
__version__ = "0.9.6"
```

- [ ] **Step 2: Add the CHANGELOG entry**

In `CHANGELOG.md`, insert after the `## [Unreleased]` line and before `## [0.9.5] - 2026-06-12`:

```markdown
## [0.9.6] - 2026-06-13

### Fixed
- **Confirmation-style decisions now distill into self-contained memories.** A turn like "lock A" or "go with (i)" previously produced an accurate but context-orphaned note ("Approach A is the decided direction" — A of what?), because the referent lived in the assistant's prior turn, which the user-turns-only model excludes. The LLM judge now receives the nearest preceding assistant turn as **transient, redacted resolution context** and is instructed to use it *only* to resolve a referent already present in your turn — never to manufacture a decision from a bare acknowledgement. `[verbatim]` (your literal words) and the keep-iff-distilled rule are unchanged; the antecedent is never stored. The judged cache is invalidated (v3) so the next sweep re-resolves existing orphaned notes.
```

Add the link reference at the bottom of `CHANGELOG.md`, just above the `[0.9.5]:` line:

```markdown
[0.9.6]: https://github.com/ccf/agentcairn/compare/v0.9.5...v0.9.6
```

- [ ] **Step 3: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (all tests).

- [ ] **Step 4: Commit**

```bash
git add src/cairn/__init__.py CHANGELOG.md
git commit -m "chore(release): 0.9.6 — antecedent-resolved distillation"
```

---

## Final verification (after all tasks)

- [ ] `uv run pytest -q` — full suite green.
- [ ] `uv run ruff check src tests` — clean.
- [ ] Open a PR; wait for CI + Cursor Bugbot; fix any Bugbot findings; rebase-merge `--delete-branch`.
- [ ] Tag `v0.9.6`, push tag, `gh release create v0.9.6` with the CHANGELOG section, confirm the PyPI publish workflow succeeds.
- [ ] **Re-gate the dogfood vault** (rollout decision (a)): back up, clear `~/agentcairn/memories/*.md` + the `<vault_key>.sha256` ledger + the `<vault_key>.judged.jsonl` cache, then `uv run cairn sweep --vault ~/agentcairn`; audit that the previously-orphaned notes ("Approach A locked", "Chose option (i)") now read self-contained.

---

## Self-Review

**Spec coverage:**
- §A antecedent capture → Task 2. ✓
- §B `Candidate.antecedent` field → Task 1. ✓
- §C Phase A redaction (counted in `report.redactions`, not in dedup hash) → Task 3. ✓
- §D judge `contexts` kwarg; EmbeddingJudge ignores → Task 4; LLMJudge consumes → Task 5; pipeline passes → Task 6. ✓
- §E prompt block + resolve-only instruction → Task 5. ✓
- §F `[verbatim]`/keep-rule unchanged, antecedent never stored → verified by Task 6 test (`- [verbatim] lock A` present, antecedent not written). ✓
- §G cache-version bump + re-gate → Task 7 + Final verification. ✓
- Edge cases (no prior assistant, session change, truncation, tool/meta between) → Task 2 tests. ✓
- Test matrix → covered across Tasks 2–7. ✓

**Placeholder scan:** No TBD/TODO; every code step shows complete code. The Task 3 cross-task dependency on Task 6 is called out explicitly with the reason (not a placeholder). ✓

**Type consistency:** `Candidate.antecedent: str | None`; `judge(self, texts, *, contexts: list[str | None] | None = None)` used identically in the protocol, `EmbeddingJudge`, and `LLMJudge`; `_judge_llm(self, texts, contexts=None)`; pipeline passes `contexts=[pending[i][0].antecedent for i in to_judge]`; `_ANTECEDENT_CHARS` defined in `pipeline.py` (Task 2) and referenced in its tests; `_JUDGE_CACHE_VERSION == 3` (Task 7). Consistent across tasks. ✓
