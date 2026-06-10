# agentcairn Claude Code Plugin — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a Claude Code plugin (from the agentcairn repo) that auto-wires the agentcairn MCP server, surfaces recent memory at SessionStart, distills sessions at SessionEnd, and adds a memory skill + slash commands — backed by two new CLI commands (`cairn recent`, `cairn init`) released as agentcairn 0.2.0.

**Architecture:** Phase 1 extends the Python package (`cairn recent`, `cairn init`) and releases 0.2.0 via the existing Trusted-Publishing workflow. Phase 2 adds a `plugin/` tree + `.claude-plugin/marketplace.json` that vendors no Python — it wires `uvx agentcairn` (MCP) and `uvx --from 'agentcairn>=0.2' cairn …` (hooks). Spec: `docs/specs/2026-06-10-agentcairn-claude-code-plugin-design.md`.

**Tech Stack:** Python 3.12 + Typer (CLI), DuckDB (notes query), uv (build/release), Claude Code plugin format (JSON manifests + POSIX sh hook scripts), pytest.

---

## File structure

```
src/cairn/cli.py                      # MODIFY: add `recent` + `init` commands
src/cairn/__init__.py                 # MODIFY: __version__ → 0.2.0
tests/test_cli.py                     # MODIFY: tests for recent + init (+ version bump)
.claude-plugin/marketplace.json       # CREATE: lists the plugin
plugin/.claude-plugin/plugin.json     # CREATE: manifest + userConfig
plugin/.mcp.json                      # CREATE: uvx agentcairn MCP server
plugin/hooks/hooks.json               # CREATE: SessionStart + SessionEnd
plugin/scripts/session-start.sh       # CREATE: ensure-vault + recent digest → additionalContext
plugin/scripts/session-end.sh         # CREATE: cairn sweep (distill ending session)
plugin/skills/using-agentcairn-memory/SKILL.md   # CREATE: recall/remember guidance
plugin/commands/recall.md             # CREATE: /agentcairn:recall
plugin/commands/remember.md           # CREATE: /agentcairn:remember
plugin/commands/memory.md             # CREATE: /agentcairn:memory
plugin/commands/ingest.md             # CREATE: /agentcairn:ingest
plugin/README.md                      # CREATE: install + usage
plugin/tests/test_plugin.py           # CREATE: manifest/frontmatter validation + hook-script smoke
.github/workflows/plugin.yml          # CREATE: validate plugin on plugin/** changes
```

Run all Python commands from the repo root with `uv run`. Commit after each task.

---

# Phase 1 — agentcairn 0.2.0 (`cairn recent` + `cairn init`)

## Task 1: `cairn recent` CLI command

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_cli.py`:

```python
def test_recent_returns_recent_notes_json(tmp_path):
    import json

    v = tmp_path / "vault"
    v.mkdir()
    (v / "a.md").write_text("---\ntitle: Alpha\npermalink: a\n---\nalpha body\n")
    (v / "b.md").write_text("---\ntitle: Beta\npermalink: b\n---\nbeta body\n")
    idx = tmp_path / "i.duckdb"
    r = runner.invoke(app, ["reindex", str(v), "--index", str(idx), "--embedder", "fake"])
    assert r.exit_code == 0, r.output
    s = runner.invoke(app, ["recent", "--index", str(idx), "-n", "5", "--json"])
    assert s.exit_code == 0, s.output
    data = json.loads(s.stdout)
    perms = {note["permalink"] for note in data["notes"]}
    assert {"a", "b"} <= perms


