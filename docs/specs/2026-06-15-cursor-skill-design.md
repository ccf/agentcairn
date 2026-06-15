# Complete Cursor Integration — MCP + Skill

**Status:** Approved (2026-06-15)
**Affects:** `src/cairn/assets/` (new bundled skill), `src/cairn/hosts/` (Host.skill_dir + skill installer), `src/cairn/cli.py` (install also installs the skill), `README.md`/`CLAUDE.md`/website, tests. No change to the MCP server, ingest, consolidation, or the Markdown contract.

## Problem

Cursor is a memory *source* (ingest shipped, 0.15.0-pending) and an MCP *host* (`cairn install cursor` → `~/.cursor/mcp.json` gives recall/search/remember tools). But it lacks the **guidance** layer the other harnesses get from the `using-agentcairn-memory` skill — nothing tells the Cursor agent to recall before non-trivial work or remember durable facts. We want Cursor first-class on output.

## Research — Cursor's extensibility model (verified on disk + `cursor-agent` CLI)

- **Cursor has no Claude-style plugin system.** `cursor-agent` (the CLI) exposes `mcp`, `generate-rule|rule`, `create-chat`, `agent`, … but **no `plugin` command**, no marketplace, and `~/.cursor/plugins/local` is empty/unused. The user's "Cursor loads claude plugins" hypothesis does not hold.
- **Cursor uses `SKILL.md` skills — the same format we already ship.** Built-in/managed skills live in `~/.cursor/skills-cursor/<name>/SKILL.md` (with a `.cursor-managed-skills-manifest.json`). **Custom skills go in `~/.cursor/skills/` (personal) or `.cursor/skills/` (project)** — confirmed by Cursor's own `create-skill` skill ("Should this be a personal skill (`~/.cursor/skills/`) or project skill (`.cursor/skills/`)?"). The frontmatter (`name`, `description`) matches our `using-agentcairn-memory/SKILL.md`.
- **MCP** is `~/.cursor/mcp.json` (the existing install target; `cursor-agent mcp list/enable/disable` manages it). Cursor also has Rules (`.cursor/rules/*.mdc`) and Hooks (`create-hook`), but neither is needed here (the skill is the right analog; capture is already out-of-band via `cairn sweep`).

So "first-class Cursor" is **MCP (already wired) + our recall/remember Skill installed into `~/.cursor/skills/`** — Cursor's native idiom, no plugin bundle.

## Goal / decisions (brainstorm)

- `cairn install cursor` does two idempotent file operations: (1) write the MCP server to `~/.cursor/mcp.json` (**unchanged**), and (2) **install the `using-agentcairn-memory` skill** to `~/.cursor/skills/using-agentcairn-memory/SKILL.md`.
- Cursor **stays an MCP host** (`kind="mcp"`) — there is no plugin host to migrate to, and the mcp.json *is* Cursor's canonical MCP mechanism (no plugin bundles it), so we keep writing it (no "only bundle" migration).
- **Global** skill location (`~/.cursor/skills/`), matching our other global installs.
- Skill content ships as **package data** so a pip-installed `cairn` can write it without the repo's `plugin/` dir.

## Architecture

### A. Bundled skill asset (packaging)

