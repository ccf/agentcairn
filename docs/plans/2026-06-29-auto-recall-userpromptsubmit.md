# Auto per-turn recall (UserPromptSubmit) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agentcairn run a hybrid recall against each substantive Claude Code prompt and inject the hits as context, closing the gap where recall was never triggered.

**Architecture:** A thin Python command `cairn recall-hook` holds all logic (config gate, trivial-prompt gate, hybrid recall, formatting, fail-open). A `UserPromptSubmit` hook calls a small shell wrapper that execs it synchronously (its stdout is the injected context). SessionStart pre-warms the embedder. The SessionStart recency digest is unchanged.

**Tech Stack:** Python 3 (Typer CLI, DuckDB-backed `search()`), `pytest` + Typer `CliRunner` + `FakeEmbedder` for hermetic tests, POSIX `sh` plugin hooks.

## Global Constraints

- **Fail-open, always exit 0:** `recall-hook` and `user-prompt-submit.sh` must NEVER raise, block, or exit non-zero — a `UserPromptSubmit` hook that exits non-zero *blocks the prompt*. Every failure path returns `""` / emits nothing.
- **Flat config keys:** new knobs are top-level `config.toml` keys (`auto_recall`, `auto_recall_k`, `auto_recall_scope`) → `CAIRN_AUTO_RECALL{,_K,_SCOPE}`. The loader does not read `[section]` tables.
- **Rerank OFF** on the recall-hook hot path (latency).
- **Plugin entries use `${CLAUDE_PLUGIN_ROOT}`**, never `${user_config.*}` — the guard test `test_hooks_do_not_hardfail_on_unset_vault_path` asserts `"${user_config.vault_path}" not in json.dumps(hooks)` over the whole file. Resolve the vault inside the script from `$CLAUDE_PLUGIN_OPTION_VAULT_PATH`.
- **Hermetic tests:** build indexes with `FakeEmbedder` (`get_embedder("fake")`) / `--embedder fake`; never load real fastembed in tests.
- **Plugin tests run outside default `testpaths`:** run them explicitly with `uv run pytest plugin/tests/test_plugin.py`.
- **Commit trailer:** end every commit message with the repo's standard trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01UyeqgET1ZMb59jeZLiREvD
  ```
- **Run the suite** with `uv run pytest` from the repo root.

## File Structure

- `src/cairn/config.py` — *modify*: add 3 `KNOBS` entries + 3 `resolve_*()` functions + one default constant.
- `src/cairn/recall_hook.py` — *create*: all auto-recall logic (pure units + `run()` orchestrator).
- `src/cairn/cli.py` — *modify*: add the `recall-hook` Typer command (thin stdin→`run()` shim).
- `plugin/scripts/user-prompt-submit.sh` — *create*: synchronous wrapper that execs `cairn recall-hook`.
- `plugin/scripts/session-start.sh` — *modify*: add a detached `cairn warm` on the warm path.
- `plugin/hooks/hooks.json` — *modify*: add the `UserPromptSubmit` entry.
- `plugin/.claude-plugin/plugin.json` — *modify*: version bump `0.3.1` → `0.4.0`.
- `tests/test_config_auto_recall.py` — *create*: resolver tests.
- `tests/test_recall_hook.py` — *create*: core-logic tests (direct `run()` + pure units).
- `tests/test_recall_hook_cli.py` — *create*: one CliRunner stdin-wiring test.
- `plugin/tests/test_plugin.py` — *modify*: assert the `UserPromptSubmit` wiring.
- `README.md`, `CHANGELOG.md` — *modify*: document auto-recall + config; changelog entry.

---

### Task 1: Config knobs + resolvers

**Files:**
- Modify: `src/cairn/config.py` (KNOBS tuple ~line 49-104; add resolvers near `resolve_consolidate` ~line 229)
- Test: `tests/test_config_auto_recall.py` (create)

**Interfaces:**
- Produces:
  - `resolve_auto_recall(env: Mapping[str, str] | None = None) -> bool` (default `True`)
  - `resolve_auto_recall_k(env: Mapping[str, str] | None = None) -> int` (default `3`)
  - `resolve_auto_recall_scope(env: Mapping[str, str] | None = None) -> str` (default `"all"`, lower-cased)
- Consumes: existing `parse_bool`, `cairn_env`, `Knob`, `KNOBS` from the same module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config_auto_recall.py`:

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.config import (
    resolve_auto_recall,
    resolve_auto_recall_k,
    resolve_auto_recall_scope,
)


