# Complete Cursor Integration (MCP + Skill) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cairn install cursor` installs the `using-agentcairn-memory` SKILL.md into `~/.cursor/skills/` alongside the existing MCP write, so Cursor is first-class on the output side (recall/remember guidance + MCP tools).

**Architecture:** Ship the skill body as package data under `src/cairn/assets/` (so a pip-installed `cairn` has it without the repo `plugin/` dir), kept byte-identical to `plugin/skills/using-agentcairn-memory/SKILL.md` by a test. Add a `Host.skill_dir` field (cursor → `~/.cursor/skills`) and a `hosts/skills.py` installer; the `cairn install` mcp-host branch installs the skill when `h.skill_dir` is set. No change to the MCP server, ingest, consolidation, or the Markdown contract.

**Tech Stack:** Python 3.11+, hatchling (`packages = ["src/cairn"]`), `importlib.resources`, Typer CLI, pytest, uv (`uv run pytest` / `uv run ruff`).

**Reference:** Spec `docs/specs/2026-06-15-cursor-skill-design.md`. Branch `feat/cursor-skill` is checked out with the spec committed.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/cairn/assets/using-agentcairn-memory/SKILL.md` | **new** — bundled (wheel-shipped) copy of the memory skill the CLI installs into Cursor |
| `src/cairn/hosts/__init__.py` | add `Host.skill_dir` field; set `skill_dir="~/.cursor/skills"` on the cursor entry |
| `src/cairn/hosts/skills.py` | **new** — `cursor_skill_text()` (read package data) + `install_skill()` (write `<root>/using-agentcairn-memory/SKILL.md`) |
| `src/cairn/cli.py` | `install` mcp-host branch installs the skill when `h.skill_dir` is set |
| `tests/test_plugin_assets.py` | asset-sync test (package copy byte-identical to `plugin/` copy) |
| `tests/test_hosts.py` | `skill_dir` registry values; `install_skill` write + dry behavior |
| `tests/test_cli.py` | `cairn install cursor` writes the skill; `--print` shows a note and writes nothing |
| `README.md`, `CLAUDE.md`, `website/src/lib/content.ts` | Cursor gets the memory skill (MCP + skill), still an MCP host |

---

## Task 1: Bundle the skill as package data + sync test

**Files:**
- Create: `src/cairn/assets/using-agentcairn-memory/SKILL.md`
- Test: `tests/test_plugin_assets.py` (add one test)

The repo `plugin/skills/using-agentcairn-memory/SKILL.md` is the canonical content (it is what the Claude/Codex/Antigravity plugins bundle). A pip-installed `cairn` does NOT ship `plugin/`, so the CLI needs its own copy inside the package. We enforce they never drift with a byte-identical test.

- [ ] **Step 1: Write the failing sync test**

Add to `tests/test_plugin_assets.py` (note the existing `ROOT`/`PLUGIN` constants at the top of that file):

```python
def test_bundled_cursor_skill_matches_plugin_copy():
    # The CLI installs this package-data copy into ~/.cursor/skills; it must stay
    # byte-identical to the canonical plugin/ copy so the two never drift.
    pkg_copy = ROOT / "src" / "cairn" / "assets" / "using-agentcairn-memory" / "SKILL.md"
    plugin_copy = PLUGIN / "skills" / "using-agentcairn-memory" / "SKILL.md"
    assert pkg_copy.read_bytes() == plugin_copy.read_bytes()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_plugin_assets.py::test_bundled_cursor_skill_matches_plugin_copy -v`
Expected: FAIL — `FileNotFoundError` (the `src/cairn/assets/...` file does not exist yet).

- [ ] **Step 3: Create the bundled asset as an exact copy**

Create the directory and copy the file verbatim (do NOT hand-retype — copy the bytes):

```bash
mkdir -p src/cairn/assets/using-agentcairn-memory
cp plugin/skills/using-agentcairn-memory/SKILL.md src/cairn/assets/using-agentcairn-memory/SKILL.md
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_plugin_assets.py::test_bundled_cursor_skill_matches_plugin_copy -v`
Expected: PASS.

- [ ] **Step 5: Verify the asset ships in the wheel**

hatchling's `packages = ["src/cairn"]` includes non-Python files under the package. Confirm the built wheel actually contains the `.md`:

```bash
uv build
python -c "import zipfile, glob; w=sorted(glob.glob('dist/agentcairn-*.whl'))[-1]; names=zipfile.ZipFile(w).namelist(); assert 'cairn/assets/using-agentcairn-memory/SKILL.md' in names, names; print('OK', w)"
```

Expected: prints `OK dist/agentcairn-*.whl`. If the assert fails, add an explicit `[tool.hatch.build.targets.wheel.force-include]` for the asset in `pyproject.toml` and rebuild. Then clean up build artifacts so they aren't committed:

```bash
rm -rf dist
```

- [ ] **Step 6: Commit**

```bash
git add src/cairn/assets/using-agentcairn-memory/SKILL.md tests/test_plugin_assets.py
git commit -m "feat(cursor): bundle using-agentcairn-memory skill as package data"
```

---

## Task 2: `Host.skill_dir` field + cursor value

**Files:**
- Modify: `src/cairn/hosts/__init__.py` (the `Host` dataclass + the cursor `HOSTS` entry)
- Test: `tests/test_hosts.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_hosts.py`:

```python
def test_cursor_host_has_skill_dir():
    from cairn.hosts import get_host

    assert get_host("cursor").skill_dir == "~/.cursor/skills"


