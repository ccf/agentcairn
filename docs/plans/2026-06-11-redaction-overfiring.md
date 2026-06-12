# Redaction Over-firing Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the entropy heuristic from redacting file paths, URLs, branches, and hyphenated slugs, while preserving zero-leakage on every known secret shape.

**Architecture:** Narrow the entropy candidate token class (`_TOKEN_RE`) so separator-bearing structured identifiers can never form a candidate; restore the one realistic coverage gap (bare 40-char AWS secret values containing `/`/`+`) with a dedicated, mixed-character-guarded pass in `redact()`. Thresholds, hex exemption, and existing pass ordering unchanged.

**Tech Stack:** Python 3.12, stdlib `re`, pytest. Spec: `docs/specs/2026-06-11-redaction-overfiring-design.md`. Branch `fix/redaction-overfiring` (exists, spec committed).

---

## File structure

```
src/cairn/ingest/redact.py     # MODIFY: _TOKEN_RE class; new aws_secret_value pass
tests/ingest/test_redact.py    # MODIFY: false-positive fixtures + leak/guard tests
src/cairn/__init__.py          # MODIFY: __version__ -> 0.7.1
CHANGELOG.md                   # MODIFY: 0.7.1 section
```

Run all Python from the repo root with `uv run`. Commit after each task. Pre-commit runs ruff + pytest; if ruff reformats, re-add and re-commit.

---

## Task 1: Narrow the entropy token class (kills the false positives)

**Files:**
- Modify: `src/cairn/ingest/redact.py:64` (`_TOKEN_RE`)
- Test: `tests/ingest/test_redact.py`

- [ ] **Step 1: Write the failing false-positive tests** — append to `tests/ingest/test_redact.py`:

```python
# ---------------------------------------------------------------------------
# Over-firing fix — structured identifiers must SURVIVE the entropy net
# (real damage observed in the 2026-06-11 vault audit)
# ---------------------------------------------------------------------------

OVERFIRE_SURVIVORS = [
    ("plugin_cache_path", "/Users/ccf/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/brainstorming"),
    ("github_url", "https://github.com/ccf/agentcairn/blob/main/CHANGELOG.md"),
    ("git_branch", "the branch feat/v1.1-bitemporal-validity-and-recall has the fix"),
    ("skill_slug", "use superpowers:subagent-driven-development for this"),
    ("plan_filename", "see docs/plans/2026-06-10-agentcairn-claude-code-plugin.md for details"),
    ("permalink_slug", "permalink: all-of-the-above-angles-are-31b5c3dc"),
]


@pytest.mark.parametrize("name,text", OVERFIRE_SURVIVORS, ids=[s[0] for s in OVERFIRE_SURVIVORS])
def test_structured_identifiers_survive_unredacted(name, text):
    result = redact(text)
    assert result.text == text, f"{name} was wrongly redacted: {result.text!r}"
    assert result.count == 0
```

- [ ] **Step 2: Run to verify they fail against current code**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_redact.py -k survive_unredacted -q`
Expected: FAIL — several cases redacted as `high_entropy` (e.g. the plugin path and skill slug).

- [ ] **Step 3: Narrow `_TOKEN_RE`** — in `src/cairn/ingest/redact.py`, replace:

```python
_TOKEN_RE = re.compile(rf"[A-Za-z0-9+/_-]{{{_ENTROPY_MIN_LEN},}}")
```

with:

```python
# No '/' or '-' in the class: paths, URLs, branches, and hyphenated slugs must
# never form an entropy candidate (2026-06-11 audit: 571 high_entropy hits were
# ~99% structured identifiers). Separator-bearing bare secrets are covered by
# the dedicated aws_secret_value pass; known vendor shapes by named patterns.
_TOKEN_RE = re.compile(rf"[A-Za-z0-9+_]{{{_ENTROPY_MIN_LEN},}}")
```

- [ ] **Step 4: Run the full redact suite — golden corpus must stay green**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_redact.py -q`
Expected: ALL pass — the new survivors AND every pre-existing test (`test_high_entropy_token_redacted` still fires on its contiguous token; the 64-char hex tests still fire; `aws_secret_access_key=` is still caught by `secret_assignment`).

- [ ] **Step 5: Commit**

```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/redact.py tests/ingest/test_redact.py && git commit -m "fix(redact): exclude / and - from entropy token class (stop over-redacting paths/slugs)"
```

---

## Task 2: Bare AWS-secret-value pass (restores the coverage gap)

**Files:**
- Modify: `src/cairn/ingest/redact.py` (new constant + pass in `redact()`)
- Test: `tests/ingest/test_redact.py`

