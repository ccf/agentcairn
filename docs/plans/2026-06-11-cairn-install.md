# `cairn install` (per-host MCP setup) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cairn install <host>` — write/merge the agentcairn MCP server into Cursor, Claude Desktop, Windsurf, Gemini CLI (JSON `mcpServers`) and Codex (TOML), non-destructively and idempotently.

**Architecture:** A focused `src/cairn/hosts/` package — a host **registry** + a canonical **entry builder** + two **writers** (one JSON, one Codex-TOML) behind a `write_host` dispatcher. `cli.py` gets a thin `install` command. Writers back up the file, merge only the `agentcairn` server (preserving everything else), and support a `dry` preview.

**Tech Stack:** Python 3.12 + Typer (CLI), stdlib `json`, **`tomlkit`** (new dep — round-trips TOML comments/formatting for Codex), pytest. Spec: `docs/specs/2026-06-11-cairn-install-design.md`.

---

## File structure

```
src/cairn/hosts/__init__.py   # CREATE: Host dataclass + HOSTS registry + get_host/detected_hosts
src/cairn/hosts/entry.py      # CREATE: mcp_entry(vault, index) -> dict (the canonical MCP entry)
src/cairn/hosts/writers.py    # CREATE: write_json_mcp, write_codex_toml, write_host dispatcher
src/cairn/cli.py              # MODIFY: add the `install` command
pyproject.toml                # MODIFY: add tomlkit dependency
README.md                     # MODIFY: "Use it in any MCP host" section
tests/test_hosts.py           # CREATE: registry + entry + writer unit tests
tests/test_cli.py             # MODIFY: `cairn install` command tests
```

Run all Python from the repo root with `uv run`. Commit after each task.

---

## Task 1: Host registry + canonical MCP entry

**Files:**
- Create: `src/cairn/hosts/__init__.py`, `src/cairn/hosts/entry.py`
- Test: `tests/test_hosts.py`

- [ ] **Step 1: Write the failing tests** — create `tests/test_hosts.py`:

```python
# SPDX-License-Identifier: Apache-2.0
from cairn.hosts import detected_hosts, get_host
from cairn.hosts.entry import mcp_entry


def test_mcp_entry_shape():
    e = mcp_entry("/home/u/agentcairn", "/home/u/.cache/agentcairn/index.duckdb")
    assert e == {
        "command": "uvx",
        "args": ["agentcairn"],
        "env": {
            "CAIRN_VAULT": "/home/u/agentcairn",
            "CAIRN_INDEX": "/home/u/.cache/agentcairn/index.duckdb",
        },
    }


def test_get_host_known_and_unknown():
    assert get_host("cursor").format == "mcpServers"
    assert get_host("codex").format == "codex-toml"
    assert get_host("nope") is None


def test_detected_hosts_uses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Nothing present yet → none detected.
    assert detected_hosts() == []
    # Create Cursor's config dir → it's detected.
    (tmp_path / ".cursor").mkdir()
    ids = {h.id for h in detected_hosts()}
    assert "cursor" in ids
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hosts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cairn.hosts'`.

- [ ] **Step 3: Create `src/cairn/hosts/entry.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""The canonical agentcairn MCP server entry, shared by every host writer and --print."""
from __future__ import annotations


def mcp_entry(vault: str, index: str) -> dict:
    """The MCP server config agentcairn writes into a host: `uvx agentcairn` with
    CAIRN_VAULT/CAIRN_INDEX. `vault`/`index` should already be absolute paths."""
    return {
        "command": "uvx",
        "args": ["agentcairn"],
        "env": {"CAIRN_VAULT": vault, "CAIRN_INDEX": index},
    }
```