def test_non_skill_hosts_have_no_skill_dir():
    from cairn.hosts import HOSTS

    for h in HOSTS:
        if h.id != "cursor":
            assert h.skill_dir is None, h.id
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_hosts.py::test_cursor_host_has_skill_dir tests/test_hosts.py::test_non_skill_hosts_have_no_skill_dir -v`
Expected: FAIL — `AttributeError: 'Host' object has no attribute 'skill_dir'`.

- [ ] **Step 3: Add the field and set it on cursor**

In `src/cairn/hosts/__init__.py`, add a field to the `Host` dataclass (after `plugin_add`, keeping all fields with defaults so ordering is valid):

```python
    plugin_add: tuple[str, ...] | None = None  # argv after the cli to install the plugin
    # mcp hosts that also accept a SKILL.md (e.g. Cursor's ~/.cursor/skills); the
    # install command writes the using-agentcairn-memory skill there too.
    skill_dir: str | None = None
```

Then update the cursor entry in `HOSTS`:

```python
    Host("cursor", "Cursor", "json", "~/.cursor/mcp.json", skill_dir="~/.cursor/skills"),
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_hosts.py -v`
Expected: PASS (both new tests + existing host tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/hosts/__init__.py tests/test_hosts.py
git commit -m "feat(cursor): add Host.skill_dir, set ~/.cursor/skills on cursor"
```

---

## Task 3: Skill installer (`hosts/skills.py`)

**Files:**
- Create: `src/cairn/hosts/skills.py`
- Test: `tests/test_hosts.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_hosts.py`:

```python
def test_cursor_skill_text_is_the_bundled_skill():
    from cairn.hosts.skills import cursor_skill_text

    text = cursor_skill_text()
    assert "name: using-agentcairn-memory" in text
    assert text.startswith("---")


def test_install_skill_writes_file(tmp_path):
    from cairn.hosts.skills import cursor_skill_text, install_skill

    note = install_skill(tmp_path, dry=False)
    dest = tmp_path / "using-agentcairn-memory" / "SKILL.md"
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == cursor_skill_text()
    assert str(dest) in note


def test_install_skill_dry_writes_nothing(tmp_path):
    from cairn.hosts.skills import install_skill

    note = install_skill(tmp_path, dry=True)
    assert not (tmp_path / "using-agentcairn-memory").exists()
    assert "would install" in note
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_hosts.py -k "skill_text or install_skill" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.hosts.skills'`.

- [ ] **Step 3: Write the installer**

Create `src/cairn/hosts/skills.py`:

```python
# SPDX-License-Identifier: Apache-2.0
"""Install the using-agentcairn-memory SKILL.md into a skill-aware MCP host
(e.g. Cursor's ~/.cursor/skills). The skill body ships as package data under
cairn/assets/ so a pip-installed cairn can write it without the repo plugin/ dir."""

from __future__ import annotations

import importlib.resources
from pathlib import Path

from cairn.hosts._io import atomic_write


def cursor_skill_text() -> str:
    """The bundled using-agentcairn-memory SKILL.md, read from package data."""
    res = (
        importlib.resources.files("cairn")
        / "assets"
        / "using-agentcairn-memory"
        / "SKILL.md"
    )
    return res.read_text(encoding="utf-8")


def install_skill(skill_root: Path, *, dry: bool = False) -> str:
    """Write the agentcairn memory skill to <skill_root>/using-agentcairn-memory/SKILL.md.

    Idempotent (agentcairn's own file; overwrites/refreshes, no backup). dry=True
    returns a note and writes nothing."""
    dest = skill_root / "using-agentcairn-memory" / "SKILL.md"
    if dry:
        return f"would install skill → {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(dest, cursor_skill_text())
    return f"installed skill → {dest}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_hosts.py -k "skill_text or install_skill" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/hosts/skills.py tests/test_hosts.py
git commit -m "feat(cursor): add install_skill helper reading bundled package data"
```

---

## Task 4: `cairn install` installs the skill for skill-aware MCP hosts

