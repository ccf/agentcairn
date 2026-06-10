# `cairn savings` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local, no-telemetry cumulative token-savings ledger — recording each real `recall` (`full_haystack_tokens` vs `recalled_tokens`) — surfaced via `cairn savings` and a SessionStart digest line.

**Architecture:** A new leaf module `src/cairn/usage.py` owns the JSONL ledger (`record`/`summarize`/`oneline`) and the shared `estimate_tokens`. The whole-haystack token total is cached in the index `meta` table at reindex time and read cheaply at recall. Capture is best-effort at two sites (`recall_tool`, CLI `recall`) and can never break or slow retrieval.

**Tech Stack:** Python 3.12 + Typer (CLI), DuckDB (index/meta), pytest. POSIX sh (plugin hook). Spec: `docs/specs/2026-06-10-cairn-savings-design.md`.

---

## File structure

```
src/cairn/usage.py                  # CREATE: ledger + estimate_tokens (leaf; stdlib only)
src/cairn/index/schema.py           # MODIFY: add cached_haystack_tokens(con) helper
src/cairn/index/build.py            # MODIFY: cache haystack_tokens in reconcile()
src/cairn/mcp/tools.py              # MODIFY: best-effort capture in recall_tool
src/cairn/cli.py                    # MODIFY: capture in `recall`; add `savings` command
benchmarks/cairn_bench/token_savings.py  # MODIFY: import estimate_tokens from cairn.usage
plugin/commands/savings.md          # CREATE: /agentcairn:savings
plugin/scripts/session-start.sh     # MODIFY: prepend the savings one-line to the digest
tests/test_usage.py                 # CREATE: ledger unit tests
tests/mcp/test_capture.py           # CREATE: recall_tool capture test
tests/test_cli.py                   # MODIFY: reindex-meta, CLI recall capture, savings command
plugin/tests/test_plugin.py         # MODIFY: savings command frontmatter + digest line
```

Run all Python from the repo root with `uv run`. Commit after each task. The estimator is `(len + 3) // 4` (≈4 chars/token); the SQL equivalent used for the haystack is `CAST((LENGTH(text)+3)/4 AS BIGINT)` — identical per-chunk values.

---

## Task 1: `cairn.usage` core — estimator, gate, ledger write

**Files:**
- Create: `src/cairn/usage.py`
- Test: `tests/test_usage.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_usage.py`:

```python
# SPDX-License-Identifier: Apache-2.0
import json

from cairn import usage


def test_estimate_tokens():
    assert usage.estimate_tokens("") == 0
    assert usage.estimate_tokens(None) == 0
    assert usage.estimate_tokens("abcd") == 1
    assert usage.estimate_tokens("abcde") == 2  # ceil(5/4)


def test_record_appends_row(tmp_path, monkeypatch):
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    usage.record("recall", full=1000, recalled=120, k=5)
    rows = [json.loads(line) for line in led.read_text().splitlines() if line.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "recall"
    assert rows[0]["full"] == 1000
    assert rows[0]["recalled"] == 120
    assert rows[0]["k"] == 5
    assert rows[0]["v"] == 1


def test_record_noop_when_disabled(tmp_path, monkeypatch):
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.setenv("CAIRN_USAGE", "0")
    usage.record("recall", full=1000, recalled=120, k=5)
    assert not led.exists()


def test_record_best_effort_swallows_errors(tmp_path, monkeypatch):
    # Point the ledger at a path whose parent is a FILE, so mkdir/open fails.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(blocker / "nope" / "usage.jsonl"))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    # Must NOT raise.
    usage.record("recall", full=10, recalled=1, k=1)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_usage.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.usage'`.