- [ ] **Step 4: Create `src/cairn/hosts/__init__.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""Registry of MCP hosts `cairn install` can configure."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Host:
    id: str
    label: str
    format: str  # "mcpServers" (JSON) | "codex-toml"
    path_template: str  # may start with ~ ; expanded by config_path()

    def config_path(self) -> Path:
        return Path(self.path_template).expanduser()


def _claude_desktop_path() -> str:
    if sys.platform == "darwin":
        return "~/Library/Application Support/Claude/claude_desktop_config.json"
    if sys.platform.startswith("win"):
        return "~/AppData/Roaming/Claude/claude_desktop_config.json"
    return "~/.config/Claude/claude_desktop_config.json"


HOSTS: list[Host] = [
    Host("cursor", "Cursor", "mcpServers", "~/.cursor/mcp.json"),
    Host("claude-desktop", "Claude Desktop", "mcpServers", _claude_desktop_path()),
    Host("windsurf", "Windsurf", "mcpServers", "~/.codeium/windsurf/mcp_config.json"),
    Host("gemini", "Gemini CLI", "mcpServers", "~/.gemini/settings.json"),
    Host("codex", "Codex CLI", "codex-toml", "~/.codex/config.toml"),
]

_BY_ID = {h.id: h for h in HOSTS}


def get_host(host_id: str) -> Host | None:
    return _BY_ID.get(host_id)


def detected_hosts() -> list[Host]:
    """Hosts whose config directory exists (the tool appears installed)."""
    return [h for h in HOSTS if h.config_path().parent.is_dir()]
```

- [ ] **Step 5: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hosts.py -q`
Expected: 3 passed.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/hosts/__init__.py src/cairn/hosts/entry.py tests/test_hosts.py && git commit -m "feat(hosts): MCP host registry + canonical mcp_entry"
```

---

## Task 2: JSON `mcpServers` writer + dispatcher

**Files:**
- Create: `src/cairn/hosts/writers.py`
- Test: `tests/test_hosts.py`

- [ ] **Step 1: Append the failing tests** to `tests/test_hosts.py`:

```python
import json as _json

from cairn.hosts import get_host
from cairn.hosts.writers import write_host, write_json_mcp

_ENTRY = mcp_entry("/v", "/i")


def test_json_writer_creates_and_writes(tmp_path):
    p = tmp_path / "sub" / "mcp.json"  # parent absent → must be created
    summary = write_json_mcp(p, _ENTRY)
    data = _json.loads(p.read_text())
    assert data["mcpServers"]["agentcairn"] == _ENTRY
    assert str(p) in summary


def test_json_writer_preserves_other_servers_and_keys(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text(_json.dumps({"theme": "dark", "mcpServers": {"other": {"command": "x"}}}))
    write_json_mcp(p, _ENTRY)
    data = _json.loads(p.read_text())
    assert data["theme"] == "dark"  # unrelated key survives
    assert data["mcpServers"]["other"] == {"command": "x"}  # other server survives
    assert data["mcpServers"]["agentcairn"] == _ENTRY
    assert (p.with_name("mcp.json.bak")).exists()  # backed up


def test_json_writer_idempotent(tmp_path):
    p = tmp_path / "mcp.json"
    write_json_mcp(p, _ENTRY)
    write_json_mcp(p, _ENTRY)
    data = _json.loads(p.read_text())
    assert list(data["mcpServers"]).count("agentcairn") == 1


def test_json_writer_dry_writes_nothing(tmp_path):
    p = tmp_path / "mcp.json"
    out = write_json_mcp(p, _ENTRY, dry=True)
    assert not p.exists()
    assert "agentcairn" in out and "uvx" in out


def test_json_writer_rejects_malformed_without_clobber(tmp_path):
    p = tmp_path / "mcp.json"
    p.write_text("{ not json")
    import pytest

    with pytest.raises(ValueError):
        write_json_mcp(p, _ENTRY)
    assert p.read_text() == "{ not json"  # original untouched


def test_write_host_dispatches_json(tmp_path):
    p = tmp_path / "mcp.json"
    h = get_host("cursor")
    # point the host at our temp path via monkeypatchless override: call writer directly
    write_json_mcp(p, _ENTRY)
    assert _json.loads(p.read_text())["mcpServers"]["agentcairn"]["command"] == "uvx"
    assert h.format == "mcpServers"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hosts.py -k 'json or dispatch' -v`
Expected: FAIL — `cannot import name 'write_json_mcp'`.

- [ ] **Step 3: Create `src/cairn/hosts/writers.py`**