**Files:**
- Modify: `src/cairn/cli.py` (the `install` command's mcp-host `else` branch, ~lines 484-491)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (the file already imports `runner` and `app`; mirror the existing `test_install_cursor_writes_entry` style):

```python
def test_install_cursor_writes_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    skill = tmp_path / ".cursor" / "skills" / "using-agentcairn-memory" / "SKILL.md"
    assert skill.is_file()
    assert "name: using-agentcairn-memory" in skill.read_text(encoding="utf-8")


def test_install_cursor_print_notes_skill_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--print"])
    assert r.exit_code == 0, r.output
    assert "would install skill" in r.output
    assert not (tmp_path / ".cursor" / "skills").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_cli.py -k "install_cursor_writes_skill or install_cursor_print_notes_skill" -v`
Expected: FAIL — the skill file is not written / the `--print` output lacks the note.

- [ ] **Step 3: Add the skill install to the mcp-host branch**

In `src/cairn/cli.py`, add the import to the existing `from cairn.hosts.*` import block at the top of `install` (alongside `from cairn.hosts.writers import write_host`):

```python
    from cairn.hosts.skills import install_skill
```

Then extend the mcp-host `else` branch so it installs the skill when `h.skill_dir` is set:

```python
            else:
                entry = mcp_entry(v, idx)
                out = write_host(h, entry, dry=print_only)
                if print_only:
                    typer.echo(f"# {h.label} ({h.config_path()})")
                    typer.echo(out)
                else:
                    typer.echo(f"✓ {h.label}: {out}")
                if h.skill_dir is not None:
                    note = install_skill(Path(h.skill_dir).expanduser(), dry=print_only)
                    typer.echo(f"  {note}")
```

(`Path` is already imported at the top of `cli.py`.)

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_cli.py -k "install_cursor" -v`
Expected: PASS (new skill tests + the existing `test_install_cursor_writes_entry` / `test_install_print_writes_nothing`).

- [ ] **Step 5: Run the full suite + lint**

Run: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests`
Expected: all green. (If ruff-format rewrites anything, `git add -A` and include it in the commit.)

- [ ] **Step 6: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cursor): cairn install cursor also installs the memory skill"
```

---

## Task 5: Documentation

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `website/src/lib/content.ts`

No tests for docs; verify by reading the diff. Keep claims accurate: Cursor stays an **MCP host** that **also installs the recall/remember skill** — it is NOT a plugin host.

- [ ] **Step 1: Find the current Cursor / host descriptions**

```bash
grep -rni "cursor" README.md CLAUDE.md website/src/lib/content.ts
grep -rni "mcp host\|install cursor\|plugin host\|using-agentcairn-memory" README.md CLAUDE.md
```

- [ ] **Step 2: Update README.md**

Where `cairn install cursor` / the MCP-host list is described, note that for Cursor the command now also installs the `using-agentcairn-memory` skill to `~/.cursor/skills/` (recall/remember guidance), in addition to writing `~/.cursor/mcp.json`. Match the surrounding wording/format; do not restructure unrelated sections.

- [ ] **Step 3: Update CLAUDE.md**

In the harness/hosts section, record that Cursor is an MCP host that also installs the memory skill (so future work knows the `skill_dir` mechanism exists). One or two sentences, matching the file's existing density.

- [ ] **Step 4: Update website/src/lib/content.ts**

If Cursor appears in a hosts/integrations list, update its description to "MCP + memory skill" (still an MCP host, not a plugin host). If the file references a hosts count or per-host blurb, keep it consistent. If Cursor is not mentioned there, skip this step (note it in the commit body).

- [ ] **Step 5: Verify the website still builds (only if content.ts changed)**

```bash
cd website && npm run build && cd ..
```

Expected: build succeeds. (If the project uses a different build command, check `website/package.json` scripts.)

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md website/src/lib/content.ts
git commit -m "docs(cursor): note install installs the memory skill alongside MCP"
```

---

## Final verification (before finishing the branch)

- [ ] Full suite + lint green: `uv run pytest && uv run ruff check src tests && uv run ruff format --check src tests`
- [ ] Wheel still bundles the asset: `uv build && python -c "import zipfile,glob; w=sorted(glob.glob('dist/agentcairn-*.whl'))[-1]; assert 'cairn/assets/using-agentcairn-memory/SKILL.md' in zipfile.ZipFile(w).namelist(); print('OK')" && rm -rf dist`
- [ ] **Dogfood:** `uv run cairn install cursor` on the real machine → assert both exist: `~/.cursor/mcp.json` has `mcpServers.agentcairn`, and `~/.cursor/skills/using-agentcairn-memory/SKILL.md` is present. Then `cairn install cursor --print` prints the MCP snippet and a `would install skill → …` note and writes nothing new.

---

## Self-Review (completed during planning)

- **Spec coverage:** bundled asset + sync test (Task 1 ↔ spec §A), `Host.skill_dir` (Task 2 ↔ §B), `install_skill`/`cursor_skill_text` (Task 3 ↔ §C), CLI mcp-host skill install + `--print` note (Task 4 ↔ §D), docs (Task 5 ↔ file-by-file row), wheel-build verification (Task 1 Step 5 + Final ↔ spec Testing). All spec sections map to a task.
- **Type consistency:** `install_skill(skill_root: Path, *, dry: bool = False) -> str` and `cursor_skill_text() -> str` are used identically in Tasks 3 and 4; the CLI calls `install_skill(Path(h.skill_dir).expanduser(), dry=print_only)` matching the keyword-only `dry`. `Host.skill_dir: str | None = None` matches the `get_host("cursor").skill_dir == "~/.cursor/skills"` assertion.
- **Placeholder scan:** no TBD/TODO; every code step shows the full code; commands have expected output.
