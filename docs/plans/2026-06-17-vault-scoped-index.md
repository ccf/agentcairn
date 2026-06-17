# Vault-Scoped Index Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the DuckDB index a pure function of the vault (`~/.cache/agentcairn/indexes/<vault_key>.duckdb`) so a scratch/test vault can never write into the production index, while keeping `--index`/`CAIRN_INDEX` as an explicit escape hatch.

**Architecture:** A new `src/cairn/paths.py` centralizes all vault-derived paths (vault resolution, `vault_key`, index, ledger, judged-cache) and a one-time legacy-index rehome. Every CLI command and the MCP server resolve the index through it. `cairn install` and the plugin hook scripts stop pinning `CAIRN_INDEX`. `doctor` gains a vault-vs-index drift check.

**Tech Stack:** Python 3.12, Typer CLI, DuckDB, hatchling, pytest. Spec: `docs/specs/2026-06-17-vault-scoped-index-design.md`.

---

## File Structure

- **Create** `src/cairn/paths.py` — the shared resolver (cache root, vault, vault_key, index, ledger, judged-cache, legacy rehome). One responsibility: turn a vault into every derived path.
- **Create** `tests/test_paths.py` — unit tests for the resolver + rehome.
- **Modify** `src/cairn/cli.py` — `reindex`, `index-status`, `recall`, `recent`, `sweep`, `ingest`, `doctor`: resolve index via `paths.index_for`; add `--vault` where missing; drop inline `vault_key`/ledger derivation; add doctor drift check. Remove `_default_index()`.
- **Modify** `src/cairn/mcp/server.py` — `resolve_config` derives the index from the resolved vault.
- **Modify** `src/cairn/hosts/entry.py` — `mcp_entry` stops writing `CAIRN_INDEX`.
- **Modify** `src/cairn/hosts/plugins.py` — add `migrate_stale_cairn_index` (JSON + TOML) config migrator.
- **Modify** `src/cairn/cli.py` install command — call the migrator after successful install.
- **Modify** `plugin/.../scripts/session-start.sh`, `session-end.sh` — pass `--vault` only, no index arg.
- **Modify** `tests/test_cli.py`, `tests/test_mcp*.py`, `tests/test_install*.py` — adjust expectations.
- **Modify** `CHANGELOG.md`, `README.md`, `CLAUDE.md` — document the new default + override.

---

## Task 1: `paths.py` — shared vault→path resolver

**Files:**
- Create: `src/cairn/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_paths.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn import paths


def test_resolve_vault_precedence(monkeypatch, tmp_path):
    # explicit wins
    assert paths.resolve_vault(tmp_path / "x", env={}) == (tmp_path / "x")
    # env next
    assert paths.resolve_vault(None, env={"CAIRN_VAULT": str(tmp_path / "y")}) == (tmp_path / "y")
    # default last
    assert paths.resolve_vault(None, env={}) == Path.home() / "agentcairn"


def test_vault_key_stable_and_distinct(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert paths.vault_key(a) == paths.vault_key(a)  # stable
    assert paths.vault_key(a) != paths.vault_key(b)  # distinct
    assert len(paths.vault_key(a)) == 16


def test_default_index_is_vault_scoped(tmp_path):
    idx = paths.default_index(tmp_path / "v")
    assert idx == paths.cache_root() / "indexes" / f"{paths.vault_key(tmp_path / 'v')}.duckdb"


def test_resolve_index_precedence(tmp_path):
    vault = tmp_path / "v"
    # explicit wins
    assert paths.resolve_index(tmp_path / "x.duckdb", vault, env={}) == (tmp_path / "x.duckdb")
    # CAIRN_INDEX next
    assert paths.resolve_index(None, vault, env={"CAIRN_INDEX": str(tmp_path / "e.duckdb")}) == (
        tmp_path / "e.duckdb"
    )
    # vault-derived default last
    assert paths.resolve_index(None, vault, env={}) == paths.default_index(vault)


def test_ledger_helpers_match_existing_scheme(tmp_path):
    vault = tmp_path / "v"
    assert paths.default_ledger(vault) == paths.cache_root() / "ledgers" / f"{paths.vault_key(vault)}.sha256"
    assert paths.judged_cache(vault) == paths.cache_root() / "ledgers" / f"{paths.vault_key(vault)}.judged.jsonl"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_paths.py -q`