```python
# SPDX-License-Identifier: Apache-2.0
"""Merge the agentcairn MCP entry into a host config — non-destructive, idempotent,
backup-first. With dry=True, render the would-be file content and write nothing."""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from cairn.hosts import Host


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(path.name + ".bak"))


def write_json_mcp(path: Path, entry: dict, *, dry: bool = False) -> str:
    """Set mcpServers['agentcairn'] = entry in a JSON config, preserving all other
    content. Returns the rendered content (dry) or a write summary."""
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text() or "{}")
        except json.JSONDecodeError as e:
            raise ValueError(f"{path} is not valid JSON ({e}); fix it or use --print") from e
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not a JSON object; fix it or use --print")
    servers = data.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise ValueError(f"{path}: 'mcpServers' is not an object; fix it or use --print")
    servers["agentcairn"] = entry
    rendered = json.dumps(data, indent=2) + "\n"
    if dry:
        return rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(rendered)
    return f"wrote agentcairn → {path}"


def write_host(host: Host, entry: dict, *, dry: bool = False) -> str:
    """Dispatch to the right writer for the host's config format."""
    if host.format == "mcpServers":
        return write_json_mcp(host.config_path(), entry, dry=dry)
    if host.format == "codex-toml":
        return write_codex_toml(host.config_path(), entry, dry=dry)
    raise ValueError(f"unknown host format: {host.format!r}")
```

(`write_codex_toml` is added in Task 3; `write_host` references it — Task 2's tests don't call the codex path, and Python resolves the name at call time, so the module imports fine. To be safe, define a stub now and replace in Task 3 — OR implement Task 3 immediately after. The spec sequences Task 3 next.)

To avoid a NameError if `write_host` is ever called for codex before Task 3, add a temporary stub at the end of `writers.py` (Task 3 replaces it):

```python
def write_codex_toml(path: Path, entry: dict, *, dry: bool = False) -> str:  # replaced in Task 3
    raise NotImplementedError("codex writer lands in Task 3")
```

- [ ] **Step 4: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hosts.py -q`
Expected: all pass (Task 1 + Task 2 tests).
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/hosts/writers.py tests/test_hosts.py && git commit -m "feat(hosts): non-destructive JSON mcpServers writer + dispatcher"
```

---

## Task 3: Codex TOML writer (tomlkit)

**Files:**
- Modify: `pyproject.toml` (add `tomlkit`), `src/cairn/hosts/writers.py`
- Test: `tests/test_hosts.py`

- [ ] **Step 1: Add the dependency**

Run: `cd /Users/ccf/git/agentcairn && uv add tomlkit`
Expected: adds `tomlkit` to `[project.dependencies]` in `pyproject.toml` and updates `uv.lock`.

- [ ] **Step 2: Append the failing tests** to `tests/test_hosts.py`:

```python
def test_codex_writer_adds_tables_and_preserves(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    p.write_text(
        "# my codex config\n"
        'model = "gpt-5"\n\n'
        "[mcp_servers.other]\n"
        'command = "npx"\n'
    )
    write_codex_toml(p, _ENTRY)
    text = p.read_text()
    assert "# my codex config" in text  # comment preserved
    assert 'model = "gpt-5"' in text  # other key preserved
    assert "[mcp_servers.other]" in text  # other server preserved
    # agentcairn tables present + re-parseable
    import tomllib

    doc = tomllib.loads(text)
    ac = doc["mcp_servers"]["agentcairn"]
    assert ac["command"] == "uvx"
    assert ac["args"] == ["agentcairn"]
    assert ac["env"]["CAIRN_VAULT"] == "/v"
    assert p.with_name("config.toml.bak").exists()


def test_codex_writer_idempotent(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    write_codex_toml(p, _ENTRY)
    write_codex_toml(p, _ENTRY)
    import tomllib

    doc = tomllib.loads(p.read_text())
    assert doc["mcp_servers"]["agentcairn"]["command"] == "uvx"


def test_codex_writer_dry_writes_nothing(tmp_path):
    from cairn.hosts.writers import write_codex_toml

    p = tmp_path / "config.toml"
    out = write_codex_toml(p, _ENTRY, dry=True)
    assert not p.exists()
    assert "[mcp_servers.agentcairn]" in out
```