def test_auto_recall_default_on():
    assert resolve_auto_recall(env={}) is True


def test_auto_recall_off():
    assert resolve_auto_recall(env={"CAIRN_AUTO_RECALL": "0"}) is False
    assert resolve_auto_recall(env={"CAIRN_AUTO_RECALL": "false"}) is False


def test_auto_recall_bad_value_falls_back_true():
    assert resolve_auto_recall(env={"CAIRN_AUTO_RECALL": "maybe"}) is True


def test_auto_recall_k_default():
    assert resolve_auto_recall_k(env={}) == 3


def test_auto_recall_k_override():
    assert resolve_auto_recall_k(env={"CAIRN_AUTO_RECALL_K": "5"}) == 5


def test_auto_recall_k_bad_falls_back():
    assert resolve_auto_recall_k(env={"CAIRN_AUTO_RECALL_K": "lots"}) == 3


def test_auto_recall_scope_default():
    assert resolve_auto_recall_scope(env={}) == "all"


def test_auto_recall_scope_override_lowercased():
    assert resolve_auto_recall_scope(env={"CAIRN_AUTO_RECALL_SCOPE": "Project"}) == "project"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config_auto_recall.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_auto_recall'`.

- [ ] **Step 3: Add the KNOBS entries**

In `src/cairn/config.py`, inside the `KNOBS` tuple (after the `consolidate` `Knob`, before the closing `)`), add:

```python
    Knob(
        "auto_recall",
        "CAIRN_AUTO_RECALL",
        "true",
        "Auto-recall relevant memory before each substantive prompt (Claude Code).",
    ),
    Knob(
        "auto_recall_k",
        "CAIRN_AUTO_RECALL_K",
        "3",
        "How many memories auto-recall injects per prompt.",
    ),
    Knob(
        "auto_recall_scope",
        "CAIRN_AUTO_RECALL_SCOPE",
        "all",
        "Auto-recall scope: 'all' (boost, non-lossy) or 'project' (hard filter).",
    ),
```

- [ ] **Step 4: Add the default constant + resolvers**

In `src/cairn/config.py`, near the other `_DEFAULT_*` constants add:

```python
_DEFAULT_AUTO_RECALL_K = 3
```

After `resolve_consolidate` add:

```python
def resolve_auto_recall(env: Mapping[str, str] | None = None) -> bool:
    """Resolve auto-recall on/off: CAIRN_AUTO_RECALL env/file → True.
    An unparseable value falls back to the default (True) rather than raising,
    so a typo never disables recall silently or breaks a prompt."""
    if env is None:
        env = cairn_env()
    raw = env.get("CAIRN_AUTO_RECALL")
    if raw is None:
        return True
    try:
        return parse_bool(raw)
    except ValueError:
        return True


def resolve_auto_recall_k(env: Mapping[str, str] | None = None) -> int:
    """Resolve auto-recall depth: CAIRN_AUTO_RECALL_K env/file → 3.
    An unparseable value falls back to the default rather than raising."""
    if env is None:
        env = cairn_env()
    try:
        return int(env.get("CAIRN_AUTO_RECALL_K") or _DEFAULT_AUTO_RECALL_K)
    except ValueError:
        return _DEFAULT_AUTO_RECALL_K


def resolve_auto_recall_scope(env: Mapping[str, str] | None = None) -> str:
    """Resolve auto-recall scope: CAIRN_AUTO_RECALL_SCOPE env/file → 'all'."""
    if env is None:
        env = cairn_env()
    return (env.get("CAIRN_AUTO_RECALL_SCOPE") or "all").strip().lower()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_config_auto_recall.py -v`
