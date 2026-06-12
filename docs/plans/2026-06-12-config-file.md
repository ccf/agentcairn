# User Config File Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One TOML config file (`~/.agentcairn/config.toml`) as a lower-precedence layer under env vars, plus a `cairn config` inspect/scaffold command — so users stop managing ~11 shell exports.

**Architecture:** `config.py` gains a `KNOBS` registry and one merge seam, `cairn_env()` (file values translated to env-var names, overlaid by the real environment, cached per process). Every existing resolver defaults its `env` to `cairn_env()`; the ~12 direct `os.environ` knob reads migrate to the seam. Precedence: explicit arg > env > file > default.

**Tech Stack:** Python 3.12, stdlib `tomllib`, Typer, pytest. Spec: `docs/specs/2026-06-12-config-file-design.md`. Branch `feat/config-file` (exists, spec committed).

---

## File structure

```
src/cairn/config.py      # MODIFY: Knob registry, cairn_env(), config_file_values(), _reset()
src/cairn/usage.py       # MODIFY: enabled()/ledger_path() read cairn_env()
src/cairn/mcp/server.py  # MODIFY: resolve_config()/build_server() read cairn_env()
src/cairn/cli.py         # MODIFY: 5 os.environ sites -> cairn_env(); new `config` command
src/cairn/ingest/judge.py# MODIFY: resolve_judge default env -> cairn_env()
tests/test_config.py     # MODIFY: cairn_env tests (precedence, coercion, warnings, override)
tests/test_cli.py        # MODIFY: `cairn config` + end-to-end file-driven judge tests
README.md                # MODIFY: two-command config story
plugin/README.md         # MODIFY (if exists): LLM-judge-via-file note
CHANGELOG.md, src/cairn/__init__.py  # MODIFY: 0.9.0
```

Run everything with `uv run` from the repo root. Pre-commit runs ruff + pytest: **run `git commit` as its OWN command, then `git log --oneline -1` to confirm (ruff reformat rejects first attempts — re-add and re-commit); never pipe commit through `tail`.**

**Test hygiene rule for every task:** tests must NEVER read the developer's real `~/.agentcairn/config.toml`. Any test that touches `cairn_env()` must `monkeypatch.setenv("CAIRN_CONFIG", str(tmp_path / "config.toml"))` (pointing at a missing or test-written file) and call `cairn.config._reset()` in setup/teardown (use a small autouse fixture in `tests/test_config.py`).

---

## Task 1: `cairn_env()` + KNOBS registry

**Files:**
- Modify: `src/cairn/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Append the failing tests** to `tests/test_config.py`:

```python
import pytest

import cairn.config as cfg


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Never read the developer's real ~/.agentcairn; reset the cache around each test."""
    monkeypatch.setenv("CAIRN_CONFIG", str(tmp_path / "config.toml"))
    cfg._reset()
    yield
    cfg._reset()


def _write(tmp_path, body: str):
    p = tmp_path / "config.toml"
    p.write_text(body)
    cfg._reset()
    return p


def test_cairn_env_missing_file_is_env_only(monkeypatch):
    monkeypatch.setenv("CAIRN_JUDGE", "none")
    e = cfg.cairn_env()
    assert e["CAIRN_JUDGE"] == "none"
    assert "CAIRN_EMBED_MODEL" not in e


def test_cairn_env_file_layer_and_env_wins(tmp_path, monkeypatch):
    _write(tmp_path, 'judge = "anthropic"\nembed_model = "BAAI/bge-small-en-v1.5"\n')
    e = cfg.cairn_env()
    assert e["CAIRN_JUDGE"] == "anthropic"  # from file
    assert e["CAIRN_EMBED_MODEL"] == "BAAI/bge-small-en-v1.5"
    monkeypatch.setenv("CAIRN_JUDGE", "none")
    cfg._reset()
    assert cfg.cairn_env()["CAIRN_JUDGE"] == "none"  # env wins over file