- [ ] **Step 3: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hosts.py -k codex -v`
Expected: FAIL — the stub raises `NotImplementedError`.

- [ ] **Step 4: Replace the `write_codex_toml` stub** in `src/cairn/hosts/writers.py` with the real implementation, and add `import tomlkit` at the top of the file:

```python
def write_codex_toml(path: Path, entry: dict, *, dry: bool = False) -> str:
    """Set [mcp_servers.agentcairn] (+ .env) in a Codex TOML config, preserving all
    other tables and comments (tomlkit round-trips)."""
    doc = tomlkit.document()
    if path.exists():
        try:
            doc = tomlkit.parse(path.read_text())
        except Exception as e:  # tomlkit raises ParseError/ValueError variants
            raise ValueError(f"{path} is not valid TOML ({e}); fix it or use --print") from e
    servers = doc.get("mcp_servers")
    if servers is None:
        servers = tomlkit.table(is_super_table=True)
        doc["mcp_servers"] = servers
    ac = tomlkit.table()
    ac["command"] = entry["command"]
    ac["args"] = entry["args"]
    env = tomlkit.table()
    for k, v in entry["env"].items():
        env[k] = v
    ac["env"] = env
    servers["agentcairn"] = ac
    rendered = tomlkit.dumps(doc)
    if dry:
        return rendered
    path.parent.mkdir(parents=True, exist_ok=True)
    _backup(path)
    path.write_text(rendered)
    return f"wrote [mcp_servers.agentcairn] → {path}"
```

- [ ] **Step 5: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_hosts.py -q`
Expected: all pass.
```bash
cd /Users/ccf/git/agentcairn && git add pyproject.toml uv.lock src/cairn/hosts/writers.py tests/test_hosts.py && git commit -m "feat(hosts): Codex TOML writer (tomlkit, comment-preserving)"
```

---

## Task 4: `cairn install` command

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Append the failing tests** to `tests/test_cli.py`:

```python
def test_install_cursor_writes_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--vault", str(tmp_path / "v")])
    assert r.exit_code == 0, r.output
    import json as _j

    data = _j.loads((tmp_path / ".cursor" / "mcp.json").read_text())
    ac = data["mcpServers"]["agentcairn"]
    assert ac["command"] == "uvx" and ac["args"] == ["agentcairn"]
    assert ac["env"]["CAIRN_VAULT"] == str((tmp_path / "v").resolve())  # absolute


def test_install_print_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install", "cursor", "--print"])
    assert r.exit_code == 0, r.output
    assert "agentcairn" in r.output
    assert not (tmp_path / ".cursor" / "mcp.json").exists()


def test_install_no_arg_previews(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".cursor").mkdir()
    r = runner.invoke(app, ["install"])
    assert r.exit_code == 0, r.output
    assert "cursor" in r.output.lower()
    assert not (tmp_path / ".cursor" / "mcp.json").exists()  # preview only


def test_install_unknown_host_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    r = runner.invoke(app, ["install", "nope"])
    assert r.exit_code == 1
    assert "unknown host" in r.output.lower()
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k install -v`
Expected: FAIL — `No such command 'install'`.

- [ ] **Step 3: Add the `install` command** to `src/cairn/cli.py` (after `warm`):