- [ ] **Step 3: Create `src/cairn/usage.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""Local, no-telemetry token-savings ledger.

Records real recall events ({full_haystack_tokens, recalled_tokens}) to a JSONL
file the user owns, and summarizes them. Best-effort by design: a ledger failure
must NEVER break or slow recall. The estimator here is the single shared one —
the benchmark imports it so the personal number and the published benchmark
number use the identical model. A model of context size, not a measured cost.
"""
from __future__ import annotations

import json
import os
import statistics
from datetime import UTC, datetime
from pathlib import Path

_CHARS_PER_TOKEN = 4
_SCHEMA = 1


def estimate_tokens(text: str | None) -> int:
    """Estimate tokens from character length (~4 chars/token, rounded up).

    Empty/None counts as 0. Deliberately simple and model-agnostic; labeled as an
    estimate wherever it surfaces.
    """
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def enabled() -> bool:
    """Usage tracking is on unless CAIRN_USAGE=0."""
    return os.environ.get("CAIRN_USAGE", "1") != "0"


def ledger_path() -> Path:
    """$CAIRN_USAGE_PATH if set, else ~/.cache/agentcairn/usage.jsonl."""
    env = os.environ.get("CAIRN_USAGE_PATH")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "agentcairn" / "usage.jsonl"


def record(event: str, *, full: int, recalled: int, k: int) -> None:
    """Append one ledger row. No-op when disabled; swallows ALL IO errors so a
    broken/unwritable ledger can never break recall."""
    if not enabled():
        return
    try:
        row = {
            "v": _SCHEMA,
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "k": int(k),
            "full": int(full),
            "recalled": int(recalled),
        }
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # analytics must never break recall
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_usage.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/usage.py tests/test_usage.py
git commit -m "feat(usage): token-savings ledger core (estimate_tokens, record, gate)"
```

---

## Task 2: `cairn.usage` — summarize + oneline

**Files:**
- Modify: `src/cairn/usage.py`
- Test: `tests/test_usage.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_usage.py`:

```python
def _seed(led, rows):
    led.write_text("".join(__import__("json").dumps(r) + "\n" for r in rows))


def test_summarize_aggregates(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    _seed(
        led,
        [
            {"v": 1, "ts": "2026-06-01T00:00:00+00:00", "event": "recall", "k": 5, "full": 1000, "recalled": 100},
            {"v": 1, "ts": "2026-06-03T00:00:00+00:00", "event": "recall", "k": 5, "full": 3000, "recalled": 200},
        ],
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    s = usage.summarize()
    assert s["recalls"] == 2
    assert s["total_full"] == 4000
    assert s["total_recalled"] == 300
    assert s["total_saved"] == 3700
    assert round(s["lifetime_factor"], 4) == round(4000 / 300, 4)
    assert s["first_ts"] == "2026-06-01T00:00:00+00:00"
    assert s["last_ts"] == "2026-06-03T00:00:00+00:00"


def test_summarize_tolerates_garbage(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":800,"recalled":80}\n'
        "not json at all\n"
        '{"v":1,"event":"recall"}\n'  # missing full/recalled -> skipped
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    s = usage.summarize()
    assert s["recalls"] == 1
    assert s["total_saved"] == 720


def test_summarize_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "nope.jsonl"))
    s = usage.summarize()
    assert s["recalls"] == 0
    assert s["total_saved"] == 0


def test_oneline_empty_when_no_data():
    assert usage.oneline({"recalls": 0, "total_saved": 0, "lifetime_factor": 0.0}) == ""


def test_oneline_has_total_and_count():
    s = {"recalls": 318, "total_saved": 2_300_000, "lifetime_factor": 51.0}
    line = usage.oneline(s)
    assert "saved you" in line
    assert "318 recalls" in line
    assert "2.3M" in line
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_usage.py -k 'summarize or oneline' -v`
Expected: FAIL — `AttributeError: module 'cairn.usage' has no attribute 'summarize'`.

- [ ] **Step 3: Implement** — append to `src/cairn/usage.py`:

```python
def summarize(path: Path | None = None) -> dict:
    """Aggregate the ledger into a summary. Tolerant of malformed/partial lines."""
    p = path or ledger_path()
    try:
        text = p.read_text()
    except OSError:
        text = ""
    fulls: list[int] = []
    recs: list[int] = []
    first_ts: str | None = None
    last_ts: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            full = int(row["full"])
            recalled = int(row["recalled"])
        except (ValueError, TypeError, KeyError):
            continue
        fulls.append(full)
        recs.append(recalled)
        ts = row.get("ts")
        if isinstance(ts, str):
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
    total_full = sum(fulls)
    total_recalled = sum(recs)
    factors = [f / r for f, r in zip(fulls, recs) if r > 0]
    return {
        "recalls": len(fulls),
        "total_full": total_full,
        "total_recalled": total_recalled,
        "total_saved": max(0, total_full - total_recalled),
        "mean_factor": statistics.mean(factors) if factors else 0.0,
        "median_factor": statistics.median(factors) if factors else 0.0,
        "lifetime_factor": (total_full / total_recalled) if total_recalled > 0 else 0.0,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _human(n: int) -> str:
    """Compact token count: 2.3M / 12.4K / 980."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def oneline(summary: dict | None = None) -> str:
    """One-line SessionStart string; '' when there are no recalls / no savings.

    The factor is the lifetime ratio total_full/total_recalled (robust to call
    count), not a per-event average.
    """
    s = summary or summarize()
    if s.get("recalls", 0) <= 0 or s.get("total_saved", 0) <= 0:
        return ""
    factor = s.get("lifetime_factor", 0.0)
    return (
        f"\U0001fab9 agentcairn has saved you ~{_human(s['total_saved'])} tokens "
        f"across {s['recalls']} recalls (≈{factor:.0f}× smaller than your full vault)"
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_usage.py -q`
Expected: all pass (9 total in this file now).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/usage.py tests/test_usage.py
git commit -m "feat(usage): summarize + oneline (lifetime factor, garbage-tolerant)"
```

---

## Task 3: Share `estimate_tokens` with the benchmark (DRY)

**Files:**
- Modify: `benchmarks/cairn_bench/token_savings.py`
- Test: `tests/test_usage.py`

- [ ] **Step 1: Write the failing parity test** — append to `tests/test_usage.py`:

```python
def test_benchmark_imports_shared_estimator():
    from cairn_bench import token_savings

    assert token_savings.estimate_tokens is usage.estimate_tokens
    for t in ["", "abc", "x" * 41, "hello world this is a test"]:
        assert token_savings.estimate_tokens(t) == usage.estimate_tokens(t)
```

- [ ] **Step 2: Run it to verify it fails**

Run: `PYTHONPATH=benchmarks uv run pytest tests/test_usage.py::test_benchmark_imports_shared_estimator -v`
Expected: FAIL — `token_savings.estimate_tokens` is the benchmark's own function (not identical object).

- [ ] **Step 3: Re-point the benchmark at the shared estimator** — in `benchmarks/cairn_bench/token_savings.py`, delete the local `_CHARS_PER_TOKEN` constant and the local `estimate_tokens` function body, and re-export the shared one. Replace the import block + definition with:

```python
from cairn.search import get_chunks, search
from cairn.usage import estimate_tokens  # shared estimator (identical to the package)

__all__ = ["estimate_tokens", "full_haystack_tokens", "recalled_tokens", "summarize", "to_markdown"]
```

(Leave `full_haystack_tokens`, `recalled_tokens`, `summarize`, `to_markdown` as-is — they call `estimate_tokens`, which is now the imported one.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `PYTHONPATH=benchmarks uv run pytest tests/test_usage.py -q`
Expected: all pass. Also confirm the benchmark suite still imports cleanly:
Run: `PYTHONPATH=benchmarks uv run python -c "from cairn_bench import token_savings; print(token_savings.estimate_tokens('abcd'))"`
Expected: prints `1`.

- [ ] **Step 5: Commit**

```bash
git add benchmarks/cairn_bench/token_savings.py tests/test_usage.py
git commit -m "refactor(bench): share estimate_tokens from cairn.usage (single estimator)"
```

---

## Task 4: Cache the whole-haystack token total in index meta

**Files:**
- Modify: `src/cairn/index/schema.py`, `src/cairn/index/build.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_reindex_caches_haystack_tokens(tmp_path):
    import duckdb

    from cairn.index.schema import get_meta

    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha beta gamma delta\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    con = duckdb.connect(str(idx))
    cached = get_meta(con, "haystack_tokens")
    assert cached is not None
    # Equals the sum of per-chunk ceil(len/4) over the chunks table.
    expected = con.execute(
        "SELECT COALESCE(SUM(CAST((LENGTH(text)+3)/4 AS BIGINT)),0) FROM chunks"
    ).fetchone()[0]
    assert int(cached) == int(expected)
    assert int(cached) > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_reindex_caches_haystack_tokens -v`
Expected: FAIL — `get_meta(... "haystack_tokens")` is `None`.

- [ ] **Step 3a: Add the read helper** — in `src/cairn/index/schema.py`, add immediately after `get_meta` (the file currently ends `get_meta` at the `return row[0] if row else None` line):

```python
def cached_haystack_tokens(con: duckdb.DuckDBPyConnection) -> int:
    """Whole-haystack token estimate. Reads the value cached at reindex time
    (meta key 'haystack_tokens'); falls back to a one-off scan if absent (an
    index built before this feature). Same per-chunk model as estimate_tokens."""
    cached = get_meta(con, "haystack_tokens")
    if cached is not None:
        try:
            return int(cached)
        except ValueError:
            pass
    row = con.execute(
        "SELECT COALESCE(SUM(CAST((LENGTH(text)+3)/4 AS BIGINT)),0) FROM chunks"
    ).fetchone()
    return int(row[0])
