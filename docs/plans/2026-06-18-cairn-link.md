# `cairn link` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An opt-in `cairn link` command that writes each note's top semantic neighbors into a `related:` frontmatter list of `[[wikilink]]` strings, so Obsidian draws graph edges + backlinks.

**Architecture:** A small pure-ish helper `_relink_note(path, desired, *, dry_run)` does the read→compare→write/clear of the `related:` frontmatter (returns `"linked" | "unchanged" | "cleared"`). The `link` CLI command resolves vault+index, iterates the index's live notes, computes each note's `desired` wikilinks via the existing `semantic_neighbors()`, calls the helper, and prints a summary. Obsidian-graph-focused — no parser/indexer change.

**Tech Stack:** Python 3.12, Typer CLI, DuckDB, python-frontmatter (via `cairn.vault`), pytest. Spec: `docs/specs/2026-06-18-cairn-link-design.md`.

---

## File Structure

- **Modify** `src/cairn/cli.py` — add `_relink_note(...)` helper + the `link` command.
- **Reuses** `cairn.search.engine.semantic_neighbors` + `open_search`, `cairn.paths` (`resolve_vault`/`index_for`), `cairn.vault` (`parse_note`/`write_note`).
- **Test:** `tests/test_cli.py` (CLI integration) + the helper is exercised through both unit-style and CLI tests there.

No new module — the command is small and lives with the other CLI commands; the helper keeps the per-note logic testable in isolation.

---

## Task 1: `_relink_note` helper