Expected: FAIL (`ModuleNotFoundError: cairn.paths`).

- [ ] **Step 3: Implement `paths.py`**

```python
# src/cairn/paths.py
# SPDX-License-Identifier: Apache-2.0
"""Vault-derived paths. The index/ledger/judged-cache are pure functions of the
vault root: explicit arg → env → derived default (`<cache>/indexes/<vault_key>.duckdb`).
This is the single home for the `vault_key` scheme the ledger already used inline."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from cairn.config import cairn_env

_DEFAULT_VAULT = Path.home() / "agentcairn"


def cache_root() -> Path:
    return Path.home() / ".cache" / "agentcairn"


def resolve_vault(explicit: Path | str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """--vault arg → CAIRN_VAULT → ~/agentcairn (matches the `vault` knob default)."""
    if explicit is not None:
        return Path(explicit).expanduser()
    if env is None:
        env = cairn_env()
    v = env.get("CAIRN_VAULT")
    return Path(v).expanduser() if v else _DEFAULT_VAULT


def vault_key(vault: Path | str) -> str:
    """16-hex of sha256(resolved vault path). Same scheme the ledger used inline,
    so existing `ledgers/<key>.*` files keep matching."""
    return hashlib.sha256(str(Path(vault).expanduser().resolve()).encode()).hexdigest()[:16]


def default_index(vault: Path | str) -> Path:
    return cache_root() / "indexes" / f"{vault_key(vault)}.duckdb"


def resolve_index(
    explicit: Path | str | None, vault: Path | str, env: Mapping[str, str] | None = None
) -> Path:
    """--index arg → CAIRN_INDEX → default_index(vault). Pure (no side effects)."""
    if explicit is not None:
        return Path(explicit).expanduser()
    if env is None:
        env = cairn_env()
    e = env.get("CAIRN_INDEX")
    if e:
        return Path(e).expanduser()
    return default_index(vault)


def default_ledger(vault: Path | str) -> Path:
    return cache_root() / "ledgers" / f"{vault_key(vault)}.sha256"


def judged_cache(vault: Path | str) -> Path:
    return cache_root() / "ledgers" / f"{vault_key(vault)}.judged.jsonl"
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_paths.py -q`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/paths.py tests/test_paths.py
git commit -m "feat(paths): vault-derived index/ledger resolver"
```

---

## Task 2: Legacy global-index auto-rehome

**Files:**
- Modify: `src/cairn/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_paths.py
import duckdb