```

- [ ] **Step 3b: Cache it during reconcile** — in `src/cairn/index/build.py`, in `reconcile`, replace the tail:

```python
    if stats.added or stats.updated or stats.deleted or stats.rebuilt:
        build_fts(con)
    return stats
```

with:

```python
    if stats.added or stats.updated or stats.deleted or stats.rebuilt:
        build_fts(con)
    # Cache the whole-haystack token estimate for `cairn savings` (read cheaply
    # at recall time; recomputed only here, off the hot path). Recompute on any
    # change, or once if the key is missing (index built before this feature).
    if (
        stats.added
        or stats.updated
        or stats.deleted
        or stats.rebuilt
        or get_meta(con, "haystack_tokens") is None
    ):
        total = con.execute(
            "SELECT COALESCE(SUM(CAST((LENGTH(text)+3)/4 AS BIGINT)),0) FROM chunks"
        ).fetchone()[0]
        set_meta(con, "haystack_tokens", str(int(total)))
    return stats
```

(`get_meta`/`set_meta` are already imported at the top of `reconcile`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py::test_reindex_caches_haystack_tokens -v`
Expected: PASS. Also run the index/cli suite for regressions: `uv run pytest tests/test_cli.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/index/schema.py src/cairn/index/build.py tests/test_cli.py
git commit -m "feat(index): cache haystack_tokens in meta + cached_haystack_tokens helper"
```

---

## Task 5: Best-effort capture in `recall_tool`

**Files:**
- Modify: `src/cairn/mcp/tools.py`
- Test: `tests/mcp/test_capture.py`

- [ ] **Step 1: Write the failing test** — create `tests/mcp/test_capture.py`:

```python
# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from cairn.cli import app
from cairn.mcp.tools import recall_tool

runner = CliRunner()


def _build_index(tmp_path):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: Alpha\npermalink: a\n---\nalpha apple brewing notes\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    return idx


def test_recall_tool_records_one_row(tmp_path, monkeypatch):
    idx = _build_index(tmp_path)
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    out = recall_tool(str(idx), "apple brewing", embedder="fake", k=3)
    assert out["notes"]  # recall succeeded
    rows = [json.loads(x) for x in led.read_text().splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "recall"
    assert rows[0]["full"] > 0
    assert rows[0]["recalled"] > 0
    assert rows[0]["recalled"] <= rows[0]["full"]


def test_recall_tool_survives_unwritable_ledger(tmp_path, monkeypatch):
    idx = _build_index(tmp_path)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(blocker / "no" / "usage.jsonl"))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    # Recall must still succeed even though the ledger write fails.
    out = recall_tool(str(idx), "apple", embedder="fake", k=3)
    assert "notes" in out
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/mcp/test_capture.py -v`
Expected: FAIL — `test_recall_tool_records_one_row` finds no ledger file (no capture yet).

- [ ] **Step 3: Add capture to `recall_tool`** — in `src/cairn/mcp/tools.py`, change the body of `recall_tool` so the `try/finally` reads the cached haystack total before closing, and records after. Replace:

```python
    finally:
        con.close()
    return {"query": query, "as_of": now.isoformat(), "notes": notes}
```

with:

```python
        full = 0
        try:
            from cairn.index.schema import cached_haystack_tokens

            full = cached_haystack_tokens(con)
        except Exception:
            full = 0
    finally:
        con.close()
    # Best-effort savings ledger — must never break or slow recall.
    try:
        from cairn import usage

        recalled = sum(usage.estimate_tokens(n.get("text")) for n in notes)
        usage.record("recall", full=full, recalled=recalled, k=k)
    except Exception:
        pass
    return {"query": query, "as_of": now.isoformat(), "notes": notes}
```

(The `full = 0 ... ` block goes INSIDE the existing `try:` — i.e. after the `notes.append(note)` loop and before `finally:`. Indent it at the same level as the `for h in hits:` loop.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_capture.py -q`
Expected: 2 passed. Regression check: `uv run pytest tests/mcp/ -q`.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/mcp/tools.py tests/mcp/test_capture.py
git commit -m "feat(mcp): best-effort savings capture in recall_tool"
```

---

## Task 6: Best-effort capture in CLI `recall`

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_cli_recall_records_savings(tmp_path, monkeypatch):
    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha apple brewing\n")
    idx = tmp_path / "i.duckdb"
    assert (
        runner.invoke(
            app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"]
        ).exit_code
        == 0
    )
    led = tmp_path / "usage.jsonl"
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    r = runner.invoke(
        app, ["recall", "apple brewing", "--index", str(idx), "--embedder", "fake", "--no-rerank"]
    )
    assert r.exit_code == 0, r.output
    import json as _j

    rows = [_j.loads(x) for x in led.read_text().splitlines() if x.strip()]
    assert len(rows) == 1
    assert rows[0]["event"] == "recall"
    assert rows[0]["full"] > 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_cli_recall_records_savings -v`
Expected: FAIL — no ledger file written.

- [ ] **Step 3: Add capture to the `recall` command** — in `src/cairn/cli.py`, in the `recall` command, after the `hits = search(...)` line and before the `if not hits:` line, insert:

```python
    # Best-effort savings ledger — must never break recall.
    try:
        from cairn import usage
        from cairn.index.schema import cached_haystack_tokens

        full = cached_haystack_tokens(con)
        recalled = sum(usage.estimate_tokens(h.snippet) for h in hits)
        usage.record("recall", full=full, recalled=recalled, k=k)
    except Exception:
        pass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py::test_cli_recall_records_savings -v`
Expected: PASS. Regression: `uv run pytest tests/test_cli.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): best-effort savings capture in recall"
```

---

## Task 7: `cairn savings` CLI command

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`:

```python
def test_savings_command_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "u.jsonl"))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    r = runner.invoke(app, ["savings"])
    assert r.exit_code == 0, r.output
    assert "No recalls recorded" in r.output


def test_savings_command_reports(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":10000,"recalled":200}\n'
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    monkeypatch.delenv("CAIRN_USAGE", raising=False)
    r = runner.invoke(app, ["savings"])
    assert r.exit_code == 0, r.output
    assert "9,800" in r.output  # 10000 - 200 saved, comma-grouped
    assert "1" in r.output  # recalls


def test_savings_json(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":10000,"recalled":200}\n'
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    r = runner.invoke(app, ["savings", "--json"])
    assert r.exit_code == 0, r.output
    data = json.loads(r.stdout)
    assert data["recalls"] == 1
    assert data["total_saved"] == 9800


def test_savings_oneline(tmp_path, monkeypatch):
    led = tmp_path / "u.jsonl"
    led.write_text(
        '{"v":1,"ts":"2026-06-01T00:00:00+00:00","event":"recall","k":5,"full":10000,"recalled":200}\n'
    )
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(led))
    r = runner.invoke(app, ["savings", "--oneline"])
    assert r.exit_code == 0, r.output
    assert "saved you" in r.stdout


def test_savings_oneline_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_USAGE_PATH", str(tmp_path / "u.jsonl"))
    r = runner.invoke(app, ["savings", "--oneline"])
    assert r.exit_code == 0
    assert r.stdout.strip() == ""
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -k savings -v`
Expected: FAIL — `No such command 'savings'`.

- [ ] **Step 3: Add the `savings` command** — in `src/cairn/cli.py`, add after the `recent` command:

```python
@app.command()
def savings(
    as_json: bool = typer.Option(False, "--json", help="Emit the summary as JSON."),
    oneline: bool = typer.Option(
        False, "--oneline", help="One-line digest string (empty when no data)."
    ),
) -> None:
    """How much context your recalls have saved (local, estimated, no telemetry)."""
    from cairn import usage

    s = usage.summarize()
    if oneline:
        line = usage.oneline(s)
        if line:
            typer.echo(line)
        return
    if as_json:
        typer.echo(json.dumps(s))
        return
    if not usage.enabled():
        typer.echo("Usage tracking is OFF (CAIRN_USAGE=0).")
    if s["recalls"] == 0:
        typer.echo("No recalls recorded yet — use recall and check back.")
        typer.echo(f"(local ledger: {usage.ledger_path()})")
        return
    typer.echo(f"Tokens saved:  ~{s['total_saved']:,}  (estimated, ~4 chars/token)")
    typer.echo(f"Recalls:       {s['recalls']}")
    typer.echo(
        f"Reduction:     {s['lifetime_factor']:.1f}x lifetime  "
        f"({s['mean_factor']:.1f}x mean / {s['median_factor']:.1f}x median per recall)"
    )
    if s["first_ts"] and s["last_ts"]:
        typer.echo(f"Span:          {s['first_ts'][:10]} -> {s['last_ts'][:10]}")
    typer.echo("")
    typer.echo("vs. dumping your whole vault — a model of context size, not a measured cost.")
    typer.echo(f"Local ledger:  {usage.ledger_path()}  (disable with CAIRN_USAGE=0)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k savings -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'cairn savings' (report, --json, --oneline)"
```

---

## Task 8: Plugin `/agentcairn:savings` command

**Files:**
- Create: `plugin/commands/savings.md`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Extend the command-frontmatter test** — in `plugin/tests/test_plugin.py`, find the `test_command_has_frontmatter` parametrize decorator and add `"savings"` to the list:

```python
@pytest.mark.parametrize("cmd", ["recall", "remember", "memory", "ingest", "savings"])
def test_command_has_frontmatter(cmd):
    text = (PLUGIN / "commands" / f"{cmd}.md").read_text()
    assert text.startswith("---")
    assert "description:" in text.split("---", 2)[1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py -k 'command_has_frontmatter and savings' -v`
Expected: FAIL — `plugin/commands/savings.md` missing.

- [ ] **Step 3: Create `plugin/commands/savings.md`**

```markdown
---
description: Show how much context agentcairn has saved you.
---
Run `uvx --from agentcairn cairn savings` and report the total tokens saved, number of recalls, and the reduction factor. Note that it's a local, estimated figure (vs. dumping the whole vault), not a measured dollar cost.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest plugin/tests/test_plugin.py -k command_has_frontmatter -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add plugin/commands/savings.md plugin/tests/test_plugin.py
git commit -m "feat(plugin): /agentcairn:savings command"
```

---

## Task 9: SessionStart digest savings line

**Files:**
- Modify: `plugin/scripts/session-start.sh`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing tests** — append to `plugin/tests/test_plugin.py`:

```python
def test_session_start_includes_savings_line(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    # Stub uvx: `cairn savings --oneline` -> a savings line; `cairn recent --json` -> one note.
    stub.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "savings" ]; then echo "SAVED 1.2M tokens across 9 recalls"; exit 0; fi\n'
        '  if [ "$a" = "recent" ]; then echo \'{"notes":[{"permalink":"a","title":"Note A","path":"a.md"}]}\'; exit 0; fi\n'
        "done\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    (tmp_path / "i.duckdb").write_text("")  # index exists -> digest path
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),
        },
    )
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SAVED 1.2M tokens across 9 recalls" in ctx
    assert "Note A" in ctx  # the recent digest is still present


def test_session_start_no_savings_line_when_empty(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    # savings --oneline prints nothing (no data); recent returns one note.
    stub.write_text(
        "#!/bin/sh\n"
        'for a in "$@"; do\n'
        '  if [ "$a" = "savings" ]; then exit 0; fi\n'
        '  if [ "$a" = "recent" ]; then echo \'{"notes":[{"permalink":"a","title":"Note A","path":"a.md"}]}\'; exit 0; fi\n'
        "done\n"
        "exit 0\n"
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    (tmp_path / "i.duckdb").write_text("")
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {
            "PATH": f"{bindir}:{os.environ['PATH']}",
            "VAULT_ARG": str(vault),
            "INDEX_ARG": str(tmp_path / "i.duckdb"),
        },
    )
    assert r.returncode == 0, r.stderr
    ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "SAVED" not in ctx
    assert "Note A" in ctx
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py -k savings_line -v`
Expected: FAIL — the savings line is not in the digest.