def test_recent_missing_index_json_is_empty(tmp_path):
    import json

    s = runner.invoke(app, ["recent", "--index", str(tmp_path / "nope.duckdb"), "--json"])
    assert s.exit_code == 0
    assert json.loads(s.stdout) == {"notes": []}
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_recent_returns_recent_notes_json -v`
Expected: FAIL — `No such command 'recent'` (Typer exits non-zero).

- [ ] **Step 3: Implement `recent`** — in `src/cairn/cli.py`, add `import json` and `import os` to the imports if not already present, then add this command (place it after the `recall` command):

```python
@app.command()
def recent(
    index: Path = typer.Option(None, "--index", help="Index .duckdb path."),
    project: str = typer.Option(
        None, "--project", help="Only notes whose path contains this substring."
    ),
    n: int = typer.Option(10, "-n", "--num", help="Number of notes."),
    as_json: bool = typer.Option(False, "--json", help="Emit JSON for machine parsing."),
) -> None:
    """Most-recently-modified notes (optionally filtered to a project path substring)."""
    idx = index or _default_index()
    if not idx.exists():
        typer.echo(json.dumps({"notes": []}) if as_json else f"no index at {idx}")
        return
    con = open_search(str(idx))
    try:
        if project:
            rows = con.execute(
                "SELECT permalink, title, path FROM notes "
                "WHERE path LIKE '%' || ? || '%' ORDER BY mtime DESC LIMIT ?",
                [project, n],
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT permalink, title, path FROM notes ORDER BY mtime DESC LIMIT ?", [n]
            ).fetchall()
    finally:
        con.close()
    notes = [{"permalink": r[0], "title": r[1], "path": r[2]} for r in rows]
    if as_json:
        typer.echo(json.dumps({"notes": notes}))
    else:
        for nt in notes:
            typer.echo(f"{nt['permalink']}  ·  {nt['title']}")
```

(`_default_index` and `open_search` are already imported/defined in `cli.py` — they're used by `recall`. Confirm `import json`/`import os` are at the top; add whichever is missing.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k recent -v`
Expected: both `recent` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'cairn recent' (recent notes, --project/--json) for the plugin SessionStart digest"
```

## Task 2: `cairn init` CLI command (Obsidian-ready vault)

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests** — append to `tests/test_cli.py`:

```python
def test_init_creates_obsidian_ready_vault(tmp_path):
    target = tmp_path / "myvault"
    r = runner.invoke(app, ["init", str(target)])
    assert r.exit_code == 0, r.output
    assert (target / ".obsidian" / "app.json").exists()
    welcome = (target / "welcome.md").read_text()
    assert "permalink: welcome" in welcome
    assert str(target) in r.output


def test_init_idempotent_preserves_edits(tmp_path):
    target = tmp_path / "myvault"
    runner.invoke(app, ["init", str(target)])
    (target / "welcome.md").write_text("---\ntitle: Mine\npermalink: welcome\n---\nedited\n")
    (target / "note.md").write_text("---\ntitle: N\npermalink: n\n---\nkeep me\n")
    r2 = runner.invoke(app, ["init", str(target)])  # second run
    assert r2.exit_code == 0
    assert "edited" in (target / "welcome.md").read_text()  # not clobbered
    assert (target / "note.md").exists()  # existing notes untouched
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_cli.py::test_init_creates_obsidian_ready_vault -v`
Expected: FAIL — `No such command 'init'`.

- [ ] **Step 3: Implement `init`** — add to `src/cairn/cli.py` (after `recent`):

```python
_WELCOME = (
    "---\ntitle: Welcome to your agentcairn vault\npermalink: welcome\n---\n\n"
    "This is your **agentcairn** memory vault. Your coding agent writes distilled, redacted "
    "memories here as plain Markdown — you can read, edit, or delete any of it by hand. "
    "Open this folder in Obsidian to browse the graph.\n"
)


@app.command()
def init(
    path: Path = typer.Argument(
        None, help="Vault path (default: $CAIRN_VAULT or ~/agentcairn)."
    ),
) -> None:
    """Scaffold an Obsidian-ready agentcairn vault. Idempotent and non-destructive."""
    target = path or Path(os.environ.get("CAIRN_VAULT") or (Path.home() / "agentcairn"))
    target = target.expanduser()
    target.mkdir(parents=True, exist_ok=True)
    obs = target / ".obsidian"
    obs.mkdir(exist_ok=True)
    app_json = obs / "app.json"
    if not app_json.exists():
        app_json.write_text("{}\n")
    welcome = target / "welcome.md"
    existed = welcome.exists()
    if not existed:
        welcome.write_text(_WELCOME)
    suffix = "" if not existed else " (existing — left intact)"
    typer.echo(f"agentcairn vault ready at {target}{suffix}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k init -v`
Expected: both `init` tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): add 'cairn init' — scaffold an Obsidian-ready vault (idempotent)"
```

## Task 3: Bump to 0.2.0 + full suite

**Files:**
- Modify: `src/cairn/__init__.py`, `tests/test_cli.py`

- [ ] **Step 1: Update the version-flag test** — in `tests/test_cli.py`, change the assertion in `test_version_flag_prints_version`:

```python
    assert "0.2.0" in result.stdout