A pip-installed `cairn` does not include the repo `plugin/` dir, so the skill content must live inside the package:
- **`src/cairn/assets/using-agentcairn-memory/SKILL.md`** (new) — a copy of `plugin/skills/using-agentcairn-memory/SKILL.md`, shipped in the wheel (hatchling's `packages = ["src/cairn"]` includes non-Python files under the package; verified by a build that the asset is present in the wheel).
- Read at runtime via `importlib.resources.files("cairn") / "assets" / "using-agentcairn-memory" / "SKILL.md"`.
- **Single-source enforced by test:** a test asserts the bundled asset is byte-identical to `plugin/skills/using-agentcairn-memory/SKILL.md`, so the two never drift. (The repo `plugin/` copy remains the one Claude/Codex/Antigravity bundle; the package asset is the copy the CLI installs into Cursor.)

### B. Host registry

In `src/cairn/hosts/__init__.py`, add a field to `Host`:
```python
    skill_dir: str | None = None  # mcp hosts that also take a SKILL.md (e.g. Cursor's ~/.cursor/skills)
```
and set it on the cursor entry:
```python
    Host("cursor", "Cursor", "json", "~/.cursor/mcp.json", skill_dir="~/.cursor/skills"),
```
(Cursor keeps `kind="mcp"`. No other host sets `skill_dir` for now; the field generalizes if another skill-aware MCP host appears.)

### C. Skill installer

`src/cairn/hosts/skills.py` (new):
```python
def cursor_skill_text() -> str:
    """The bundled using-agentcairn-memory SKILL.md, from package data."""
    return (importlib.resources.files("cairn") / "assets" / "using-agentcairn-memory" / "SKILL.md").read_text(encoding="utf-8")

def install_skill(skill_root: Path, *, dry: bool = False) -> str:
    """Write the agentcairn memory skill to <skill_root>/using-agentcairn-memory/SKILL.md.
    Idempotent (agentcairn's own file; overwrites/refreshes, no backup). dry → a note, no write."""
    dest = skill_root / "using-agentcairn-memory" / "SKILL.md"
    if dry:
        return f"would install skill → {dest}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(dest, cursor_skill_text())
    return f"installed skill → {dest}"
```
(Reuses `atomic_write` from `hosts._io`.)

### D. `cairn install` — install the skill for skill-aware MCP hosts

In `src/cairn/cli.py`, in the **mcp-host** branch (after `write_host`), if `h.skill_dir` is set, also install the skill and report it:
```python
            else:  # mcp host
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
`import install_skill` from `cairn.hosts.skills` alongside the existing host imports. No migration; the skill install is additive to the existing MCP write.

## Data flow

```
cairn install cursor
  → write_host(cursor, mcp_entry) → ~/.cursor/mcp.json    (UNCHANGED: recall/search/remember)
  → install_skill(~/.cursor/skills) → ~/.cursor/skills/using-agentcairn-memory/SKILL.md  (NEW)
  (--print: show the mcp snippet + "would install skill → …"; write nothing)
```

## Error handling

- The MCP write path is unchanged (non-destructive, idempotent, backup-first via the existing writer).
- Skill install: `mkdir -p` the dest dir; `atomic_write` (temp + rename) so a crash can't leave a partial file. Overwrites our own skill file (idempotent) — no backup needed (it is agentcairn's file, not user config).
- `--print` never writes (skill install is `dry`).
- A non-cursor MCP host (no `skill_dir`) → the skill step is skipped (the `if h.skill_dir is not None` guard).

## Testing / verification

- **Package-asset sync:** `src/cairn/assets/using-agentcairn-memory/SKILL.md` is byte-identical to `plugin/skills/using-agentcairn-memory/SKILL.md` (a test reads both and asserts equality), and `cursor_skill_text()` returns that content via `importlib.resources`.
- **Host registry:** `get_host("cursor").skill_dir == "~/.cursor/skills"`; other hosts' `skill_dir is None`.
- **install_skill:** writes `<root>/using-agentcairn-memory/SKILL.md` with the skill content (frontmatter `name: using-agentcairn-memory` present); `dry=True` writes nothing and returns the "would install" note.
- **CLI:** `cairn install cursor --print` shows the MCP snippet AND a skill note, writes nothing (no `mcp.json`, no skill file). `cairn install cursor` (tmp HOME) writes both `~/.cursor/mcp.json` (with `mcpServers.agentcairn`) and `~/.cursor/skills/using-agentcairn-memory/SKILL.md`. Existing cursor MCP-writer behavior unchanged.
- **Wheel build:** `uv build` (or the project's build) produces a wheel containing `cairn/assets/using-agentcairn-memory/SKILL.md` (asserted, or a documented manual check) — so a pip-installed `cairn install cursor` can find the skill.
- `uv run pytest` green; `uv run ruff` clean.
- **Dogfood:** `cairn install cursor` on the real machine → `~/.cursor/skills/using-agentcairn-memory/SKILL.md` exists and `~/.cursor/mcp.json` has `agentcairn`; start a `cursor-agent` session and confirm the recall/remember MCP tools resolve and the skill is listed.

## File-by-file

| File | Change |
|---|---|
| `src/cairn/assets/using-agentcairn-memory/SKILL.md` | **new** — bundled copy of the memory skill (package data) |
| `src/cairn/hosts/__init__.py` | add `Host.skill_dir`; set `skill_dir="~/.cursor/skills"` on cursor |
| `src/cairn/hosts/skills.py` | **new** — `cursor_skill_text()` + `install_skill()` |
| `src/cairn/cli.py` | `install` mcp-host branch: install the skill when `h.skill_dir` is set |
| `tests/test_hosts.py` / `tests/test_cli.py` / `tests/test_plugin_assets.py` | skill-dir registry, install_skill, CLI skill install + --print, asset-sync test |
| `README.md`, `CLAUDE.md`, `website/src/lib/content.ts` | Cursor gets the memory skill (MCP + skill), still an MCP host |

## Non-goals

- **No Cursor plugin host** — Cursor has no plugin system; it stays an MCP host that also installs a skill.
- **No Rule (`.mdc`) or hook** — the skill is the right analog; capture is already out-of-band via `cairn sweep`.
- **No per-project `.cursor/skills/`** install, and **no `cursor-agent` shell-out** (skills are plain files).
- No change to the MCP server, ingest, consolidation, reindex, or the Markdown contract.

## Open questions

None.
