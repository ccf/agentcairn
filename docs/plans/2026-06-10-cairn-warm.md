# `cairn warm` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `cairn warm` command that pre-downloads the configured embedder + reranker models (best-effort), and call it from the plugin's detached first-run job so the first sweep/recall isn't slow.

**Architecture:** A new `cairn warm` CLI command resolves the configured embedder (`CAIRN_EMBEDDER`) and reranker (`CAIRN_RERANK`) and constructs each to trigger its one-time model download, wrapped best-effort (exit 0 even on failure). The plugin's SessionStart first-run detached background job adds `cairn warm` after `cairn init`.

**Tech Stack:** Python 3.12 + Typer (CLI), FastEmbed (ONNX models), pytest, POSIX sh (plugin hook). Spec: `docs/specs/2026-06-10-cairn-warm-design.md`.

---

## File structure

```
src/cairn/cli.py                  # MODIFY: add `warm` command (after `savings`)
plugin/scripts/session-start.sh   # MODIFY: first-run detached job adds `; $CAIRN warm`
CHANGELOG.md                      # MODIFY: add `cairn warm` under [Unreleased]
tests/test_cli.py                 # MODIFY: warm command tests
plugin/tests/test_plugin.py       # MODIFY: assert the warm step is wired in
```

`cli.py` already imports `os`, `typer`, `get_embedder` (from `cairn.embed`), and `resolve_rerank` (from `cairn.config`). `rerank_candidates` is imported locally inside the command. Run all Python from the repo root with `uv run`. Commit after each task.

---