```

- [ ] **Step 2: Bump the version (single source)** — in `src/cairn/__init__.py`:

```python
__version__ = "0.2.0"
```

- [ ] **Step 3: Run the full suite + build**

Run: `uv run pytest -q`
Expected: all pass (incl. the new `recent`/`init` tests and the 0.2.0 version test).
Run: `uv build`
Expected: `Successfully built dist/agentcairn-0.2.0-py3-none-any.whl` (dynamic version picks up 0.2.0).

- [ ] **Step 4: Commit**

```bash
git add src/cairn/__init__.py tests/test_cli.py
git commit -m "release: agentcairn 0.2.0 (cairn recent + cairn init)"
```

- [ ] **Step 5: Release (after the PR merges to main)**

> Do this once the Phase-1 PR is merged to `main` (so the tag points at merged code):
```bash
git tag v0.2.0 && git push origin v0.2.0
```
Then verify (the Trusted-Publishing `release.yml` runs): `curl -s https://pypi.org/pypi/agentcairn/json | grep -o '"version":"0.2.0"'` and `uvx --refresh --from agentcairn cairn --version` → `0.2.0`.

---

# Phase 2 — the Claude Code plugin

> Phase 2 depends on agentcairn 0.2.0 being on PyPI (Task 3 release), since hooks call `uvx --from 'agentcairn>=0.2' cairn …`.

## Task 4: Plugin scaffold (marketplace + manifest + MCP wiring)

**Files:**
- Create: `.claude-plugin/marketplace.json`, `plugin/.claude-plugin/plugin.json`, `plugin/.mcp.json`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing validation test** — `plugin/tests/test_plugin.py`:

```python
# SPDX-License-Identifier: Apache-2.0
"""Validate the Claude Code plugin's static assets (no network)."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # repo root
PLUGIN = ROOT / "plugin"


def _json(p):
    return json.loads(Path(p).read_text())


def test_marketplace_lists_the_plugin():
    mkt = _json(ROOT / ".claude-plugin" / "marketplace.json")
    names = {p["name"] for p in mkt["plugins"]}
    assert "agentcairn" in names


def test_plugin_manifest_valid():
    man = _json(PLUGIN / ".claude-plugin" / "plugin.json")
    assert man["name"] == "agentcairn"
    assert "vault_path" in man["userConfig"]


def test_mcp_config_wires_uvx_agentcairn():
    mcp = _json(PLUGIN / ".mcp.json")
    srv = mcp["mcpServers"]["agentcairn"]
    assert srv["command"] == "uvx"
    assert srv["args"] == ["agentcairn"]
    assert srv["env"]["CAIRN_VAULT"] == "${user_config.vault_path}"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py -v`
Expected: FAIL — files don't exist yet (FileNotFoundError).

- [ ] **Step 3: Create the three files**

`.claude-plugin/marketplace.json`:
```json
{
  "name": "agentcairn",
  "plugins": [
    {
      "name": "agentcairn",
      "source": "./plugin",
      "description": "Local-first agent memory for Claude Code — recall, remember, ambient capture."
    }
  ]
}
```