```python
@app.command()
def install(
    host: str = typer.Argument(
        None, help="Host id: cursor / claude-desktop / windsurf / gemini / codex."
    ),
    all_hosts: bool = typer.Option(False, "--all", help="Configure every detected host."),
    print_only: bool = typer.Option(False, "--print", help="Print the config; write nothing."),
    vault: Path = typer.Option(None, "--vault", help="Vault path (default ~/agentcairn)."),
    index: Path = typer.Option(
        None, "--index", help="Index path (default ~/.cache/agentcairn/index.duckdb)."
    ),
) -> None:
    """Wire the agentcairn MCP server into another MCP host (Cursor, Codex, …)."""
    from cairn.hosts import HOSTS, detected_hosts, get_host
    from cairn.hosts.entry import mcp_entry
    from cairn.hosts.writers import write_host

    v = str((vault or (Path.home() / "agentcairn")).expanduser().resolve())
    idx = str(
        (index or (Path.home() / ".cache" / "agentcairn" / "index.duckdb")).expanduser().resolve()
    )
    entry = mcp_entry(v, idx)
    ids = ", ".join(h.id for h in HOSTS)

    if host is None and not all_hosts:  # detect + preview, write nothing
        present = detected_hosts()
        if not present:
            typer.echo(f"No supported MCP hosts detected. Supported: {ids}")
            return
        typer.echo("Detected hosts — run `cairn install <id>` (or `--all`):")
        for h in present:
            typer.echo(f"  {h.id:15} {h.label}  → {h.config_path()}")
        return

    if all_hosts:
        targets = detected_hosts()
    else:
        h = get_host(host)
        if h is None:
            typer.echo(f"unknown host '{host}'. Supported: {ids}")
            raise typer.Exit(1)
        targets = [h]

    failures = 0
    for h in targets:
        try:
            out = write_host(h, entry, dry=print_only)
            typer.echo(out if print_only else f"✓ {h.label}: {out}")
        except Exception as e:  # best-effort per host; continue under --all
            failures += 1
            typer.echo(f"✗ {h.label}: {e}")
    if failures:
        raise typer.Exit(1)
```

- [ ] **Step 4: Run tests + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k install -v`
Expected: 4 passed.
Regression: `cd /Users/ccf/git/agentcairn && uv run pytest -q`.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/cli.py tests/test_cli.py && git commit -m "feat(cli): add 'cairn install' — wire the MCP server into Cursor/Codex/etc."
```

---

## Task 5: README — "Use it in any MCP host"

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add the section** — in `README.md`, right after the `## Using it directly` section, insert:

```markdown
## Use it in any MCP host

The MCP server is portable — `cairn install` wires it into other hosts (the vault stays your single `~/agentcairn`):

```bash
cairn install                 # detect installed hosts + preview (writes nothing)
cairn install cursor          # configure one host
cairn install --all           # configure every detected host
cairn install codex --print   # just print the snippet, change nothing
```

Supported: **Cursor**, **Claude Desktop**, **Windsurf**, **Gemini CLI** (JSON `mcpServers`) and **Codex** (TOML). Writes are non-destructive (your other servers are preserved) and backed up to `<config>.bak`. Ambient memory (recall-at-start, capture-at-end) is Claude-Code-only for now — cross-host capture is tracked in [#36](https://github.com/ccf/agentcairn/issues/36).
```

- [ ] **Step 2: Commit**

```bash
cd /Users/ccf/git/agentcairn && git add README.md && git commit -m "docs: 'use it in any MCP host' (cairn install)"
```

---

## Self-review (against the spec)

- **§ CLI surface** (`<host>` / `--all` / no-arg preview / `--print` / `--vault` / `--index`, absolute-ized): Task 4. ✓
- **§ What gets written** (`uvx agentcairn` + CAIRN_VAULT/CAIRN_INDEX; TOML equivalent): `mcp_entry` (Task 1) + writers (Tasks 2/3). ✓
- **§ Merge semantics** (non-destructive, idempotent, backup-first, malformed→error-without-clobber): Tasks 2/3 + their tests. ✓
- **§ Architecture** (`hosts/` registry + entry + writers + dispatcher; thin CLI): Tasks 1–4. ✓
- **§ Scope** (5 hosts; one JSON writer for four + Codex TOML): registry (Task 1) + writers (2/3). ✓
- **§ tomlkit dep**: Task 3 Step 1. ✓
- **§ Testing** (preserve / idempotent / dry-writes-nothing / malformed / backup / parent-dir / absolute paths / TOML round-trip + comments): Tasks 1–4 tests. ✓
- **§ Docs**: Task 5. ✓
- **§ Out of scope** (Zed, ambient capture, uninstall): none added. ✓

**Type/name consistency:** `Host{id,label,format,path_template}.config_path()`; `mcp_entry(vault,index)->dict`; `write_json_mcp(path,entry,*,dry)`, `write_codex_toml(path,entry,*,dry)`, `write_host(host,entry,*,dry)`; `get_host(id)`, `detected_hosts()`, `HOSTS`. Used identically across tasks. The Task-2 codex stub is explicitly replaced in Task 3. No placeholders.
```