def test_cairn_env_passthrough_keys(tmp_path):
    _write(tmp_path, 'anthropic_api_key = "sk-ant-test-12345678"\nollama_host = "http://x:1"\n')
    e = cfg.cairn_env()
    assert e["ANTHROPIC_API_KEY"] == "sk-ant-test-12345678"
    assert e["OLLAMA_HOST"] == "http://x:1"


def test_cairn_env_type_coercion(tmp_path):
    _write(tmp_path, "rerank = false\njudge_timeout = 10\nusage = true\n")
    e = cfg.cairn_env()
    assert e["CAIRN_RERANK"] == "false"  # bool -> lowercase string
    assert e["CAIRN_JUDGE_TIMEOUT"] == "10"  # int -> string
    assert e["CAIRN_USAGE"] == "true"


def test_cairn_env_unknown_key_warns_once(tmp_path, capsys):
    _write(tmp_path, 'judg_model = "typo"\n')
    cfg.cairn_env()
    cfg.cairn_env()  # second call: no second warning
    err = capsys.readouterr().err
    assert err.count("judg_model") == 1
    assert "unknown" in err.lower()


def test_cairn_env_malformed_file_degrades(tmp_path, capsys):
    _write(tmp_path, "this is = = not toml")
    e = cfg.cairn_env()
    assert "CAIRN_JUDGE" not in e  # treated as empty
    assert "config" in capsys.readouterr().err.lower()


def test_resolvers_pick_up_file(tmp_path):
    _write(tmp_path, 'judge = "none"\nrerank = false\nembed_model = "m-x"\n')
    mode, _, _ = cfg.judge_config()
    assert mode == "none"
    assert cfg.resolve_rerank(None) is False
    assert cfg.fastembed_model() == "m-x"


def test_config_file_values_exposes_file_layer(tmp_path):
    _write(tmp_path, 'judge = "anthropic"\n')
    assert cfg.config_file_values()["CAIRN_JUDGE"] == "anthropic"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_config.py -q`
Expected: FAIL — `AttributeError: module 'cairn.config' has no attribute '_reset'`.

- [ ] **Step 3: Add to `src/cairn/config.py`** — new imports at top (`import sys`, `import tomllib`, `from dataclasses import dataclass`, `from pathlib import Path`), then append:

```python
# ---------------------------------------------------------------------------
# User config file (~/.agentcairn/config.toml): a lower-precedence layer under
# env vars. Keys map MECHANICALLY to env-var names (judge_model ->
# CAIRN_JUDGE_MODEL) so the file schema can never drift from the env surface.
# Precedence everywhere: explicit arg > env var > config file > default.
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG_PATH = Path.home() / ".agentcairn" / "config.toml"
_PASSTHROUGH = {"anthropic_api_key": "ANTHROPIC_API_KEY", "ollama_host": "OLLAMA_HOST"}


@dataclass(frozen=True)
class Knob:
    key: str  # config-file key
    env: str  # env-var name
    default: str  # human-readable default (for docs/template)
    description: str
    secret: bool = False


KNOBS: tuple[Knob, ...] = (
    Knob("vault", "CAIRN_VAULT", "~/agentcairn", "Vault directory (the source of truth)."),
    Knob("index", "CAIRN_INDEX", "~/.cache/agentcairn/index.duckdb", "DuckDB index path (rebuildable cache)."),
    Knob("embedder", "CAIRN_EMBEDDER", "fastembed", "Embedding provider: fastembed | ollama | fake."),
    Knob("embed_model", "CAIRN_EMBED_MODEL", "nomic-ai/nomic-embed-text-v1.5", "Embedding model name."),
    Knob("rerank", "CAIRN_RERANK", "true", "Cross-encoder reranker on recall (biggest quality lever)."),
    Knob("usage", "CAIRN_USAGE", "1", "Token-savings ledger (local, no telemetry)."),
    Knob("usage_path", "CAIRN_USAGE_PATH", "~/.cache/agentcairn/usage.jsonl", "Savings ledger path."),
    Knob("judge", "CAIRN_JUDGE", "embedding", "Memory durability judge: anthropic | embedding | none."),
    Knob("judge_model", "CAIRN_JUDGE_MODEL", "claude-haiku-4-5", "Model for the LLM judge tier."),
    Knob("judge_timeout", "CAIRN_JUDGE_TIMEOUT", "10", "LLM judge timeout (seconds)."),
    Knob("ollama_host", "OLLAMA_HOST", "http://localhost:11434", "Ollama server (ollama embedder)."),
    Knob("anthropic_api_key", "ANTHROPIC_API_KEY", "", "API key for the LLM judge tier.", secret=True),
)
_KNOWN_KEYS = {k.key for k in KNOBS}