`plugin/.claude-plugin/plugin.json`:
```json
{
  "name": "agentcairn",
  "displayName": "agentcairn",
  "description": "Local-first agent memory for Claude Code — recall, remember, and ambient capture into a Markdown vault you own.",
  "version": "0.1.0",
  "author": { "name": "Charles C. Figueiredo", "email": "ccf@ccf.io" },
  "homepage": "https://agentcairn.dev",
  "repository": "https://github.com/ccf/agentcairn",
  "license": "Apache-2.0",
  "keywords": ["memory", "mcp", "obsidian", "agent", "local-first"],
  "userConfig": {
    "vault_path": {
      "type": "directory",
      "title": "agentcairn vault",
      "description": "Folder for your Markdown memory vault (auto-created on first session).",
      "default": "~/agentcairn"
    },
    "index_path": {
      "type": "string",
      "title": "Index path",
      "description": "DuckDB index location (rebuildable cache).",
      "default": "~/.cache/agentcairn/index.duckdb"
    }
  },
  "mcpServers": "./.mcp.json",
  "hooks": "./hooks/hooks.json"
}
```

`plugin/.mcp.json`:
```json
{
  "mcpServers": {
    "agentcairn": {
      "command": "uvx",
      "args": ["agentcairn"],
      "env": {
        "CAIRN_VAULT": "${user_config.vault_path}",
        "CAIRN_INDEX": "${user_config.index_path}"
      }
    }
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest plugin/tests/test_plugin.py -v`
Expected: the three tests PASS.

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin/marketplace.json plugin/.claude-plugin/plugin.json plugin/.mcp.json plugin/tests/test_plugin.py
git commit -m "feat(plugin): scaffold — marketplace, manifest, MCP wiring (uvx agentcairn)"
```

## Task 5: Lifecycle hook scripts

**Files:**
- Create: `plugin/hooks/hooks.json`, `plugin/scripts/session-start.sh`, `plugin/scripts/session-end.sh`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing smoke test** — append to `plugin/tests/test_plugin.py`:

```python
import json as _json
import os
import subprocess