**Files:**
- Modify: `src/cairn/cli.py` (add the helper near the other module-level CLI helpers, e.g. after `_resolve_harnesses`)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — add
def test_relink_note_writes_related_when_changed(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    status = _relink_note(p, ["[[b]]", "[[c]]"])
    assert status == "linked"
    text = p.read_text()
    assert "related:" in text and "[[b]]" in text and "[[c]]" in text
    assert "alpha body" in text  # body preserved


def test_relink_note_unchanged_is_noop(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    _relink_note(p, ["[[b]]"])  # first write
    mtime1 = p.stat().st_mtime_ns
    import time as _t; _t.sleep(0.01)
    status = _relink_note(p, ["[[b]]"])  # same desired → no rewrite
    assert status == "unchanged"
    assert p.stat().st_mtime_ns == mtime1  # file untouched


def test_relink_note_clears_stale_related(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\nrelated:\n- '[[b]]'\n---\nalpha body\n")
    status = _relink_note(p, [])  # no neighbors now → clear
    assert status == "cleared"
    assert "related:" not in p.read_text()


def test_relink_note_empty_and_absent_is_unchanged(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    mtime1 = p.stat().st_mtime_ns
    import time as _t; _t.sleep(0.01)
    assert _relink_note(p, []) == "unchanged"  # no related, none desired
    assert p.stat().st_mtime_ns == mtime1


def test_relink_note_dry_run_writes_nothing(tmp_path):
    from cairn.cli import _relink_note

    p = tmp_path / "a.md"
    p.write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    mtime1 = p.stat().st_mtime_ns
    import time as _t; _t.sleep(0.01)
    assert _relink_note(p, ["[[b]]"], dry_run=True) == "linked"  # reports intent
    assert p.stat().st_mtime_ns == mtime1  # but writes nothing
    assert "related:" not in p.read_text()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k relink`
Expected: FAIL (`cannot import name '_relink_note'`).

- [ ] **Step 3: Implement** — add to `src/cairn/cli.py` (it already imports `parse_note`; ensure `write_note` is imported from `cairn.vault` — check `grep -n "from cairn.vault" src/cairn/cli.py` and add `write_note` if missing):

```python
def _relink_note(path: Path, desired: list[str], *, dry_run: bool = False) -> str:
    """Set/clear a note's `related:` frontmatter to `desired` (a list of "[[permalink]]"
    strings). Writes only when it differs from the current value. Returns one of
    "linked" (set/changed), "cleared" (removed a stale list), or "unchanged". The tool
    owns the `related:` field; body and other frontmatter are preserved via the
    parse_note→write_note fixpoint."""
    note = parse_note(path.read_text(encoding="utf-8"))
    current = note.frontmatter.get("related")
    if desired:
        if current == desired:
            return "unchanged"
        note.frontmatter["related"] = desired
        if not dry_run:
            path.write_text(write_note(note), encoding="utf-8")
        return "linked"
    # desired is empty
    if "related" in note.frontmatter:
        if not dry_run:
            del note.frontmatter["related"]
            path.write_text(write_note(note), encoding="utf-8")
        return "cleared"
    return "unchanged"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k relink`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): _relink_note helper (write/clear related: frontmatter, idempotent)"
```

---

## Task 2: the `cairn link` command

**Files:**
- Modify: `src/cairn/cli.py` (add the `link` command)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — add
def _seed_vault_indexed(tmp_path, monkeypatch, notes):
    """notes: list of (permalink, body). Build a vault + vault-scoped index (fake embedder)."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    v = tmp_path / "vault"; v.mkdir()
    for permalink, body in notes:
        (v / f"{permalink}.md").write_text(
            f"---\ntitle: {permalink}\npermalink: {permalink}\n---\n{body}\n"
        )
    assert runner.invoke(app, ["reindex", str(v), "--embedder", "fake"]).exit_code == 0
    return v


def test_link_writes_related_for_near_notes(tmp_path, monkeypatch):
    v = _seed_vault_indexed(tmp_path, monkeypatch, [
        ("ram", "scale the RAM to 4 gigabytes for the build"),
        ("ram2", "increase memory RAM to 8 gigabytes"),
        ("coffee", "pour over coffee brewing beans"),
    ])
    r = runner.invoke(app, ["link", "--vault", str(v), "--top", "2", "--min-score", "0.0"])
    assert r.exit_code == 0, r.output
    ram = (v / "ram.md").read_text()
    assert "related:" in ram and "[[ram2]]" in ram  # near neighbor linked
    assert "[[ram]]" not in ram  # never links to self


def test_link_is_idempotent(tmp_path, monkeypatch):
    v = _seed_vault_indexed(tmp_path, monkeypatch, [
        ("ram", "scale the RAM to 4 gigabytes"),
        ("ram2", "increase memory RAM to 8 gigabytes"),
    ])
    assert runner.invoke(app, ["link", "--vault", str(v), "--min-score", "0.0"]).exit_code == 0
    mtimes = {p.name: p.stat().st_mtime_ns for p in v.glob("*.md")}
    import time as _t; _t.sleep(0.01)
    r = runner.invoke(app, ["link", "--vault", str(v), "--min-score", "0.0"])
    assert r.exit_code == 0, r.output
    assert {p.name: p.stat().st_mtime_ns for p in v.glob("*.md")} == mtimes  # nothing rewritten


def test_link_dry_run_writes_nothing(tmp_path, monkeypatch):
    v = _seed_vault_indexed(tmp_path, monkeypatch, [
        ("ram", "scale the RAM to 4 gigabytes"),
        ("ram2", "increase memory RAM to 8 gigabytes"),
    ])
    r = runner.invoke(app, ["link", "--vault", str(v), "--min-score", "0.0", "--dry-run"])
    assert r.exit_code == 0, r.output
    assert all("related:" not in p.read_text() for p in v.glob("*.md"))  # nothing written


def test_link_missing_index_exits_1(tmp_path, monkeypatch):
    from cairn import paths
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    v = tmp_path / "vault"; v.mkdir()
    r = runner.invoke(app, ["link", "--vault", str(v)])
    assert r.exit_code == 1
    assert "no index" in r.output.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k "link and not relink"`
Expected: FAIL (no `link` command).

- [ ] **Step 3: Implement** — add the command to `src/cairn/cli.py` (model the option block / vault+index resolution on the existing `recall`/`doctor` commands; `semantic_neighbors` + `open_search` come from `cairn.search.engine` — confirm/add the import: `grep -n "from cairn.search" src/cairn/cli.py`):

```python
@app.command()
def link(
    vault: Path = typer.Option(
        None, "--vault", help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn)."
    ),
    index: Path = typer.Option(
        None, "--index", help="Index .duckdb path (default: derived from the vault)."
    ),
    top: int = typer.Option(5, "--top", help="Max neighbors to link per note."),
    min_score: float = typer.Option(0.6, "--min-score", help="Minimum cosine to link a neighbor."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change; write nothing."),
) -> None:
    """Write each note's top semantic neighbors into a `related:` frontmatter list of
    [[wikilinks]] (populates the Obsidian graph). Opt-in and idempotent; re-run to refresh.
    Reads the current index — run `cairn sweep`/`reindex` first for fresh results."""
    vault_dir = paths.resolve_vault(vault)
    idx = paths.index_for(index, vault_dir)
    if not idx.exists():
        typer.echo(f"no index at {idx} — run `cairn reindex <vault>` first")
        raise typer.Exit(1)
    con = open_search(str(idx))
    linked = unchanged = cleared = errors = 0
    try:
        rows = con.execute(
            "SELECT permalink, path FROM notes WHERE superseded_by IS NULL"
        ).fetchall()
        for permalink, path in rows:
            if not path:
                continue
            try:
                nbrs = semantic_neighbors(con, permalink, k=top, min_score=min_score)
                desired = [f"[[{n['permalink']}]]" for n in nbrs]
                status = _relink_note(Path(path), desired, dry_run=dry_run)
            except Exception as exc:  # best-effort per note
                errors += 1
                typer.echo(f"  skip {permalink}: {exc}")
                continue
            if status == "linked":
                linked += 1
            elif status == "cleared":
                cleared += 1
            else:
                unchanged += 1
    finally:
        con.close()
    prefix = "[dry-run] " if dry_run else ""
    suffix = f" · {errors} errors" if errors else ""
    typer.echo(f"{prefix}linked {linked} · unchanged {unchanged} · cleared {cleared}{suffix}")
```

Make sure `open_search` and `semantic_neighbors` are imported at the top of `cli.py` from `cairn.search.engine` (add to the existing `from cairn.search...` import or a new import line).

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k link`
Expected: PASS (all link + relink tests). Then `uv run pytest -q` (full suite green).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): cairn link — write semantic neighbors as related: frontmatter"
```

---

## Task 3: Docs + full verify

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: CHANGELOG** — add under `## [Unreleased]` (create the section under the header if missing):

```markdown
### Added
- `cairn link` — opt-in command that writes each note's top semantic neighbors into a `related:`
  frontmatter list of `[[wikilinks]]`, populating the Obsidian graph (edges + backlinks). Idempotent
  (writes only when a note's links change), one-directional (Obsidian backlinks show the reverse),
  `--top`/`--min-score` tunable, `--dry-run` to preview. Reuses 0.19.0's `semantic_neighbors`.
```

- [ ] **Step 2: Full verify** — run and confirm:

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q`
Expected: all green (3 pre-existing skips OK).
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.
Run: `uv run --no-project --with pytest pytest plugin/tests/ -q`
Expected: green (plugin suite is outside the repo-root testpaths — run it explicitly).

- [ ] **Step 3: Manual smoke (dry-run on the real vault, writes nothing)**

```bash
uvx --from 'agentcairn>=0.2' cairn link --vault ~/agentcairn --dry-run
```
Expected: a `[dry-run] linked N · unchanged M · cleared K` summary, no files changed.

- [ ] **Step 4: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: cairn link command"
```

---

## Self-Review Notes (author)

- **Spec coverage:** command/interface + resolution → Task 2; per-note write/clear/unchanged + idempotency + dry-run → Task 1 (helper) & Task 2 (wiring); superseded source excluded (`WHERE superseded_by IS NULL`) + superseded targets excluded (inside `semantic_neighbors`) → Task 2; best-effort per note → Task 2 try/except; missing index exit 1 → Task 2; summary line → Task 2; CHANGELOG/rollout → Task 3. No gaps.
- **No parser/indexer change** (Q1 minimal scope) — confirmed: only `cli.py` + CHANGELOG are modified.
- **Naming consistency:** `_relink_note(path, desired, *, dry_run=False) -> str` returns `"linked"|"cleared"|"unchanged"`; the command tallies those exact strings. `semantic_neighbors(con, permalink, k=top, min_score=min_score)` matches the 0.19.0 signature.
- **No placeholders:** every code step is complete; the two "confirm/add import" notes give exact grep commands.
- **plugin/tests/** included in verify (the CI gotcha).
- **Idempotency check:** `_relink_note` writes only when `current != desired`; the idempotent-run test asserts mtimes are unchanged.