_file_cache: dict[str, str] | None = None
_warned_keys: set[str] = set()


def _reset() -> None:
    """Clear the config-file cache (tests; also after `cairn config --init`)."""
    global _file_cache
    _file_cache = None
    _warned_keys.clear()


def _config_path() -> Path:
    return Path(os.environ.get("CAIRN_CONFIG") or _DEFAULT_CONFIG_PATH).expanduser()


def _translate(key: str) -> str:
    return _PASSTHROUGH.get(key, f"CAIRN_{key.upper()}")


def _coerce(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def config_file_values() -> dict[str, str]:
    """The config file's values, translated to env-var names. Cached per process.
    Missing/malformed/unreadable file -> {} (config must never break a run)."""
    global _file_cache
    if _file_cache is not None:
        return _file_cache
    path = _config_path()
    values: dict[str, str] = {}
    try:
        if path.exists():
            data = tomllib.loads(path.read_text(encoding="utf-8"))
            for key, raw in data.items():
                if key not in _KNOWN_KEYS and key not in _warned_keys:
                    _warned_keys.add(key)
                    print(f"agentcairn: unknown config key {key!r} in {path}", file=sys.stderr)
                values[_translate(key)] = _coerce(raw)
    except Exception as e:  # malformed TOML, unreadable file, ...
        if "file" not in _warned_keys:
            _warned_keys.add("file")
            print(f"agentcairn: ignoring config file {path}: {e}", file=sys.stderr)
        values = {}
    _file_cache = values
    return values


def cairn_env(env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    """The unified settings mapping: config-file values overlaid by the (real or
    given) environment. THE seam for every knob read: arg > env > file > default."""
    base = dict(config_file_values())
    base.update(os.environ if env is None else env)
    return base
```

Then change the FOUR existing resolvers' `if env is None: env = os.environ` lines to `if env is None: env = cairn_env()` (in `fastembed_model`, `ollama_config`, `resolve_rerank`, `judge_config`), and update the module docstring's precedence note to "explicit-arg → environment → config file (~/.agentcairn/config.toml) → default".

- [ ] **Step 4: Run + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_config.py -q` → all pass. Then `uv run pytest -q` → all pass.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/config.py tests/test_config.py
```
```bash
cd /Users/ccf/git/agentcairn && git commit -m "feat(config): cairn_env() — config-file layer under env vars + KNOBS registry"
```
Confirm with `git log --oneline -1`.

---

## Task 2: Migrate the direct `os.environ` knob reads

**Files:**
- Modify: `src/cairn/usage.py`, `src/cairn/mcp/server.py`, `src/cairn/cli.py`, `src/cairn/ingest/judge.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Append the end-to-end failing test** to `tests/test_cli.py`:

```python
def test_config_file_drives_judge_tier(tmp_path, monkeypatch):
    """End-to-end: judge = "none" in the config file changes the ingest tier
    with NO env var set (the whole point of the file)."""
    import json as _j

    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text('judge = "none"\n')
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.delenv("CAIRN_JUDGE", raising=False)
    cfg._reset()
    proj = tmp_path / "projects" / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "t.jsonl").write_text(
        _j.dumps(
            {
                "type": "user",
                "sessionId": "s",
                "cwd": "/Users/x/proj",
                "message": {"role": "user", "content": "we decided to always rebase-merge"},
            }
        )
        + "\n"
    )
    r = runner.invoke(
        app,
        ["ingest", "--vault", str(tmp_path / "vault"), "--transcripts-dir",
         str(tmp_path / "projects"), "--ledger", str(tmp_path / "led.sha256")],
    )
    cfg._reset()
    assert r.exit_code == 0, r.output
    assert "judge: none" in r.output
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k config_file_drives -q`
Expected: FAIL — the run uses the embedding tier ("judge: embedding"), because the CLI reads `os.environ`, not the file.

- [ ] **Step 3: Migrate each site** (exact replacements):

(a) `src/cairn/usage.py` — add `from cairn.config import cairn_env` to imports, then:
```python
def enabled() -> bool:
    """Usage tracking is on unless CAIRN_USAGE=0."""
    return cairn_env().get("CAIRN_USAGE", "1") != "0"


def ledger_path() -> Path:
    """$CAIRN_USAGE_PATH (env or config file) else ~/.cache/agentcairn/usage.jsonl."""
    env = cairn_env().get("CAIRN_USAGE_PATH")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "agentcairn" / "usage.jsonl"
```

(b) `src/cairn/mcp/server.py` — add `from cairn.config import cairn_env` to imports; in `resolve_config` replace the three `os.environ.get(...)` calls:
```python
    settings = cairn_env()
    resolved_vault = vault or settings.get("CAIRN_VAULT")
    resolved_index = index or settings.get("CAIRN_INDEX") or _DEFAULT_INDEX
    resolved_embedder = embedder or settings.get("CAIRN_EMBEDDER") or _DEFAULT_EMBEDDER
```
and in `build_server` change `rerank_default = resolve_rerank(None, os.environ)` to `rerank_default = resolve_rerank(None)` (the resolver now defaults to `cairn_env()`).

(c) `src/cairn/cli.py` — add `cairn_env` to the existing `from cairn.config import ...` import (check what's imported; add it), then:
- `_default_index`: `env = os.environ.get("CAIRN_INDEX")` → `env = cairn_env().get("CAIRN_INDEX")`
- `init`: `target = path or Path(os.environ.get("CAIRN_VAULT") or (Path.home() / "agentcairn"))` → `target = path or Path(cairn_env().get("CAIRN_VAULT") or (Path.home() / "agentcairn"))`
- `warm`: `embedder = os.environ.get("CAIRN_EMBEDDER") or "fastembed"` → `embedder = cairn_env().get("CAIRN_EMBEDDER") or "fastembed"`
- `_warn_if_llm_tier_unavailable`: `if os.environ.get("CAIRN_JUDGE") == "anthropic"` → `if cairn_env().get("CAIRN_JUDGE") == "anthropic"`
- `ingest` dry-run block: `env = dict(os.environ)` → `env = dict(cairn_env())`

(d) `src/cairn/ingest/judge.py` — in `resolve_judge`, replace `e = env if env is not None else dict(_os.environ)` with:
```python
    from cairn.config import cairn_env

    e = env if env is not None else dict(cairn_env())
```
(remove the now-unused `import os as _os` if nothing else uses it in that function).

- [ ] **Step 4: Run + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → ALL pass (the new end-to-end test AND every existing test — existing CLI tests set env vars, which still win over the [missing] file).
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/usage.py src/cairn/mcp/server.py src/cairn/cli.py src/cairn/ingest/judge.py tests/test_cli.py
```
```bash
cd /Users/ccf/git/agentcairn && git commit -m "feat(config): route every knob read through cairn_env()"
```
Confirm with `git log --oneline -1`.

---

## Task 3: `cairn config` command (inspect + --init)

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Append the failing tests** to `tests/test_cli.py`:

```python
def test_config_inspect_shows_sources(tmp_path, monkeypatch):
    import cairn.config as cfg

    conf = tmp_path / "config.toml"
    conf.write_text('judge = "anthropic"\nanthropic_api_key = "sk-ant-test-abcdef12345678"\n')
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    monkeypatch.setenv("CAIRN_EMBEDDER", "fake")
    monkeypatch.delenv("CAIRN_JUDGE", raising=False)
    cfg._reset()
    r = runner.invoke(app, ["config"])
    cfg._reset()
    assert r.exit_code == 0, r.output
    out = r.output
    assert "judge" in out and "anthropic" in out and "file" in out  # file-sourced
    assert "embedder" in out and "fake" in out and "env" in out  # env-sourced
    assert "default" in out  # untouched knobs
    assert "sk-ant-test-abcdef12345678" not in out  # secret masked
    assert "5678" in out  # ...but last4 shown


def test_config_init_scaffolds_template(tmp_path, monkeypatch):
    import cairn.config as cfg

    conf = tmp_path / "sub" / "config.toml"  # parent must be created
    monkeypatch.setenv("CAIRN_CONFIG", str(conf))
    cfg._reset()
    r = runner.invoke(app, ["config", "--init"])
    cfg._reset()
    assert r.exit_code == 0, r.output
    assert conf.exists()
    assert (conf.stat().st_mode & 0o777) == 0o600  # key may live here
    body = conf.read_text()
    assert '# judge = "embedding"' in body  # every knob present, commented out
    assert "# anthropic_api_key" in body
    # refuses overwrite
    r2 = runner.invoke(app, ["config", "--init"])
    assert r2.exit_code == 0
    assert "exists" in r2.output.lower()
```

- [ ] **Step 2: Run to verify failure**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k 'config_inspect or config_init' -q`
Expected: FAIL — `No such command 'config'`.

- [ ] **Step 3: Add the command** to `src/cairn/cli.py` (place after the `install` command; imports: `from cairn.config import KNOBS, config_file_values, _config_path` — extend the existing import; also `import cairn.config as _cfg` for `_reset`):

```python
@app.command()
def config(
    init: bool = typer.Option(False, "--init", help="Write a commented template config file."),
) -> None:
    """Show every setting's effective value and source (env / file / default),
    or scaffold ~/.agentcairn/config.toml with --init."""
    from cairn.config import KNOBS, _config_path, config_file_values

    path = _config_path()
    if init:
        if path.exists():
            typer.echo(f"config file already exists: {path}")
            return
        lines = [
            "# agentcairn configuration — env vars override these values.",
            "# Uncomment a line to set it. Docs: https://github.com/ccf/agentcairn",
            "",
        ]
        for k in KNOBS:
            lines.append(f"# {k.description}")
            lines.append(f'# {k.key} = "{k.default}"')
            lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        path.chmod(0o600)  # the API key may live here
        import cairn.config as _cfg

        _cfg._reset()
        typer.echo(f"wrote {path} (mode 0600) — uncomment lines to configure")
        return

    file_vals = config_file_values()
    typer.echo(f"config file: {path}{'' if path.exists() else ' (not present)'}")
    for k in KNOBS:
        if k.env in os.environ:
            value, source = os.environ[k.env], "env"
        elif k.env in file_vals:
            value, source = file_vals[k.env], "file"
        else:
            value, source = k.default, "default"
        if k.secret and value:
            value = f"{value[:7]}…{value[-4:]}" if len(value) > 11 else "…set…"
        typer.echo(f"  {k.key:18} = {value:42} [{source}]")
```

- [ ] **Step 4: Run + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest tests/test_cli.py -k config -q` → pass. Full: `uv run pytest -q` → all pass.
```bash
cd /Users/ccf/git/agentcairn && git add src/cairn/cli.py tests/test_cli.py
```
```bash
cd /Users/ccf/git/agentcairn && git commit -m "feat(cli): cairn config — source-labeled inspection + --init template"
```
Confirm with `git log --oneline -1`.

---

## Task 4: Docs + 0.9.0

**Files:**
- Modify: `README.md`, `plugin/README.md` (if present, else skip), `CHANGELOG.md`, `src/cairn/__init__.py`

- [ ] **Step 1: README** — in the "Using it directly" section, after the CLI code block, add:

```markdown
### Configuration

All settings live in one file — `~/.agentcairn/config.toml` — with env vars as overrides (precedence: CLI flag > env var > config file > default):

​```bash
cairn config --init   # scaffold a fully-commented template (chmod 600)
cairn config          # show every setting's effective value and where it came from
​```

For example, enabling the LLM memory judge is two uncommented lines — no shell exports needed (the plugin's background sweep reads the file directly):

​```toml
judge = "anthropic"
anthropic_api_key = "sk-ant-..."
​```
```

(Strip the zero-width characters around the inner code fences when writing — they're only here to nest fences in this plan.)

- [ ] **Step 2: plugin docs** — if `plugin/README.md` exists, add a short "Enabling the LLM judge" note pointing at `cairn config --init`. If it doesn't exist, skip.

- [ ] **Step 3: CHANGELOG** — insert under `## [Unreleased]` (current top section is `## [0.8.0] - 2026-06-12`):

```markdown
## [0.9.0] - 2026-06-12

### Added
- **User config file: `~/.agentcairn/config.toml`.** Every setting can now live in one TOML file instead of shell exports; env vars override file values (precedence: CLI flag > env > file > default). Keys map mechanically to env names (`judge_model` → `CAIRN_JUDGE_MODEL`; `anthropic_api_key` and `ollama_host` pass through), so the file schema can never drift from the env surface. New `cairn config` shows every setting's effective value and source (secrets masked); `cairn config --init` scaffolds a fully-commented template (mode 0600). The plugin's detached SessionEnd sweep reads the file directly — enabling the LLM judge no longer requires any shell-profile exports.
```

Update link refs: `[Unreleased]` → `v0.9.0...HEAD`, add `[0.9.0]: https://github.com/ccf/agentcairn/compare/v0.8.0...v0.9.0` above `[0.8.0]`.

- [ ] **Step 4: Bump** `src/cairn/__init__.py` → `__version__ = "0.9.0"`.

- [ ] **Step 5: Run + commit**

Run: `cd /Users/ccf/git/agentcairn && uv run pytest -q` → all pass.
```bash
cd /Users/ccf/git/agentcairn && git add README.md plugin/README.md CHANGELOG.md src/cairn/__init__.py
```
```bash
cd /Users/ccf/git/agentcairn && git commit -m "chore(release): 0.9.0 — user config file"
```
Confirm with `git log --oneline -1`.

---

## Self-review (against the spec)

- **§ Location/`CAIRN_CONFIG` override**: `_config_path()` (Task 1). ✓
- **§ Mechanical mapping + passthrough**: `_translate`/`_PASSTHROUGH` (Task 1). ✓
- **§ Precedence arg > env > file > default**: `cairn_env` overlay + resolver defaults (Tasks 1–2); end-to-end test (Task 2). ✓
- **§ Type coercion / unknown-key warn / malformed degrade**: Task 1 code + tests. ✓
- **§ Call-site migration (cli, usage, mcp, judge)**: Task 2, exact replacements. ✓
- **§ `cairn config` + `--init` (0600, commented, refuse-overwrite, masked, sources)**: Task 3. ✓
- **§ KNOBS registry as single docs source**: Task 1 registry consumed by Task 3 template + inspection. ✓
- **§ Docs two-command story**: Task 4. ✓
- **§ 0.9.0 backward-compatible**: Task 4; missing file = `{}` layer. ✓
- **§ Out of scope** (XDG, per-project, set/get, encryption, install-writes-file): none added. ✓

**Type/name consistency:** `Knob{key,env,default,description,secret}`, `KNOBS`, `cairn_env(env=None)->Mapping`, `config_file_values()->dict`, `_config_path()->Path`, `_reset()`, `_translate`, `_coerce` — used identically across tasks. No placeholders.