Expected: PASS (8 passed).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/config.py tests/test_config_auto_recall.py
git commit   # message: "feat(config): auto_recall / _k / _scope knobs + resolvers" + trailer
```

---

### Task 2: `recall_hook` core module

**Files:**
- Create: `src/cairn/recall_hook.py`
- Test: `tests/test_recall_hook.py`

**Interfaces:**
- Consumes (Task 1): `resolve_auto_recall`, `resolve_auto_recall_k`, `resolve_auto_recall_scope`, `cairn_env`.
- Consumes (existing): `cairn.paths.index_for`, `cairn.paths.resolve_vault`, `cairn.embed.get_embedder`, `cairn.search.open_search`, `cairn.search.resolve_current_project`, `cairn.search.search` (returns `list[Hit]`, each `Hit` has `.permalink`, `.heading_path`, `.snippet`, `.score`).
- Produces (Task 3 consumes):
  - `should_recall(prompt: str, env: Mapping | None = None) -> bool`
  - `format_block(notes: list[dict]) -> str`
  - `build_hook_output(block: str) -> dict`
  - `run(stdin_text: str, *, vault=None, index=None, embedder_name="fastembed", env=None) -> str`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_recall_hook.py`:

```python
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from cairn.embed import get_embedder
from cairn.recall_hook import build_hook_output, format_block, run, should_recall
from tests.search.test_engine import build_index


def _idx(tmp_path) -> Path:
    return Path(build_index(tmp_path, get_embedder("fake")))


def test_should_recall_gate():
    assert should_recall("how do I brew coffee beans?", env={}) is True
    assert should_recall("go", env={}) is False
    assert should_recall("  yes  ", env={}) is False
    assert should_recall("how do I brew coffee?", env={"CAIRN_AUTO_RECALL": "0"}) is False


def test_format_block_empty_returns_empty():
    assert format_block([]) == ""
    assert format_block([{"permalink": "x", "text": "   "}]) == ""


def test_format_block_includes_permalink():
    block = format_block([{"permalink": "coffee", "text": "Arabica beans."}])
    assert block.startswith("## Relevant memories (agentcairn)")
    assert "Arabica beans." in block
    assert "[[coffee]]" in block


def test_build_hook_output_shape():
    assert build_hook_output("hi") == {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "hi",
        }
    }


def test_run_injects_relevant_memory(tmp_path):
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=_idx(tmp_path),
        embedder_name="fake",
        env={},
    )
    assert out
    data = json.loads(out)
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "coffee" in data["hookSpecificOutput"]["additionalContext"].lower()


def test_run_skips_trivial_prompt(tmp_path):
    out = run(json.dumps({"prompt": "go"}), index=_idx(tmp_path), embedder_name="fake", env={})
    assert out == ""


def test_run_disabled_via_env(tmp_path):
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=_idx(tmp_path),
        embedder_name="fake",
        env={"CAIRN_AUTO_RECALL": "0"},
    )
    assert out == ""


def test_run_no_index_is_silent(tmp_path):
    out = run(
        json.dumps({"prompt": "how do I brew coffee beans?"}),
        index=tmp_path / "missing.duckdb",
        embedder_name="fake",
        env={},
    )
    assert out == ""


def test_run_malformed_stdin_is_silent(tmp_path):
    out = run("not json at all", index=_idx(tmp_path), embedder_name="fake", env={})
    assert out == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_recall_hook.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'cairn.recall_hook'`.

- [ ] **Step 3: Implement the module**

Create `src/cairn/recall_hook.py`:

```python
# SPDX-License-Identifier: Apache-2.0
"""UserPromptSubmit auto-recall.

Runs a hybrid recall against the user's prompt and emits it as Claude Code
`additionalContext`. All logic lives here as small, testable units; the plugin
ships only a thin shell wrapper that execs the `cairn recall-hook` CLI command,
which delegates to `run()`. Every path is fail-open: `run()` never raises and
returns "" (inject nothing) on any problem."""
from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from cairn import paths
from cairn.config import (
    cairn_env,
    resolve_auto_recall,
    resolve_auto_recall_k,
    resolve_auto_recall_scope,
)
from cairn.embed import get_embedder
from cairn.search import open_search, resolve_current_project, search

_DEFAULT_MIN_CHARS = 12


def _min_chars(env: Mapping[str, str]) -> int:
    try:
        return int(env.get("CAIRN_AUTO_RECALL_MIN_CHARS") or _DEFAULT_MIN_CHARS)
    except ValueError:
        return _DEFAULT_MIN_CHARS


def should_recall(prompt: str, env: Mapping[str, str] | None = None) -> bool:
    """True iff auto-recall is enabled and the prompt is substantive.
    Skips trivially-short prompts ("yes", "go") — continuations where recall
    adds noise, not signal."""
    if env is None:
        env = cairn_env()
    if not resolve_auto_recall(env):
        return False
    return len(prompt.strip()) >= _min_chars(env)


def format_block(notes: list[dict]) -> str:
    """Render recalled notes into the injection markdown. Returns "" when there
    is nothing to inject (empty list / all-blank texts) so callers skip-inject."""
    items: list[str] = []
    for n in notes:
        text = (n.get("text") or "").strip()
        if not text:
            continue
        permalink = n.get("permalink")
        items.append(f"{text}\n— [[{permalink}]]" if permalink else text)
    if not items:
        return ""
    return "## Relevant memories (agentcairn)\n\n" + "\n\n---\n\n".join(items)


def build_hook_output(block: str) -> dict:
    """Wrap an injection block in the Claude Code UserPromptSubmit envelope."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": block,
        }
    }


def _recall(prompt: str, *, vault, index, embedder_name: str, k: int, scope: str) -> list[dict]:
    """Run one hybrid recall; returns note dicts (possibly empty). Falls back to
    BM25-only if the embedder cannot load. Records the savings ledger
    best-effort (this is the observability that proves recall fired)."""
    idx = paths.index_for(index, paths.resolve_vault(vault))
    if not idx.exists():
        return []
    try:
        emb = None if embedder_name == "none" else get_embedder(embedder_name)
    except Exception:
        emb = None  # BM25-only fallback when the embedder can't load
    current = resolve_current_project(None)
    con = open_search(str(idx))
    try:
        hits = search(con, prompt, embedder=emb, k=k, rerank=False, project=current, scope=scope)
        notes = [
            {"permalink": h.permalink, "title": h.heading_path, "text": h.snippet, "score": h.score}
            for h in hits
        ]
        try:
            from cairn import usage
            from cairn.index.schema import cached_haystack_tokens

            full = cached_haystack_tokens(con)
            recalled = sum(usage.estimate_tokens(n["text"]) for n in notes)
            usage.record("recall", full=full, recalled=recalled, k=k)
        except Exception:
            pass
    finally:
        con.close()
    return notes


def run(
    stdin_text: str,
    *,
    vault: Path | str | None = None,
    index: Path | str | None = None,
    embedder_name: str = "fastembed",
    env: Mapping[str, str] | None = None,
) -> str:
    """Parse a UserPromptSubmit payload (JSON on stdin) and return the string to
    print: a hook-output JSON envelope, or "" to inject nothing. NEVER raises —
    every failure path returns "" (fail-open)."""
    try:
        if env is None:
            env = cairn_env()
        try:
            prompt = (json.loads(stdin_text) or {}).get("prompt") or ""
        except (ValueError, TypeError):
            prompt = ""
        if not should_recall(prompt, env):
            return ""
        notes = _recall(
            prompt,
            vault=vault,
            index=index,
            embedder_name=embedder_name,
            k=resolve_auto_recall_k(env),
            scope=resolve_auto_recall_scope(env),
        )
        block = format_block(notes)
        return json.dumps(build_hook_output(block)) if block else ""
    except Exception:
        return ""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_recall_hook.py -v`