def _run_hook(script, stdin_obj, env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.run(
        ["sh", str(PLUGIN / "scripts" / script), env["VAULT_ARG"], env["INDEX_ARG"]],
        input=_json.dumps(stdin_obj),
        capture_output=True,
        text=True,
        env=env,
    )


def test_session_start_emits_valid_json_with_memories(tmp_path, monkeypatch):
    # Stub `uvx` so `uvx --from ... cairn recent --json` returns canned notes — no network.
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    stub.write_text(
        '#!/bin/sh\n'
        '# echo canned recent JSON regardless of args\n'
        'echo \'{"notes":[{"permalink":"a","title":"Fixed login","path":"a.md"}]}\'\n'
    )
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()  # exists → init path is skipped
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {"PATH": f"{bindir}:{os.environ['PATH']}", "VAULT_ARG": str(vault), "INDEX_ARG": str(tmp_path / "i.duckdb")},
    )
    assert r.returncode == 0, r.stderr
    out = _json.loads(r.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert "Fixed login" in out["hookSpecificOutput"]["additionalContext"]


def test_session_start_empty_emits_nothing(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    stub.write_text('#!/bin/sh\necho \'{"notes":[]}\'\n')
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    r = _run_hook(
        "session-start.sh",
        {"hook_event_name": "SessionStart", "cwd": "/Users/x/proj"},
        {"PATH": f"{bindir}:{os.environ['PATH']}", "VAULT_ARG": str(vault), "INDEX_ARG": str(tmp_path / "i.duckdb")},
    )
    assert r.returncode == 0
    assert r.stdout.strip() == ""  # no context when no memories


def test_session_end_runs_and_exits_zero(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "uvx"
    stub.write_text('#!/bin/sh\necho swept; exit 0\n')
    stub.chmod(0o755)
    vault = tmp_path / "agentcairn"
    vault.mkdir()
    r = _run_hook(
        "session-end.sh",
        {"hook_event_name": "SessionEnd", "cwd": "/Users/x/proj"},
        {"PATH": f"{bindir}:{os.environ['PATH']}", "VAULT_ARG": str(vault), "INDEX_ARG": str(tmp_path / "i.duckdb")},
    )
    assert r.returncode == 0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py -k session -v`
Expected: FAIL — scripts don't exist.

- [ ] **Step 3: Create the hook config + scripts**

`plugin/hooks/hooks.json`:
```json
{
  "hooks": {
    "SessionStart": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-start.sh", "${user_config.vault_path}", "${user_config.index_path}"],
          "timeout": 20 } ] }
    ],
    "SessionEnd": [
      { "matcher": "*", "hooks": [
        { "type": "command", "command": "sh",
          "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/session-end.sh", "${user_config.vault_path}", "${user_config.index_path}"],
          "timeout": 120 } ] }
    ]
  }
}
```

`plugin/scripts/session-start.sh`:
```sh
#!/bin/sh
# args: $1 = vault path, $2 = index path. stdin = hook JSON (has "cwd").
# Emits SessionStart additionalContext with a compact recent-memory digest.
# Always exits 0 (never blocks/delays the session); no output when there's nothing.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
INDEX=$(printf '%s' "${2:-$HOME/.cache/agentcairn/index.duckdb}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"

# Read cwd from stdin hook JSON (best-effort; default to repo dir name unknown).
INPUT=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$INPUT" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
PROJECT=$(basename "${CWD:-}" 2>/dev/null || echo "")

# Zero-step onboarding: create the vault if missing.
[ -d "$VAULT" ] || $CAIRN init "$VAULT" >/dev/null 2>&1 || true

# Fetch recent project-scoped memories as JSON (best-effort).
JSON=$($CAIRN recent --index "$INDEX" ${PROJECT:+--project "$PROJECT"} -n 5 --json 2>/dev/null || echo '{"notes":[]}')

# Format a compact digest; emit nothing if no notes.
LINES=$(printf '%s' "$JSON" | python3 -c '
import json,sys
try:
    notes=json.load(sys.stdin).get("notes",[])
except Exception:
    notes=[]
for n in notes:
    print(f"- {n.get(\"title\") or n.get(\"permalink\")}")
' 2>/dev/null || true)

[ -z "$LINES" ] && exit 0

CTX="## agentcairn — recent memory${PROJECT:+ for $PROJECT}
$LINES

(Use the \`recall\` tool to pull full notes.)"
python3 -c '
import json,sys
print(json.dumps({"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":sys.argv[1]}}))
' "$CTX" 2>/dev/null || true
exit 0
```

`plugin/scripts/session-end.sh`:
```sh
#!/bin/sh
# args: $1 = vault path, $2 = index path. stdin = hook JSON (has "cwd").
# Distills the just-ended session into the vault (incremental; dedup-ledger gated).
# Always exits 0; never blocks teardown beyond the hook timeout.
set -u
VAULT=$(printf '%s' "${1:-$HOME/agentcairn}" | sed "s#^~#$HOME#")
INDEX=$(printf '%s' "${2:-$HOME/.cache/agentcairn/index.duckdb}" | sed "s#^~#$HOME#")
CAIRN="uvx --from agentcairn>=0.2 cairn"
INPUT=$(cat 2>/dev/null || true)
CWD=$(printf '%s' "$INPUT" | sed -n 's/.*"cwd"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')

[ -d "$VAULT" ] || $CAIRN init "$VAULT" >/dev/null 2>&1 || true
$CAIRN sweep --vault "$VAULT" --index "$INDEX" ${CWD:+--project "$CWD"} >/dev/null 2>&1 || true
exit 0
```

- [ ] **Step 4: Make scripts executable, run tests + shellcheck**

```bash
chmod +x plugin/scripts/session-start.sh plugin/scripts/session-end.sh
```
Run: `uv run pytest plugin/tests/test_plugin.py -k session -v`
Expected: the three session tests PASS.
Run (if shellcheck is available): `shellcheck plugin/scripts/*.sh`
Expected: no errors (warnings about `$CAIRN` word-splitting are intentional — the command is multi-word; leave it).

- [ ] **Step 5: Commit**

```bash
git add plugin/hooks/hooks.json plugin/scripts/session-start.sh plugin/scripts/session-end.sh plugin/tests/test_plugin.py
git commit -m "feat(plugin): SessionStart recent-digest (+auto-init) and SessionEnd distill hooks"
```

## Task 6: Memory skill

**Files:**
- Create: `plugin/skills/using-agentcairn-memory/SKILL.md`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing test** — append to `plugin/tests/test_plugin.py`:

```python
def test_skill_has_valid_frontmatter():
    text = (PLUGIN / "skills" / "using-agentcairn-memory" / "SKILL.md").read_text()
    assert text.startswith("---")
    head = text.split("---", 2)[1]
    assert "name:" in head and "description:" in head
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py::test_skill_has_valid_frontmatter -v`
Expected: FAIL — file missing.

- [ ] **Step 3: Create the skill** — `plugin/skills/using-agentcairn-memory/SKILL.md`:

```markdown
---
name: using-agentcairn-memory
description: Use when starting a non-trivial task or finishing a decision/fix — recall prior memory before working, and remember durable facts worth carrying across sessions.
---

# Using agentcairn memory

You have a persistent memory backed by agentcairn (a Markdown vault the user owns). Use it.

## Recall before you work
Before designing, debugging, or re-deriving something non-trivial, **search memory first**:
- Use the `recall` tool (hybrid search) with a focused query — "how did we fix the auth token refresh?", "what did we decide about the migration order?".
- Expand a promising hit with `build_context` to read the full note.
- Recall is cross-project: prior solutions in *any* repo can help. Cite notes by permalink.

## Remember durable facts
After a decision, a non-obvious fix, a gotcha, or a stated user preference, **persist it** with the
`remember` tool — a short, self-contained fact. Good memories: "We rotate jwt-secret on deploy via
X.", "User prefers rebase-merges.", "DuckDB TIMESTAMP stores naive-UTC — bind accordingly."
Skip the trivial — the SessionEnd hook already captures the session in bulk; `remember` is for the
high-value things worth pinning deliberately.

The vault is plain Markdown the user can read and edit; treat it as shared, durable knowledge.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest plugin/tests/test_plugin.py::test_skill_has_valid_frontmatter -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugin/skills/using-agentcairn-memory/SKILL.md plugin/tests/test_plugin.py
git commit -m "feat(plugin): using-agentcairn-memory skill (recall-before-work, remember durable facts)"
```

## Task 7: Slash commands

**Files:**
- Create: `plugin/commands/recall.md`, `plugin/commands/remember.md`, `plugin/commands/memory.md`, `plugin/commands/ingest.md`
- Test: `plugin/tests/test_plugin.py`

- [ ] **Step 1: Write the failing test** — append to `plugin/tests/test_plugin.py`:

```python
import pytest


@pytest.mark.parametrize("cmd", ["recall", "remember", "memory", "ingest"])
def test_command_has_frontmatter(cmd):
    text = (PLUGIN / "commands" / f"{cmd}.md").read_text()
    assert text.startswith("---")
    assert "description:" in text.split("---", 2)[1]
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest plugin/tests/test_plugin.py -k command_has_frontmatter -v`
Expected: FAIL — files missing.

- [ ] **Step 3: Create the four command files**

`plugin/commands/recall.md`:
```markdown
---
description: Search agentcairn memory and show cited results.
---
Use the `recall` tool to search memory for: $ARGUMENTS

Present the top results compactly, each with its permalink. If one looks directly relevant, expand it with `build_context` before answering.
```

`plugin/commands/remember.md`:
```markdown
---
description: Persist a durable memory into the vault.
---
Use the `remember` tool to store this durable fact: $ARGUMENTS

Keep it short and self-contained. Confirm what was saved and where.
```

`plugin/commands/memory.md`:
```markdown
---
description: Show agentcairn memory health (vault + index status).
---
Run `uvx --from agentcairn cairn doctor` and `uvx --from agentcairn cairn index-status` and summarize the vault location, note/chunk counts, and any health warnings.
```

`plugin/commands/ingest.md`:
```markdown
---
description: Distill recent sessions into the vault now.
---
Run `uvx --from agentcairn cairn sweep --vault "$CAIRN_VAULT"` to ingest and reindex recent sessions, then report how many memories were written.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest plugin/tests/test_plugin.py -k command_has_frontmatter -v`
Expected: 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add plugin/commands/
git commit -m "feat(plugin): slash commands /recall /remember /memory /ingest"
```

## Task 8: Plugin README + CI validation job

**Files:**
- Create: `plugin/README.md`, `.github/workflows/plugin.yml`

- [ ] **Step 1: Write `plugin/README.md`**

```markdown
# agentcairn — Claude Code plugin

Local-first agent memory inside Claude Code: auto-wires the agentcairn MCP server, surfaces recent
memory at the start of each session, and distills each session into a Markdown vault you own.

## Install
```bash
claude plugin marketplace add ccf/agentcairn
claude plugin install agentcairn@agentcairn
```
On install you'll be asked for a **vault path** (default `~/agentcairn`). The vault is **auto-created**
(Obsidian-ready) on the first session — no Obsidian setup needed.

## What you get
- **MCP tools:** `recall`, `search`, `build_context`, `recent`, `remember`.
- **Ambient memory:** SessionStart surfaces recent memories; SessionEnd distills the session.
- **Skill:** `using-agentcairn-memory` (recall-before-work, remember durable facts).
- **Commands:** `/agentcairn:recall`, `/agentcairn:remember`, `/agentcairn:memory`, `/agentcairn:ingest`.

The plugin runs the published `agentcairn` PyPI package via `uvx` — nothing to pip-install.
You can also scaffold a vault yourself: `uvx --from agentcairn cairn init ~/agentcairn`.
```

- [ ] **Step 2: Create `.github/workflows/plugin.yml`**

```yaml
name: plugin
on:
  push:
    branches: [main]
    paths: ["plugin/**", ".claude-plugin/**", ".github/workflows/plugin.yml"]
  pull_request:
    paths: ["plugin/**", ".claude-plugin/**", ".github/workflows/plugin.yml"]
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: "3.12"
      - run: uv run --no-project pytest plugin/tests/ -q
      - name: shellcheck hook scripts
        run: |
          sudo apt-get update && sudo apt-get install -y shellcheck
          shellcheck plugin/scripts/*.sh || true
```

- [ ] **Step 3: Run the plugin test suite locally**

Run: `uv run pytest plugin/tests/ -q`
Expected: all plugin tests PASS (manifest, MCP, hooks smoke, skill, commands).

- [ ] **Step 4: Commit**

```bash
git add plugin/README.md .github/workflows/plugin.yml
git commit -m "docs+ci(plugin): README + plugin validation workflow"
```

---

## Self-review (against the spec)

- **§3 distribution (repo = tool + marketplace, no vendored Python, uvx):** Task 4 (marketplace.json + plugin in `plugin/`), `.mcp.json` uses `uvx agentcairn`; hooks use `uvx --from 'agentcairn>=0.2' cairn`. ✓
- **§4 prerequisites — `cairn recent` + `cairn init`, release 0.2.0:** Tasks 1, 2, 3. ✓
- **§6/§7 manifest + MCP wiring (CAIRN_VAULT/CAIRN_INDEX from user_config):** Task 4 files + test. ✓
- **§8 `~` expansion:** hook scripts `sed "s#^~#$HOME#"` the paths (guarantees absolute). ✓
- **§9 hooks (SessionStart digest + auto-init; SessionEnd distill):** Task 5 (`hooks.json` + both scripts + smoke). ✓
- **§10 skill:** Task 6. **§11 commands:** Task 7. ✓
- **§12 testing (tool-side cli tests; manifest/frontmatter validation; offline hook smoke w/ stubbed cairn; CI job):** Tasks 1–2 (cli), 4–7 (validation), 5 (hook smoke), 8 (CI). ✓
- **§13 out of scope:** no UserPromptSubmit, agents, telemetry, vendored Python — none added. ✓
- **Version consistency:** plugin manifest `0.1.0`; package `0.2.0` (Task 3) — matches §14. The `recent` JSON shape (`{"notes":[{permalink,title,path}]}`) is consistent between Task 1's impl, its test, and the hook script's parser in Task 5.

No placeholders; every step has concrete code/commands.
```