def _make_index(path, note_path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE notes (permalink TEXT, path TEXT)")
    con.execute("INSERT INTO notes VALUES ('a', ?)", [note_path])
    con.close()


def test_migrate_legacy_index_rehomes_by_inferred_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    legacy = (tmp_path / "cache" / "index.duckdb")
    vault_root = "/Users/x/somevault"
    _make_index(legacy, f"{vault_root}/memories/a.md")
    moved = paths.migrate_legacy_index(env={})
    assert moved == paths.default_index(vault_root)
    assert moved.exists() and not legacy.exists()


def test_migrate_legacy_index_noops_when_cairn_index_set(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    legacy = (tmp_path / "cache" / "index.duckdb")
    _make_index(legacy, "/Users/x/v/memories/a.md")
    assert paths.migrate_legacy_index(env={"CAIRN_INDEX": "/somewhere/i.duckdb"}) is None
    assert legacy.exists()  # untouched


def test_migrate_legacy_index_noops_when_no_legacy(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    assert paths.migrate_legacy_index(env={}) is None


def test_index_for_triggers_migration(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    legacy = (tmp_path / "cache" / "index.duckdb")
    vault_root = "/Users/x/v2"
    _make_index(legacy, f"{vault_root}/memories/a.md")
    # index_for with no explicit/env → migrates, then resolves the derived path
    got = paths.index_for(None, vault_root, env={})
    assert got == paths.default_index(vault_root) and got.exists()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_paths.py -q`
Expected: FAIL (`migrate_legacy_index`/`index_for` not defined).

- [ ] **Step 3: Implement rehome + `index_for`**

Add to `src/cairn/paths.py`:

```python
def migrate_legacy_index(env: Mapping[str, str] | None = None) -> Path | None:
    """One-time best-effort: if the legacy global `<cache>/index.duckdb` exists and
    CAIRN_INDEX is unset, infer its vault root from a stored note path and move it to
    the derived `indexes/<key>.duckdb` slot. Returns the new path, or None if it did
    nothing. Never raises — a missing/failed migration just means a lazy rebuild."""
    if env is None:
        env = cairn_env()
    if env.get("CAIRN_INDEX"):
        return None
    legacy = cache_root() / "index.duckdb"
    if not legacy.exists():
        return None
    try:
        import duckdb

        con = duckdb.connect(str(legacy), read_only=True)
        row = con.execute(
            "SELECT path FROM notes WHERE path LIKE '%/memories/%' LIMIT 1"
        ).fetchone()
        con.close()
        if not row or not row[0]:
            return None
        vault_root = str(row[0]).split("/memories/")[0]
        target = default_index(vault_root)
        if target.exists():
            return None  # derived slot already populated — leave legacy in place
        target.parent.mkdir(parents=True, exist_ok=True)
        legacy.rename(target)
        return target
    except Exception:
        return None


def index_for(
    explicit: Path | str | None, vault: Path | str, env: Mapping[str, str] | None = None
) -> Path:
    """resolve_index + a one-time legacy rehome when falling back to the derived
    default. Commands should call this instead of resolve_index directly."""
    if env is None:
        env = cairn_env()
    if explicit is None and not env.get("CAIRN_INDEX"):
        migrate_legacy_index(env)
    return resolve_index(explicit, vault, env)
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_paths.py -q`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/paths.py tests/test_paths.py
git commit -m "feat(paths): one-time legacy-index auto-rehome + index_for"
```

---

## Task 3: Wire `sweep`/`ingest`/`reindex` to the resolver

**Files:**
- Modify: `src/cairn/cli.py` (`reindex` ~146-172, `sweep` ~617-700, `ingest` ~738-810)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — add
def test_sweep_default_index_is_vault_scoped(tmp_path, monkeypatch):
    """With no --index and no CAIRN_INDEX, sweep writes the vault-derived index."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    projects = tmp_path / "projects"
    cwd = "/Users/x/proj"
    _seed_transcript(projects, cwd, "s1",
                     [("user", "We decided to always escape the ATTACH path before interpolating it.")])
    vault = tmp_path / "vault"; vault.mkdir()
    r = runner.invoke(app, ["sweep", "--vault", str(vault), "--transcripts-dir", str(projects),
                            "--harness", "claude-code", "--project", cwd, "--embedder", "fake",
                            "--ledger", str(tmp_path / "led.sha256")],
                      env={"CAIRN_JUDGE": "none"})
    assert r.exit_code == 0, r.output
    assert paths.default_index(vault).exists()  # vault-scoped, not the global path
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py::test_sweep_default_index_is_vault_scoped -q`
Expected: FAIL (index resolves to global `_default_index()`, not the vault-scoped path).

- [ ] **Step 3: Update the three commands**

In `src/cairn/cli.py`, add to imports near the top: `from cairn import paths`.

`reindex` — replace `idx = index or _default_index()` (line ~161) with:
```python
    idx = paths.index_for(index, vault)
```

`sweep` (~617) — replace the inline ledger/index block. Current:
```python
    vault_key = hashlib.sha256(str(vault.resolve()).encode()).hexdigest()[:16]
    if ledger is not None:
        led_path = ledger
    else:
        led_path = Path.home() / ".cache" / "agentcairn" / "ledgers" / f"{vault_key}.sha256"
    led = DedupLedger(led_path)
    ...
    idx = index or _default_index()
```
becomes:
```python
    led_path = ledger if ledger is not None else paths.default_ledger(vault)
    led = DedupLedger(led_path)
    ...
    idx = paths.index_for(index, vault)
```
and update the `judged_cache=` argument (currently `JudgedCache(led_path.parent / f"{vault_key}.judged.jsonl")`) to keep the judged-cache next to whichever ledger is used (explicit or derived):
```python
        judged_cache=JudgedCache(led_path.parent / f"{paths.vault_key(vault)}.judged.jsonl"),
```

`ingest` (~738) — same two edits (the `vault_key`/`led_path`/`idx` block and the `judged_cache=` argument), mirroring sweep.

Then **remove the now-unused `_default_index()`** (lines 118-124) and its `hashlib` import if unused elsewhere (grep first: `grep -n hashlib src/cairn/cli.py`).

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q`
Expected: PASS (new test green; pre-existing sweep/ingest tests that pass `--index`/`--ledger` still pass because explicit args win).

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): vault-scope index+ledger for sweep/ingest/reindex"
```

---

## Task 4: Add `--vault` to read commands (`recall`/`search`/`recent`/`index-status`)

**Files:**
- Modify: `src/cairn/cli.py` (`index-status` ~175, `recall` ~195, `recent` ~256; there is no separate `search` CLI command — `recall` is the CLI surface, `search` is MCP-only, handled in Task 6)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli.py — add
def test_recall_derives_index_from_vault(tmp_path, monkeypatch):
    """recall with --vault (no --index) reads the vault-derived index sweep wrote."""
    from cairn import paths

    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha apple brewing\n")
    idx = paths.default_index(vault)
    r = runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"])  # derives same path
    assert r.exit_code == 0, r.output
    assert idx.exists()
    s = runner.invoke(app, ["recall", "apple brewing", "--vault", str(vault),
                            "--embedder", "fake", "--no-rerank"])
    assert s.exit_code == 0, s.output
    assert "a" in s.output
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py::test_recall_derives_index_from_vault -q`
Expected: FAIL (`recall` has no `--vault`; resolves global index → "no index").

- [ ] **Step 3: Add `--vault` + derive**

For each of `index-status`, `recall`, `recent`, add a `--vault` option and resolve the index from it. Pattern — add this option to the signature:
```python
    vault: Path = typer.Option(
        None, "--vault", help="Vault dir; the index is derived from it (default: CAIRN_VAULT or ~/agentcairn)."
    ),
```
and replace `idx = index or _default_index()` with:
```python
    idx = paths.index_for(index, paths.resolve_vault(vault))
```
(`paths.resolve_vault(vault)` turns the optional `--vault` into the concrete vault: arg → `CAIRN_VAULT` → `~/agentcairn`.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): --vault on recall/recent/index-status derives the index"
```

---

## Task 5: MCP server derives index from the resolved vault

**Files:**
- Modify: `src/cairn/mcp/server.py` (`resolve_config` ~20-45)
- Test: `tests/test_mcp_server.py` (or the existing server test file — grep `resolve_config`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mcp_server.py — add (create file if absent)
from cairn import paths
from cairn.mcp.server import resolve_config


def test_resolve_config_derives_index_from_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    monkeypatch.delenv("CAIRN_INDEX", raising=False)
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "v"))
    vault, index, _ = resolve_config()
    assert index == str(paths.default_index(tmp_path / "v"))


def test_resolve_config_index_env_still_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("CAIRN_VAULT", str(tmp_path / "v"))
    monkeypatch.setenv("CAIRN_INDEX", str(tmp_path / "explicit.duckdb"))
    _, index, _ = resolve_config()
    assert index == str(tmp_path / "explicit.duckdb")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_mcp_server.py -q`
Expected: FAIL (index resolves to the global `_DEFAULT_INDEX`, not vault-derived).

- [ ] **Step 3: Update `resolve_config`**

In `src/cairn/mcp/server.py`, add `from cairn import paths` and replace the resolution body:
```python
    settings = cairn_env()
    resolved_vault = vault or settings.get("CAIRN_VAULT")
    if resolved_vault:
        resolved_vault = os.path.expanduser(resolved_vault)
    vault_path = paths.resolve_vault(resolved_vault, env=settings)
    resolved_index = str(paths.index_for(index, vault_path, env=settings))
    resolved_embedder = embedder or settings.get("CAIRN_EMBEDDER") or _DEFAULT_EMBEDDER
    return resolved_vault, resolved_index, resolved_embedder
```
Remove the now-unused `_DEFAULT_INDEX` constant. Keep `resolved_vault` as the (possibly None) raw value returned in the tuple to preserve the existing contract; the index uses the concrete `vault_path`.

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_mcp_server.py tests/ -q -k "mcp or server"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/mcp/server.py tests/test_mcp_server.py
git commit -m "feat(mcp): derive index from CAIRN_VAULT when CAIRN_INDEX unset"
```

---

## Task 6: `cairn install` stops pinning `CAIRN_INDEX` + stale-index migrator

**Files:**
- Modify: `src/cairn/hosts/entry.py` (`mcp_entry`)
- Modify: `src/cairn/hosts/plugins.py` (add `migrate_stale_cairn_index`)
- Modify: `src/cairn/cli.py` install command (call migrator after success; drop `idx` plumbing into `mcp_entry`)
- Test: `tests/test_cli.py` install tests

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — add / adjust
def test_install_cursor_omits_cairn_index(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    import json as _j
    env = _j.loads((tmp_path / ".cursor" / "mcp.json").read_text())["mcpServers"]["agentcairn"]["env"]
    assert env["CAIRN_VAULT"] == str((tmp_path / "v").resolve())
    assert "CAIRN_INDEX" not in env  # derived from the vault now


def test_migrate_stale_cairn_index_strips_json(tmp_path):
    from cairn.hosts.plugins import migrate_stale_cairn_index
    import json as _j
    cfg = tmp_path / "mcp.json"
    cfg.write_text(_j.dumps({"mcpServers": {"agentcairn": {"command": "uvx", "args": ["agentcairn"],
                                                           "env": {"CAIRN_VAULT": "/v", "CAIRN_INDEX": "/old/i.duckdb"}}}}))
    changed = migrate_stale_cairn_index(cfg, fmt="json")
    assert changed is True
    env = _j.loads(cfg.read_text())["mcpServers"]["agentcairn"]["env"]
    assert "CAIRN_INDEX" not in env and env["CAIRN_VAULT"] == "/v"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k "cairn_index or stale"`
Expected: FAIL (`CAIRN_INDEX` still written; `migrate_stale_cairn_index` undefined).

- [ ] **Step 3: Implement**

`src/cairn/hosts/entry.py` — `mcp_entry` drops the index:
```python
def mcp_entry(vault: str) -> dict:
    """The MCP server config agentcairn writes into a host: `uvx agentcairn` with
    CAIRN_VAULT. The index is derived from the vault, so CAIRN_INDEX is not pinned."""
    return {"command": "uvx", "args": ["agentcairn"], "env": {"CAIRN_VAULT": vault}}
```

`src/cairn/cli.py` install — update the call site (~496) `entry = mcp_entry(v, idx)` → `entry = mcp_entry(v)`, and drop the now-unused `idx`/`default_index` lines (~431-434, 436) for the MCP path. (Keep `v`/`default_vault`.) After a successful MCP-host write, call the migrator on that host's config path:
```python
        if h.kind != "plugin":
            from cairn.hosts.plugins import migrate_stale_cairn_index
            fmt = "toml" if h.format == "toml" else "json"
            if not print_only:
                migrate_stale_cairn_index(h.config_path(), fmt=fmt)
```

`src/cairn/hosts/plugins.py` — add (mirror the existing `migrate_*` helpers' style):
```python
def migrate_stale_cairn_index(path, *, fmt: str) -> bool:
    """Remove a stale CAIRN_INDEX from agentcairn's env block in an existing host
    config (the index is now vault-derived). Returns True if it changed the file.
    Best-effort: missing/unparseable file → False, never raises."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        return False
    try:
        text = p.read_text(encoding="utf-8")
        if "CAIRN_INDEX" not in text:
            return False
        if fmt == "json":
            import json
            data = json.loads(text)
            env = data.get("mcpServers", {}).get("agentcairn", {}).get("env", {})
            if "CAIRN_INDEX" not in env:
                return False
            env.pop("CAIRN_INDEX")
            p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            return True
        else:  # toml — agentcairn writes a flat [mcp_servers.agentcairn.env] table
            import re
            new = re.sub(r'(?m)^\s*CAIRN_INDEX\s*=.*\n', "", text)
            if new == text:
                return False
            p.write_text(new, encoding="utf-8")
            return True
    except Exception:
        return False
```

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q`
Expected: PASS. (Update any existing install test that asserted `CAIRN_INDEX` in the env — e.g. `test_install_defaults_honor_config_file` — to drop that assertion.)

- [ ] **Step 5: Commit**

```bash
git add src/cairn/hosts/entry.py src/cairn/hosts/plugins.py src/cairn/cli.py tests/test_cli.py
git commit -m "feat(install): stop pinning CAIRN_INDEX + strip stale CAIRN_INDEX"
```

---

## Task 7: Plugin hook scripts pass `--vault` only

**Files:**
- Modify: `plugin/.../scripts/session-end.sh`, `session-start.sh` (find: `find /Users/ccf/git/agentcairn/plugin -name 'session-*.sh'`)
- Test: `tests/test_hook_scripts.py` (create — a smoke test on the rendered command)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_hook_scripts.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

_SCRIPTS = list(Path("plugin").rglob("scripts/session-end.sh")) + list(
    Path("plugin").rglob("scripts/session-start.sh")
)


def test_hook_scripts_pass_vault_not_index():
    assert _SCRIPTS, "hook scripts not found"
    for s in _SCRIPTS:
        body = s.read_text(encoding="utf-8")
        # cairn invocations use --vault and never --index (index is vault-derived)
        assert "--vault" in body
        assert "--index" not in body
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hook_scripts.py -q`
Expected: FAIL (`--index` present in the scripts).

- [ ] **Step 3: Edit the scripts**

In `session-end.sh`, drop the `INDEX=` line and change the sweep call from `--vault "$VAULT" --index "$INDEX"` to `--vault "$VAULT"`. In `session-start.sh`, drop `INDEX=`, and change `cairn recent --index "$INDEX"` → `cairn recent --vault "$VAULT"`, and the first-run guard `if [ ! -f "$INDEX" ]` → derive once with `INDEX=$($CAIRN _index-path --vault "$VAULT" 2>/dev/null)` **OR** simplify the guard to check the derived path. Simplest robust change: keep the warm/first-run detached job but gate it on the recent output instead of `$INDEX` existence — replace the `if [ ! -f "$INDEX" ]` block with a check that runs `init`+`warm` detached only when `cairn recent --vault "$VAULT" --json` returns empty notes. Keep `$2` accepted-but-ignored for back-compat with the existing `hooks.json` arg.

(Implementation note for the executor: keep both scripts' "always exit 0 / never block" contract; only the index plumbing changes.)

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hook_scripts.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add plugin tests/test_hook_scripts.py
git commit -m "feat(plugin): hook scripts derive index from --vault"
```

---

## Task 8: `doctor` — `--vault` + drift check

**Files:**
- Modify: `src/cairn/cli.py` (`doctor` ~702-735)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py — add
def test_doctor_reports_drift_on_dead_path(tmp_path, monkeypatch):
    from cairn import paths
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    idx = paths.default_index(vault)
    assert runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"]).exit_code == 0
    # delete the on-disk note so the index path is now dead
    (vault / "a.md").unlink()
    r = runner.invoke(app, ["doctor", "--vault", str(vault)])
    assert "DRIFT" in r.output
    assert "indexed" in r.output.lower()  # mentions the missing-on-disk count


def test_doctor_reports_drift_on_unindexed_note(tmp_path, monkeypatch):
    from cairn import paths
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    assert runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"]).exit_code == 0
    (vault / "b.md").write_text("---\ntitle: B\npermalink: b\n---\nbeta body\n")  # unindexed
    r = runner.invoke(app, ["doctor", "--vault", str(vault)])
    assert "DRIFT" in r.output


def test_doctor_ok_when_in_sync(tmp_path, monkeypatch):
    from cairn import paths
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    vault = tmp_path / "vault"; vault.mkdir()
    (vault / "a.md").write_text("---\ntitle: A\npermalink: a\n---\nalpha body\n")
    assert runner.invoke(app, ["reindex", str(vault), "--embedder", "fake"]).exit_code == 0
    r = runner.invoke(app, ["doctor", "--vault", str(vault)])
    assert "status: OK" in r.output
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k doctor`
Expected: FAIL (`doctor` has no `--vault`; no DRIFT logic).

- [ ] **Step 3: Implement**

Add a `--vault` option to `doctor` (same option block as Task 4) and resolve `vault_dir = paths.resolve_vault(vault)`, `idx = paths.index_for(index, vault_dir)`. After the existing chunk/embedding `problems` checks and before `status: OK`, add the drift check (open the index read-only — reuse the existing `con` before `con.close()`, or reopen):
```python
    # Drift: index vs on-disk vault. Dead paths or unindexed notes mean the index
    # was built against a different/stale vault (the 2026-06-17 footgun).
    import duckdb as _ddb
    dcon = _ddb.connect(str(idx), read_only=True)
    indexed = dcon.execute("SELECT path FROM notes").fetchall()
    dcon.close()
    indexed_missing = sum(1 for (p,) in indexed if p and not Path(p).exists())
    mem_dir = vault_dir / "memories"
    on_disk = {p.stem for p in mem_dir.glob("*.md")} if mem_dir.exists() else set()
    indexed_stems = {Path(p).stem for (p,) in indexed if p}
    disk_unindexed = len(on_disk - indexed_stems)
    if indexed_missing or disk_unindexed:
        typer.echo(
            f"status: DRIFT — {indexed_missing} indexed note(s) missing on disk, "
            f"{disk_unindexed} on-disk note(s) unindexed. Fix: cairn reindex {vault_dir}"
        )
        raise typer.Exit(1)
```
Keep the existing `PROBLEM`/`Exit(1)` block above it; `status: OK` only prints when neither problems nor drift fire.

- [ ] **Step 4: Run to verify pass**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -q -k doctor`
Expected: PASS (incl. the existing `test_doctor_command_healthy` — note it passes `--index` only; with no `--vault` it resolves `~/agentcairn`, whose `memories/` likely differs, so **update that test to also pass `--vault`** pointing at its temp vault, or assert it still prints counts. Prefer: pass `--vault str(vault)` in `test_doctor_command_healthy`.)

- [ ] **Step 5: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(doctor): --vault + vault/index drift check"
```

---

## Task 9: Docs + CHANGELOG + full verify

**Files:**
- Modify: `CHANGELOG.md`, `README.md`, `CLAUDE.md`

- [ ] **Step 1: CHANGELOG**

Add under `## [Unreleased]`:
```markdown
### Changed
- The DuckDB index default is now **vault-scoped**: `~/.cache/agentcairn/indexes/<vault_key>.duckdb`,
  derived from the vault. A scratch/test vault can no longer write into your production index.
  `--index` / `CAIRN_INDEX` still override. `cairn install` no longer pins `CAIRN_INDEX`
  (and strips a stale one); the legacy global `index.duckdb` is auto-rehomed on first run.

### Added
- `--vault` on `recall`/`recent`/`index-status`/`doctor` (the index derives from it).
- `cairn doctor` now reports `DRIFT` (with counts + remedy) when the index and vault disagree.
```

- [ ] **Step 2: README/CLAUDE**

Update any line that documents the index path as `~/.cache/agentcairn/index.duckdb` to note the vault-scoped default + `CAIRN_INDEX` override (grep: `grep -rn 'index.duckdb' README.md CLAUDE.md`).

- [ ] **Step 3: Full verify**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q`
Expected: all green (3 pre-existing skips OK).
Run: `uv run ruff check src tests && uv run ruff format --check src tests`
Expected: clean.

- [ ] **Step 4: Manual smoke (real vault, read-only)**

```bash
uvx --from 'agentcairn>=0.2' cairn doctor --vault ~/agentcairn   # expect status: OK (paths real after the earlier reindex)
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md README.md CLAUDE.md
git commit -m "docs: vault-scoped index default + doctor drift"
```

---

## Self-Review Notes (author)

- **Spec coverage:** §1 resolver → Task 1; §3 legacy rehome → Task 2; §2 command surface → Tasks 3/4 (CLI) + Task 5 (MCP); §3 install/migrator → Task 6; §3 hook scripts → Task 7; §4 doctor drift → Task 8; rollout/CHANGELOG → Task 9. No gaps.
- **Escape hatch** (`--index`/`CAIRN_INDEX` wins) is preserved in `resolve_index`/`index_for` and asserted in Tasks 1 & 5.
- **Back-compat risk:** existing tests that pass `--index`/`--ledger` keep passing (explicit wins). Tests that asserted global-index behavior or `CAIRN_INDEX` in install env are explicitly called out for update in Tasks 6 & 8.
- **Naming consistency:** `index_for(explicit, vault, env)`, `resolve_index(explicit, vault, env)`, `resolve_vault(explicit, env)`, `default_index(vault)`, `vault_key(vault)`, `default_ledger(vault)`, `judged_cache(vault)`, `migrate_legacy_index(env)`, `migrate_stale_cairn_index(path, *, fmt)` — used identically across all tasks.
