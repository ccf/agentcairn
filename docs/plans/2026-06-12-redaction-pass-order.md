# Redaction Pass-Order Fix + JudgedCache Tier-Awareness (0.9.1)

> **For agentic workers:** executing-plans style (inline). TDD per task.

**Goal:** Stop named-vendor keys (`sk-ant-…`, `sk-proj-…`, Slack, GitHub) from being fragmented (and partially leaked) by the entropy pass; make the LLM judge cache not be suppressed by stale embedding-tier verdicts.

**Root causes:** (1) `redact()` runs the entropy heuristic BEFORE the named-pattern pass; since 0.7.1 narrowed the entropy token class to exclude `-`, entropy matches only the hyphen-free middle of a hyphenated key, replaces it, and breaks the contiguous run the named pattern needs — so the key's ends survive. Found dogfooding 0.9.0 on a real `sk-ant-` key. (2) `JudgedCache` is tier-blind: an embedding-fallback verdict cached during a key-less window permanently suppresses an available LLM tier.

---

## Task 1: Named patterns before entropy (SECURITY)

**Files:** `src/cairn/ingest/redact.py`, `tests/ingest/test_redact.py`

- [ ] **Step 1: Failing regression test** — append to `tests/ingest/test_redact.py`:

```python
import re as _re


def test_hyphenated_vendor_key_not_fragmented():
    """A hyphenated sk-ant key must be redacted WHOLE by the named pattern, not
    sliced by the entropy pass (which would leak the hyphen-delimited ends).
    Found dogfooding 0.9.0 — the golden test only checks the full contiguous
    string is gone, so fragmentation slipped through."""
    key = "sk-ant-api03-aBcd1234EfGh5678IjKl90MnOpQrStUvWxYz-aB12cd34Ef56_gh78iJkLmN"
    r = redact(f"here's the key: {key} thanks")
    assert "anthropic_key" in r.kinds  # matched by the PRECISE pattern, not high_entropy
    # NO fragment of the key survives (prefix, middle, or hyphen-delimited tail)
    assert not _re.search(r"sk-ant-[A-Za-z0-9_]{2,}", r.text), f"key fragment leaked: {r.text!r}"
    for frag in ("api03", "iJkLmN", "aB12cd34"):
        assert frag not in r.text, f"key fragment {frag!r} leaked: {r.text!r}"


def test_named_pattern_wins_over_entropy_kind():
    """A token matching both a named pattern and the entropy heuristic is labeled
    by the precise named kind (named pass runs first)."""
    r = redact("token ghp_16C7e42F292c6912E7710c838347Ae178B4a end")
    assert "github_token" in r.kinds
    assert "high_entropy" not in r.kinds
```

- [ ] **Step 2: Verify fail** — `uv run pytest tests/ingest/test_redact.py -k 'fragmented or wins_over' -q` → both FAIL (key fragments leak; kind is high_entropy).

- [ ] **Step 3: Reorder passes** in `src/cairn/ingest/redact.py` — move the named-pattern loop ABOVE the entropy pass. Replace the block from "Pass 2: entropy heuristic" comment through the `_PATTERNS` loop with:

```python
    # Pass 2: named-pattern regexes — PRECISE known-credential shapes run BEFORE
    # the entropy heuristic, so a vendor key (e.g. sk-ant-…, with hyphens) is
    # consumed whole. Running entropy first would slice the hyphen-free middle of
    # such a key and leak its hyphen-delimited ends (dogfood bug, 2026-06-12).
    for kind, pat in _PATTERNS:

        def _sub(m: re.Match[str], _kind: str = kind) -> str:
            kinds.append(_kind)
            return f"[REDACTED:{_kind}]"

        out = pat.sub(_sub, out)

    # Pass 3: entropy heuristic — last-resort catch-all for long high-entropy
    # tokens that no named pattern recognized.
    def _entropy_sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if _looks_secret(tok):
            kinds.append("high_entropy")
            return "[REDACTED:high_entropy]"
        return tok

    out = _TOKEN_RE.sub(_entropy_sub, out)
```

Update the `redact()` docstring's pass list to: URL-cred → aws_secret_value → named patterns → entropy.