## Task 1: `cairn warm` command

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`:

```python
def test_warm_fake_embedder_rerank_off_is_noop(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    monkeypatch.setenv("CAIRN_RERANK", "0")
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert "fake" in r.output  # nothing to warm for the fake embedder
    assert "skipped" in r.output.lower()  # reranker skipped


def test_warm_embedder_failure_is_best_effort(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fastembed")
    monkeypatch.setenv("CAIRN_RERANK", "0")

    def _boom(name):
        raise RuntimeError("download blew up")

    monkeypatch.setattr("cairn.cli.get_embedder", _boom)
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output  # best-effort: never crashes
    assert "fail" in r.output.lower()


def test_warm_warms_reranker_when_enabled(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")  # skip the embedder download
    monkeypatch.delenv("CAIRN_RERANK", raising=False)  # default: rerank ON
    called = {}

    def _spy(query, candidates, **kw):
        called["args"] = (query, candidates)
        return candidates

    monkeypatch.setattr("cairn.search.rerank_candidates", _spy)
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert called["args"][0] == "warm"
    assert called["args"][1] == [{"text": "hello"}]


def test_warm_skips_reranker_when_disabled(monkeypatch):
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    monkeypatch.setenv("CAIRN_RERANK", "0")
    called = {"n": 0}

    def _spy(query, candidates, **kw):
        called["n"] += 1
        return candidates

    monkeypatch.setattr("cairn.search.rerank_candidates", _spy)
    r = runner.invoke(app, ["warm"])
    assert r.exit_code == 0, r.output
    assert called["n"] == 0  # reranker not warmed when disabled
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py -k warm -v`
Expected: FAIL — `No such command 'warm'`.

- [ ] **Step 3: Implement the `warm` command** — in `src/cairn/cli.py`, add after the `savings` command:

```python
@app.command()
def warm() -> None:
    """Pre-download the configured embedder + reranker models (best-effort).

    Reads CAIRN_EMBEDDER (default 'fastembed') and CAIRN_RERANK. Idempotent —
    near-instant once the models are cached. The plugin's detached first-run job
    calls this so the first real sweep/recall isn't slow; also handy before
    first CLI use.
    """
    embedder = os.environ.get("CAIRN_EMBEDDER") or "fastembed"
    if embedder in ("fastembed", "ollama"):
        try:
            get_embedder(embedder)
            typer.echo(f"embedder ready: {embedder}")
        except Exception as exc:  # best-effort pre-fetch — never crash
            typer.echo(f"embedder warm failed ({embedder}): {exc}")
    else:
        typer.echo(f"embedder: nothing to warm ({embedder})")

    if resolve_rerank():
        try:
            from cairn.search import rerank_candidates

            rerank_candidates("warm", [{"text": "hello"}])
            typer.echo("reranker ready")
        except Exception as exc:  # best-effort pre-fetch — never crash
            typer.echo(f"reranker warm failed: {exc}")
    else:
        typer.echo("reranker: skipped (CAIRN_RERANK=0)")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k warm -v`
Expected: 4 passed.
Regression: `uv run pytest tests/test_cli.py -q`.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'cairn warm' — pre-download configured embedder + reranker (best-effort)"
```

---

## Task 2: Wire pre-warm into the plugin + CHANGELOG

**Files:**
- Modify: `plugin/scripts/session-start.sh`
- Modify: `CHANGELOG.md`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing test** — append to `plugin/tests/test_plugin.py`:

```python
def test_session_start_first_run_warms_models():
    text = (PLUGIN / "scripts" / "session-start.sh").read_text()
    # The first-run detached job pre-warms the models (after vault init) so the
    # first sweep/recall isn't slow.
    assert "$CAIRN warm" in text
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py -k warms_models -v`
Expected: FAIL — `$CAIRN warm` not in the script yet.

- [ ] **Step 3: Add the warm step to the detached first-run job** — in `plugin/scripts/session-start.sh`, change the first-run block from:

```sh
if [ ! -f "$INDEX" ]; then
  ( $CAIRN init "$VAULT" ) </dev/null >/dev/null 2>&1 &
  exit 0
fi
```

to:

```sh
if [ ! -f "$INDEX" ]; then
  ( $CAIRN init "$VAULT"; $CAIRN warm ) </dev/null >/dev/null 2>&1 &
  exit 0
fi
```

(Leave the rest of the script — the unconditional `mkdir -p "$VAULT"`, the comment above this block, the recent-fetch, the savings line — unchanged. `$CAIRN` stays unquoted so it word-splits.)

- [ ] **Step 4: Add the CHANGELOG entry** — in `CHANGELOG.md`, under `## [Unreleased]`, add an `### Added` section (the `[Unreleased]` section is currently empty after the 0.3.0 release):

```markdown
## [Unreleased]

### Added
- `cairn warm` — pre-downloads the configured embedder + reranker models (best-effort, config-aware). The plugin's detached first-run job calls it so the first SessionEnd `sweep` and first `recall` aren't slowed by a model download.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest plugin/tests/test_plugin.py -k session -v`
Expected: all session tests pass (the new `warms_models` test + the existing first-run early-exit test, which still exits 0 instantly because the job is detached).
Full plugin suite: `uv run pytest plugin/tests/test_plugin.py -q`.
If `shellcheck` is available (`command -v shellcheck`): `shellcheck -S error plugin/scripts/session-start.sh` — no error-level findings (SC2086 word-splitting on `$CAIRN` is intentional/info-level).

- [ ] **Step 6: Commit**

```bash
git add plugin/scripts/session-start.sh CHANGELOG.md plugin/tests/test_plugin.py
git commit -m "feat(plugin): pre-warm models in the SessionStart detached first-run job"
```

---

## Release (after the PR merges to main)

`cairn warm` is a new command → cut **`0.4.0`** (the established ritual):
1. Bump `__version__` → `"0.4.0"` in `src/cairn/__init__.py`; update the version assertion in `tests/test_cli.py::test_version_flag_prints_version` to `"0.4.0"`.
2. Promote CHANGELOG `## [Unreleased]` → `## [0.4.0] - <date>`; add a fresh empty `[Unreleased]`; update the compare links (`[Unreleased]: …compare/v0.4.0...HEAD`, add `[0.4.0]: …compare/v0.3.0...v0.4.0`).
3. `uv run pytest -q` + `uv build` (confirm `0.4.0` artifacts), commit to `main`.
4. `git tag v0.4.0 && git push origin v0.4.0` (Trusted Publishing → PyPI), then `gh release create v0.4.0 --verify-tag --title v0.4.0 --notes "<CHANGELOG section>"`.

The plugin pre-warm is inert until `0.4.0` is on PyPI (hook pins `agentcairn>=0.2`; `cairn warm` errors into `/dev/null` until then).

---

## Self-review (against the spec)

- **§ `cairn warm` command** (resolve `CAIRN_EMBEDDER`; warm fastembed/ollama, skip fake/none; warm reranker iff `resolve_rerank()`; best-effort per step; exit 0; no flags): Task 1. ✓
- **§ Plugin wiring** (first-run detached job adds `$CAIRN warm`; stays detached/best-effort): Task 2 Step 3. ✓
- **§ Release 0.4.0**: Release section + Task 2 CHANGELOG entry. ✓
- **§ Testing** (fake+rerank-off no-op; best-effort embedder failure; rerank-on warms; rerank-off skips; plugin script includes the step): Task 1 (4 tests) + Task 2 (1 test). ✓
- **§ Out of scope** (no flags, no progress bar, only first-run warm, no daemon): nothing extra added. ✓

**Type/name consistency:** the command is `warm` (no args); reads `os.environ.get("CAIRN_EMBEDDER")` + `resolve_rerank()`; warms via `get_embedder(embedder)` and `rerank_candidates("warm", [{"text": "hello"}])`. The reranker spy patches `cairn.search.rerank_candidates` (the source module the command imports from at call time), matching the implementation's `from cairn.search import rerank_candidates`. No placeholders; every code step is complete.
```