- [ ] **Step 3: Add the savings line to the digest** — in `plugin/scripts/session-start.sh`, replace the digest-build block (from the `LINES=` assignment through the `python3 -c` that emits the JSON). The current block is:

```sh
[ -z "$LINES" ] && exit 0

CTX="## agentcairn — recent memory
$LINES

(Use the \`recall\` tool to pull full notes.)"
python3 -c '
import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.argv[1]}}))
' "$CTX" 2>/dev/null || true
exit 0
```

Replace with:

```sh
# Cumulative savings one-liner (empty when there are no recorded recalls).
SAVINGS=$($CAIRN savings --oneline 2>/dev/null || true)

# Nothing to surface at all → emit nothing.
[ -z "$LINES" ] && [ -z "$SAVINGS" ] && exit 0

CTX=""
[ -n "$SAVINGS" ] && CTX="$SAVINGS
"
if [ -n "$LINES" ]; then
  CTX="$CTX## agentcairn — recent memory
$LINES

(Use the \`recall\` tool to pull full notes.)"
fi
python3 -c '
import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.argv[1]}}))
' "$CTX" 2>/dev/null || true
exit 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest plugin/tests/test_plugin.py -k session -v`
Expected: all session tests pass (existing + the 2 new). Full plugin suite: `uv run pytest plugin/tests/test_plugin.py -q`.

- [ ] **Step 5: Commit**

```bash
git add plugin/scripts/session-start.sh plugin/tests/test_plugin.py
git commit -m "feat(plugin): show cumulative savings line in the SessionStart digest"
```

---

## Self-review (against the spec)

- **§ Goal / surfaces:** `cairn savings` (Task 7), `/agentcairn:savings` (Task 8), SessionStart line (Task 9). ✓
- **§ Architecture — one module owns it:** `src/cairn/usage.py` (Tasks 1–2); leaf, stdlib-only. ✓
- **§ Ledger schema (`v,ts,event,k,full,recalled`, JSONL, no query text, cache dir):** Task 1 `record`. ✓
- **§ Token model — `full` cached in meta, lazy fallback; `recalled` = returned payload:** Task 4 (cache + helper) + Tasks 5/6 (recalled = note text / snippets). ✓
- **§ Capture — recall_tool + CLI recall, best-effort:** Tasks 5, 6 (both wrapped so a ledger failure can't break recall — tested). ✓
- **§ Default on, `CAIRN_USAGE=0`:** Task 1 `enabled()` + tested no-op. ✓
- **§ Tokenizer shared with benchmark:** Task 3 (`token_savings.estimate_tokens is usage.estimate_tokens`). ✓
- **§ Honest labels:** Task 7 report footer ("vs. dumping your whole vault — a model of context size, not a measured cost"). ✓
- **§ Testing:** estimate parity (T3), record/disabled/best-effort (T1), summarize/oneline/garbage (T2), reindex meta (T4), recall_tool + CLI capture + unwritable-survival (T5/T6), CLI text/json/oneline (T7), plugin frontmatter (T8), digest line present/absent (T9). ✓
- **§ Out of scope:** no `--watch`, no search/build_context capture, no tiktoken/dollar cost, no rotation — none added. ✓

**Type/name consistency:** `usage.record(event, *, full, recalled, k)`, `usage.summarize()->dict` keys (`recalls,total_full,total_recalled,total_saved,mean_factor,median_factor,lifetime_factor,first_ts,last_ts`), `usage.oneline(summary)`, `usage.enabled()`, `usage.ledger_path()`, `usage.estimate_tokens()`, `schema.cached_haystack_tokens(con)` — used identically across Tasks 1–9. No placeholders; every code step is complete and copy-pasteable.
```