- [ ] **Step 4: Run full redact suite + commit** — `uv run pytest tests/ingest/test_redact.py -q` (golden corpus + new tests all pass; the 64-char hex / contiguous-base64 entropy tests still fire — those aren't named, so entropy still catches them). Then full suite `uv run pytest -q`.

```bash
git add src/cairn/ingest/redact.py tests/ingest/test_redact.py && git commit -m "fix(redact): named patterns before entropy — stop fragmenting/leaking vendor keys"
```

---

## Task 2: JudgedCache tier-awareness

**Files:** `src/cairn/ingest/judge.py`, `src/cairn/ingest/pipeline.py`, tests

- [ ] **Step 1: Failing tests** — append to `tests/ingest/test_judge.py`:

```python
def test_judged_cache_records_tier(tmp_path):
    from cairn.ingest.judge import JudgedCache, Judgment

    c = JudgedCache(tmp_path / "j.jsonl")
    c.put("h1", Judgment(durability=0.3), tier="embedding")
    c.put("h2", Judgment(durability=0.9, title="T", distilled="D."), tier="llm")
    c2 = JudgedCache(tmp_path / "j.jsonl")
    assert c2.get("h1") == (Judgment(durability=0.3), "embedding")
    assert c2.get("h2")[1] == "llm"
    assert c2.get("missing") is None
```

and to `tests/ingest/test_pipeline.py`:

```python
def test_llm_run_ignores_embedding_cache_entry(tmp_path):
    """An embedding-tier cache entry must NOT suppress an available LLM tier
    (the key-less-window poisoning bug)."""
    from cairn.ingest.dedup import content_hash
    from cairn.ingest.judge import JudgedCache, Judgment
    from cairn.ingest.pipeline import ingest_transcripts

    text = "We decided to always rebase-merge approved PRs because it is important."
    cache = JudgedCache(tmp_path / "j.jsonl")
    cache.put(content_hash(text), Judgment(durability=0.1), tier="embedding")  # gated, no distill

    class LLMish:
        degraded = 0

        def judge(self, texts):
            return [Judgment(durability=0.9, title="Rebase policy", distilled="Always rebase-merge.") for _ in texts]

    vault = tmp_path / "v"
    vault.mkdir()
    t = Transcript(session_id="s", cwd="/Users/x/p", git_branch="main",
                   path=tmp_path / "s.jsonl", events=[_ev(EventKind.AUTHORED_USER, text)])
    rep = ingest_transcripts([t], vault_root=vault, ledger=DedupLedger(tmp_path / "l.sha256"),
                             judge=LLMish(), judged_cache=JudgedCache(tmp_path / "j.jsonl"))
    blob = "\n".join(p.read_text() for p in vault.rglob("*.md"))
    assert "Always rebase-merge." in blob  # LLM re-judged despite the embedding cache entry
```

- [ ] **Step 2: Verify fail.**

- [ ] **Step 3: Implement.** In `src/cairn/ingest/judge.py`:
  - Add tier rank helper + change `JudgedCache`:
    ```python
    _TIER_RANK = {"none": 0, "embedding": 1, "llm": 2}
    ```
  - `put(self, h, judgment, tier="embedding")`: persist `{"h","d","tier",[ "t","s"]}`. Idempotent on (judgment, tier).
  - `_mem: dict[str, tuple[Judgment, str]]`; loader reads `tier` (default `"embedding"` for legacy rows missing it).
  - `get(self, h) -> tuple[Judgment, str] | None`.
  - Module helper `def tier_at_least(cached_tier: str, current_tier: str) -> bool: return _TIER_RANK.get(cached_tier, 0) >= _TIER_RANK.get(current_tier, 0)`.

  In `src/cairn/ingest/pipeline.py`, Phase A cache lookup becomes tier-aware (the run's tier is `report.judge_tier`, set before Phase A):
    ```python
    from cairn.ingest.judge import tier_at_least
    ...
    if judge is not None and judged_cache is not None:
        entry = judged_cache.get(h)
        if entry is not None and tier_at_least(entry[1], report.judge_tier):
            judged[len(pending)] = entry[0]
    ```
  Phase C put passes the run tier: `judged_cache.put(h, j, report.judge_tier)`.

- [ ] **Step 4: Run full suite + commit.** (Update the two existing JudgedCache tests from the prior PR to the new `get` 2-tuple / `put(..., tier=...)` signature.)

```bash
git add src/cairn/ingest/judge.py src/cairn/ingest/pipeline.py tests/ && git commit -m "fix(judge): tier-aware JudgedCache — embedding verdict never blocks the LLM tier"
```

---

## Task 3: Corpus replay + 0.9.1

- [ ] **Step 1: Redaction corpus replay** — over real transcripts' authored turns, count `redact().kinds`; confirm `anthropic_key` now fires where the dogfood key appears and no `sk-ant-` fragment survives in any redacted output. (Local check; numbers in the PR.)
- [ ] **Step 2: CHANGELOG `## [0.9.1]` + `__version__ = "0.9.1"`.** Entry: "fix: named secret patterns now run before the entropy heuristic, so hyphenated vendor keys (`sk-ant-…` etc.) are redacted whole instead of fragmented (a fragment could previously survive); the LLM-judge cache is now tier-aware so a prior embedding-only verdict no longer suppresses the LLM tier." Link refs.
- [ ] **Step 3: Full suite + commit + PR.**

---

## Self-review
- Pass reorder fixes fragmentation; named kind wins; entropy still catches unknown shapes (golden hex/base64 tests). ✓
- Cache tier-aware; legacy rows default embedding (so the poison auto-heals — an LLM run re-judges them). ✓
- 0.9.1, backward-compatible. ✓