- [ ] **Step 1: Write the failing leak + guard tests** — append to `tests/ingest/test_redact.py`:

```python
# ---------------------------------------------------------------------------
# Bare AWS secret value — exactly-40-char base64 (may contain / and +), no
# key-name prefix. The narrowed entropy class can't span '/', so this shape
# needs its own pattern. Guarded: must contain upper+lower+digit.
# ---------------------------------------------------------------------------

_BARE_AWS = "wJalr/UtnFEMIK7MDENGbPxRfiCY+EXAMPLEKEYz"  # 40 chars, has / + upper lower digit


def test_bare_aws_secret_value_redacted():
    text = f"the old secret was {_BARE_AWS} rotate it"
    result = redact(text)
    assert result.count >= 1, "bare 40-char AWS secret value was not redacted"
    assert _BARE_AWS not in result.text
    assert "aws_secret_value" in result.kinds


def test_contiguous_base64_still_caught_by_entropy():
    # 32+ contiguous mixed-case alnum (no separators) — entropy net territory
    tok = "Zk9Q2mVx7Lp4Rt6Yw1Nf3Hd8Bc5Jg0Ks2Pv4Ua7"
    result = redact(f"value {tok} end")
    assert result.count >= 1
    assert tok not in result.text


def test_aws_guard_rejects_lowercase_only():
    # 40 chars, lowercase-only -> fails the upper+lower+digit guard. Deliberately
    # repetitive (low Shannon entropy) so the entropy net can't fire either —
    # this isolates the aws_secret_value guard.
    s = "abcdabcdabcdabcdabcdabcdabcdabcdabcdabcd"
    assert len(s) == 40
    result = redact(s)
    assert result.text == s
    assert result.count == 0


def test_aws_guard_rejects_40char_hex_sha():
    sha = "f3d17de96b66ad5f56a3f29cf8bcb57b7aed83fe"  # git SHA-1: single-case hex
    result = redact(f"commit {sha} on main")
    assert sha in result.text
    assert result.count == 0
```

- [ ] **Step 2: Run to verify the leak test fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_redact.py -k aws_secret_value -q`
Expected: FAIL — `test_bare_aws_secret_value_redacted` (no `aws_secret_value` kind exists yet). The two guard tests may already pass (nothing fires today); that's fine.

- [ ] **Step 3: Add the pattern + guarded pass** — in `src/cairn/ingest/redact.py`:

(a) Below the `_PATTERNS` list, add:

```python
# Bare AWS-style secret value: exactly 40 chars of base64-ish material (may
# contain '/' and '+'), standing alone (not inside a longer run). The narrowed
# _TOKEN_RE cannot span '/', so this shape gets a dedicated, guarded pass.
_AWS_SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40}(?![A-Za-z0-9+/=])")


def _mixed_charset(token: str) -> bool:
    return (
        bool(re.search(r"[A-Z]", token))
        and bool(re.search(r"[a-z]", token))
        and bool(re.search(r"[0-9]", token))
    )
```

(b) In `redact()`, add a pass AFTER the named-pattern loop (so precise vendor kinds win first) and before the `return`:

```python
    # Pass 3: bare AWS-style secret values — exactly-40-char base64 runs with a
    # mixed-charset guard (hex SHAs and prose-ish strings are single-case or
    # letter-only and never fire).
    def _aws_sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if _mixed_charset(tok):
            kinds.append("aws_secret_value")
            return "[REDACTED:aws_secret_value]"
        return tok

    out = _AWS_SECRET_VALUE_RE.sub(_aws_sub, out)
```

- [ ] **Step 4: Run the full redact suite**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/ingest/test_redact.py -q`
Expected: ALL pass (new + golden + survivors).

- [ ] **Step 5: Full regression + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass.

```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/ingest/redact.py tests/ingest/test_redact.py && git commit -m "feat(redact): guarded bare aws_secret_value pattern (covers separator-bearing 40-char secrets)"
```

---

## Task 3: Corpus replay — prove the delta on real data

**Files:** none committed (manual verification; results go in the PR description).

- [ ] **Step 1: Run old-vs-new redaction over the real transcripts' authored turns**

Run from the repo root:

```bash
cd /Users/ccf/git/agentcairn && uv run python - <<'PY'
import re
from collections import Counter
from cairn.ingest.locate import find_transcripts, parse_transcript
from cairn.ingest.events import EventKind
from cairn.ingest import redact as new_redact

# Reconstruct the OLD behavior: wide token class, no aws_secret_value pass.
import cairn.ingest.redact as rmod
old_token = re.compile(r"[A-Za-z0-9+/_-]{24,}")

def count_kinds(redact_fn):
    kinds = Counter()
    for tp in find_transcripts():
        for e in parse_transcript(tp).events:
            if e.kind == EventKind.AUTHORED_USER:
                kinds.update(redact_fn(e.text).kinds)
    return kinds

new_counts = count_kinds(rmod.redact)

saved = rmod._TOKEN_RE
rmod._TOKEN_RE = old_token
saved_aws = rmod._AWS_SECRET_VALUE_RE
rmod._AWS_SECRET_VALUE_RE = re.compile(r"(?!x)x")  # never matches -> disables the new pass
old_counts = count_kinds(rmod.redact)
rmod._TOKEN_RE = saved
rmod._AWS_SECRET_VALUE_RE = saved_aws

print("OLD:", dict(old_counts))
print("NEW:", dict(new_counts))
PY
```

Expected: OLD shows `high_entropy` in the hundreds; NEW shows `high_entropy` near single digits, with any `aws_secret_value` hits individually reviewable. If NEW `high_entropy` remains high (>20), STOP and inspect the surviving matches before proceeding — do not rationalize.

- [ ] **Step 2: Record the before/after numbers** — paste both `OLD:`/`NEW:` lines into the eventual PR description (Task 4's commit message footer is fine as a scratch note; no repo file changes).

---

## Task 4: Release 0.7.1

**Files:**
- Modify: `CHANGELOG.md`, `src/cairn/__init__.py`

- [ ] **Step 1: Add the CHANGELOG section** — replace:

```
## [Unreleased]

## [0.7.0] - 2026-06-11
```

with:

```
## [Unreleased]

## [0.7.1] - 2026-06-11

### Fixed
- **Redaction no longer swallows paths, URLs, branches, or hyphenated slugs.** The entropy heuristic's candidate token class no longer includes `/` or `-`, so structured identifiers can't form a candidate by construction (the 2026-06-11 vault audit found 571 `high_entropy` redactions that were ~99% false positives on such identifiers). A new guarded `aws_secret_value` pattern covers the one realistic separator-bearing bare secret shape (exactly-40-char base64 with upper+lower+digit). All known vendor key shapes remain covered by the named patterns; the golden zero-leakage corpus is unchanged and passing.

## [0.7.0] - 2026-06-11
```

Then update the link refs at the bottom: `[Unreleased]:` compare becomes `v0.7.1...HEAD`, and add `[0.7.1]: https://github.com/ccf/agentcairn/compare/v0.7.0...v0.7.1` above the `[0.7.0]` line.

- [ ] **Step 2: Bump the version** — in `src/cairn/__init__.py`, change `__version__ = "0.7.0"` to `__version__ = "0.7.1"`.

- [ ] **Step 3: Verify + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass.

```bash
cd /Users/ccf/git/agentcairn && git add CHANGELOG.md src/cairn/__init__.py && git commit -m "chore(release): 0.7.1 — redaction over-firing fix"
```

(Tag/push/PyPI/GitHub-Release follow the cut-a-release ritual after the PR merges. The vault rebuild — clear ledger, re-sweep, gated on `cairn ingest --dry-run` — runs after this ships.)

---

## Self-review (against the spec)

- **§ Change 1** (narrow `_TOKEN_RE`, no `/`/`-`): Task 1 Step 3. ✓
- **§ Change 2** (guarded `aws_secret_value`, runs with named patterns after entropy): Task 2 Step 3 — placed after the named-pattern loop so vendor kinds win. ✓
- **§ Change 3/4** (thresholds, hex exemption, ordering untouched): no task touches them. ✓
- **§ Validation 1** (golden corpus zero-leakage): Task 1 Step 4 + Task 2 Step 4 run the full file. ✓
- **§ Validation 2** (leak tests): Task 2 Step 1 (`_BARE_AWS`, contiguous base64). ✓
- **§ Validation 3** (false-positive fixtures from real damage): Task 1 Step 1 — all six. ✓
- **§ Validation 4** (guard negatives: lowercase prose, hex SHA): Task 2 Step 1. ✓
- **§ Validation 5** (corpus replay with per-kind delta): Task 3. ✓
- **§ Rollout 0.7.1**: Task 4. ✓
- **§ Out of scope** (wordiness exemption, in-place note repair, title/tag flow): none added. ✓

**Type/name consistency:** `_TOKEN_RE`, `_AWS_SECRET_VALUE_RE`, `_mixed_charset(token)->bool`, kind string `"aws_secret_value"` used identically in code and tests. `_BARE_AWS` is exactly 40 chars of `[A-Za-z0-9+/]` with upper+lower+digit. No placeholders.