Expected: PASS (9 passed). If `test_run_injects_relevant_memory` finds no hits, confirm `build_index` is imported from `tests.search.test_engine` and the prompt overlaps the fixture's "coffee"/"beans" note.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/recall_hook.py tests/test_recall_hook.py
git commit   # message: "feat(recall): recall_hook core (gate, recall, format, fail-open)" + trailer
```

---

### Task 3: `cairn recall-hook` CLI command

**Files:**
- Modify: `src/cairn/cli.py` (add a command; mirror the `recall` command's option style ~line 280)
- Test: `tests/test_recall_hook_cli.py` (create)

**Interfaces:**
- Consumes (Task 2): `cairn.recall_hook.run`.
- Produces: CLI command `cairn recall-hook [--vault PATH] [--index PATH] [--embedder NAME]` that reads the hook JSON from stdin and prints `run(...)`'s output (or nothing). Always exits 0.

- [ ] **Step 1: Write the failing test**

Create `tests/test_recall_hook_cli.py`:

```python
# SPDX-License-Identifier: Apache-2.0
import json

from typer.testing import CliRunner

from cairn.cli import app
from cairn.embed import get_embedder
from tests.search.test_engine import build_index

runner = CliRunner()


def test_recall_hook_cli_stdin_wiring(tmp_path):
    idx = build_index(tmp_path, get_embedder("fake"))
    r = runner.invoke(
        app,
        ["recall-hook", "--index", str(idx), "--embedder", "fake"],
        input=json.dumps({"prompt": "how do I brew coffee beans?"}),
    )
    assert r.exit_code == 0
    data = json.loads(r.output)
    assert data["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"


def test_recall_hook_cli_trivial_prompt_no_output(tmp_path):
    idx = build_index(tmp_path, get_embedder("fake"))
    r = runner.invoke(
        app,
        ["recall-hook", "--index", str(idx), "--embedder", "fake"],
        input=json.dumps({"prompt": "go"}),
    )
    assert r.exit_code == 0
    assert r.output.strip() == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_recall_hook_cli.py -v`
Expected: FAIL — `recall-hook` is not a known command (Typer exits non-zero / "No such command").

- [ ] **Step 3: Add the command**

In `src/cairn/cli.py`, add (near the `recall` command; `Path`, `typer`, and `app` are already imported there):

```python
@app.command("recall-hook")
def recall_hook(
    vault: Path = typer.Option(
        None, "--vault", help="Vault dir (default: CAIRN_VAULT or ~/agentcairn)."
    ),
    index: Path = typer.Option(
        None, "--index", help="Index .duckdb path (default: derived from vault)."
    ),
    embedder: str = typer.Option(
        "fastembed", "--embedder", help="'fastembed' (default), 'fake' (tests), or 'none' (BM25)."
    ),
) -> None:
    """Auto-recall for the Claude Code UserPromptSubmit hook (internal).

    Reads the hook JSON payload from stdin, runs a hybrid recall against the
    prompt, and prints the additionalContext envelope (or nothing). Always
    exits 0 — never blocks or breaks a prompt.
    """
    import sys

    from cairn import recall_hook as _rh

    out = _rh.run(sys.stdin.read(), vault=vault, index=index, embedder_name=embedder)
    if out:
        typer.echo(out)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_recall_hook_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/cli.py tests/test_recall_hook_cli.py
git commit   # message: "feat(cli): cairn recall-hook command (stdin -> recall_hook.run)" + trailer
```

---

### Task 4: Plugin wiring (hook + wrapper + warm + version)

**Files:**
- Create: `plugin/scripts/user-prompt-submit.sh` (chmod 0755)
- Modify: `plugin/hooks/hooks.json` (add `UserPromptSubmit`)
- Modify: `plugin/scripts/session-start.sh` (add detached warm on the warm path)
- Modify: `plugin/.claude-plugin/plugin.json` (`0.3.1` → `0.4.0`)
- Test: `plugin/tests/test_plugin.py` (add a wiring assertion)

**Interfaces:**
- Consumes (Task 3): the `cairn recall-hook` command.
- The wrapper is **synchronous** — its stdout is the injected context. It is NOT a detached `cairn warm`; warm-keeping lives in `session-start.sh`.

- [ ] **Step 1: Write the failing plugin-wiring test**

In `plugin/tests/test_plugin.py`, add (mirrors the existing `test_precompact_hook_*`; `_json`, `PLUGIN` already defined at module top):

```python
def test_userpromptsubmit_hook_runs_recall():
    hooks = _json(PLUGIN / "hooks" / "hooks.json")["hooks"]
    assert "UserPromptSubmit" in hooks
    assert "user-prompt-submit.sh" in json.dumps(hooks["UserPromptSubmit"])
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py::test_userpromptsubmit_hook_runs_recall -v`
Expected: FAIL — `"UserPromptSubmit"` not in `hooks`.

- [ ] **Step 3: Add the `UserPromptSubmit` entry to `hooks.json`**

Replace the contents of `plugin/hooks/hooks.json` with (adds `UserPromptSubmit` first; mind the trailing comma rules):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/user-prompt-submit.sh"],
          "timeout": 10 } ] }
    ],
    "SessionStart": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-start.sh"],
          "timeout": 20 } ] }
    ],
    "SessionEnd": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-end.sh"],
          "timeout": 120 } ] }
    ],
    "PreCompact": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-end.sh"],
          "timeout": 30 } ] }
    ]
  }
}
```

- [ ] **Step 4: Create the wrapper script**

Create `plugin/scripts/user-prompt-submit.sh`:

```sh
#!/bin/sh
# UserPromptSubmit hook. Runs a hybrid recall against the user's prompt and
# prints it as additionalContext for this turn. SYNCHRONOUS — its stdout IS the
# injected context (do not detach it). Fail-open: `cairn recall-hook` always
# exits 0 and emits nothing on any problem, so it never blocks or breaks a
# prompt. The 10s hook timeout is the safety ceiling; SessionStart pre-warms the
# embedder so the steady-state path is ~1s. stdin = the UserPromptSubmit hook
# JSON (the prompt), inherited by the command.
set -u
VAULT=$(printf '%s' "${CLAUDE_PLUGIN_OPTION_VAULT_PATH:-${1:-$HOME/agentcairn}}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"
$CAIRN recall-hook --vault "$VAULT" 2>/dev/null
exit 0
```

Then make it executable:

```bash
chmod 0755 plugin/scripts/user-prompt-submit.sh
```

- [ ] **Step 5: Add detached warm to `session-start.sh`**

In `plugin/scripts/session-start.sh`, immediately AFTER the first-run `if … fi` block (the block that runs `( $CAIRN init "$VAULT"; $CAIRN warm ) … &` then `exit 0`) and BEFORE the `# Fetch recent memories as JSON` comment, insert:

```sh
# Keep the embedder/reranker models loaded on every (warm-path) session so the
# per-prompt UserPromptSubmit recall stays fast. `cairn warm` is idempotent and
# near-instant once cached; fully detached anyway so a cold re-download can never
# delay the session, and stdin/stdout/stderr detached so it can't hold the hook's
# pipes open. Best-effort: failures are swallowed.
( $CAIRN warm ) </dev/null >/dev/null 2>&1 &
```

- [ ] **Step 6: Bump the plugin version**

In `plugin/.claude-plugin/plugin.json`, change `"version": "0.3.1"` to `"version": "0.4.0"`.

- [ ] **Step 7: Run the plugin tests (full file — outside default testpaths)**

Run: `uv run pytest plugin/tests/test_plugin.py -v`
Expected: PASS — including the new `test_userpromptsubmit_hook_runs_recall` AND the existing `test_hooks_do_not_hardfail_on_unset_vault_path` (the new entry uses `${CLAUDE_PLUGIN_ROOT}`, no `user_config`).

- [ ] **Step 8: Commit**

```bash
git add plugin/hooks/hooks.json plugin/scripts/user-prompt-submit.sh \
        plugin/scripts/session-start.sh plugin/.claude-plugin/plugin.json \
        plugin/tests/test_plugin.py
git commit   # message: "feat(plugin): UserPromptSubmit auto-recall hook + warm-path warm (v0.4.0)" + trailer
```

---

### Task 5: Docs + CHANGELOG

**Files:**
- Modify: `README.md` (document auto-recall + the three config keys)
- Modify: `CHANGELOG.md` (Unreleased entry)

**Interfaces:**
- Consumes: nothing (documentation of Tasks 1-4).

- [ ] **Step 1: Document auto-recall in the README**

In `README.md`, find the section describing the Claude Code plugin's ambient behavior (recall-at-start + capture-at-end). Add a short paragraph and a config block. Use this copy:

```markdown
**Automatic recall (Claude Code).** On every substantive prompt, the plugin runs
a hybrid recall against what you just asked and injects the most relevant
memories as context for that turn — not just the recency digest shown at session
start. Trivially-short prompts (e.g. "yes", "go") are skipped. It is fail-open:
if anything goes wrong it injects nothing and never blocks your prompt.

Configure it in `~/.agentcairn/config.toml` (flat top-level keys; env vars
`CAIRN_AUTO_RECALL{,_K,_SCOPE}` override the file):

​```toml
auto_recall       = true    # master on/off (default: true)
auto_recall_k     = 3       # memories injected per prompt
auto_recall_scope = "all"   # "all" (boost, non-lossy) or "project" (hard filter)
​```
```

(Remove the zero-width `​` characters around the inner fence — they are only here to nest the code block in this plan. The README gets a normal ```` ```toml ```` block.)

- [ ] **Step 2: Add a CHANGELOG entry**

In `CHANGELOG.md`, under the `## [Unreleased]` heading (create it above the latest version if absent), add:

```markdown
### Added
- **Automatic per-turn recall on Claude Code.** A `UserPromptSubmit` hook runs a
  hybrid recall against each substantive prompt and injects the hits as context,
  closing the gap where the `recall` tool was almost never invoked. New
  `cairn recall-hook` command; configurable via `auto_recall` / `auto_recall_k` /
  `auto_recall_scope` (default on). Plugin `0.4.0`.
```

- [ ] **Step 3: Verify the suite is still green**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add README.md CHANGELOG.md
git commit   # message: "docs: document automatic per-turn recall + config" + trailer
```

---

## Self-Review

**Spec coverage:**
- Behavior (substantive-prompt recall + inject) → Tasks 2, 3, 4. ✓
- Keep SessionStart digest → unchanged (Task 4 only *adds*). ✓
- Default-on, configurable (`auto`/`k`/`scope`, flat keys) → Task 1 + README (Task 5). ✓
- Trivial-prompt gate → `should_recall` (Task 2). ✓
- Architecture: thin command + shell wrapper → Tasks 2-4. ✓
- Latency: SessionStart pre-warm + BM25 fallback + 10s ceiling → Task 4 warm line, `_recall` except→`None`, `hooks.json` timeout. ✓
- Fail-open (always exit 0) → `run()` try/except, wrapper `exit 0`, command no-raise. ✓
- Injection format (`## Relevant memories (agentcairn)` + permalink) → `format_block` (Task 2). ✓
- usage.jsonl moves again → `usage.record` in `_recall` (Task 2). ✓
- Testing (unit + CliRunner stdin + plugin wiring) → Tasks 1-4. ✓
- Codex fast-follow → out of scope (noted). ✓

**Placeholder scan:** none — every code step shows complete code. The README inner-fence note explains the zero-width placeholder characters.

**Type consistency:** `run()` signature is identical in Task 2 (definition) and Task 3 (call). `Hit` attributes used (`.permalink`, `.heading_path`, `.snippet`, `.score`) match the extracted dataclass. `format_block` consumes the dict keys (`permalink`, `text`) that `_recall` produces. Resolver names match between Task 1 (defs) and Task 2 (imports).
