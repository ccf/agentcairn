# `cairn schedule` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `cairn schedule install/uninstall/status` — a per-OS scheduler (launchd on macOS, user crontab on Linux) that periodically runs `cairn sweep`, as the host-agnostic capture backstop.

**Architecture:** A new `src/cairn/schedule.py` splits into **pure render functions** (plist XML, cron line, interval parsing — unit-testable on any OS) and a **side-effecting layer** (a `_run` subprocess wrapper + per-OS install/uninstall/status, with `_run` and HOME monkeypatched in tests). A `schedule` Typer sub-app in `cli.py` exposes it; `cairn install` prints a one-line hint.

**Tech Stack:** Python, Typer, `subprocess`, `launchctl` (macOS) / `crontab` (Linux). No new dependencies.

## Global Constraints

- Scheduled job is always: `<abs cairn> sweep --vault <abs vault>` logging to `~/.cache/agentcairn/sweep.log`.
- launchd Label / cron marker: `dev.agentcairn.sweep` / `# agentcairn-sweep`.
- Minimum interval **5 minutes**. Cron can't express `*/N` for `N>=60`: `<60` → `*/N * * * *`; whole-hour multiples → `0 */H * * *`; other `>=60` rejected.
- Opt-in (never auto-install); idempotent (install replaces, never duplicates); `--print` writes nothing.
- Use `cairn.paths.cache_root()` (→ `~/.cache/agentcairn`) and `cairn.paths.resolve_vault(explicit)` for paths. CLI output via `typer.echo`.
- XML-escape paths in the plist; `shlex.quote` paths in the cron line (vaults may contain spaces).

---

## Task 1: Pure helpers — interval parsing + renderers

**Files:**
- Create: `src/cairn/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Produces: `parse_interval(s: str) -> int` (minutes); `render_plist(cairn: str, vault: str, interval_min: int, log: str) -> str`; `render_cron_line(cairn: str, vault: str, interval_min: int, log: str) -> str`; constants `PLIST_LABEL="dev.agentcairn.sweep"`, `CRON_MARKER="# agentcairn-sweep"`.

- [ ] **Step 1: Write the failing tests**

`tests/test_schedule.py`:
```python
# SPDX-License-Identifier: Apache-2.0
import pytest

from cairn import schedule


def test_parse_interval_units():
    assert schedule.parse_interval("30m") == 30
    assert schedule.parse_interval("45") == 45
    assert schedule.parse_interval("1h") == 60
    assert schedule.parse_interval("2h") == 120


def test_parse_interval_floor():
    with pytest.raises(ValueError):
        schedule.parse_interval("3m")


def test_render_plist_has_label_args_interval():
    p = schedule.render_plist("/usr/local/bin/cairn", "/Users/x/vault", 30, "/tmp/s.log")
    assert "<string>dev.agentcairn.sweep</string>" in p
    assert "<string>/usr/local/bin/cairn</string>" in p
    assert "<string>sweep</string>" in p and "<string>--vault</string>" in p
    assert "<integer>1800</integer>" in p  # 30m -> 1800s


def test_render_plist_escapes_xml():
    p = schedule.render_plist("/bin/cairn", "/v/a&b", 30, "/l.log")
    assert "a&amp;b" in p and "a&b" not in p


def test_render_cron_subhourly_and_hourly():
    assert schedule.render_cron_line("/c", "/v", 30, "/l").startswith("*/30 * * * * ")
    assert schedule.render_cron_line("/c", "/v", 120, "/l").startswith("0 */2 * * * ")
    assert schedule.render_cron_line("/c", "/v", 30, "/l").endswith("# agentcairn-sweep")


def test_render_cron_quotes_paths_and_rejects_bad_interval():
    line = schedule.render_cron_line("/c", "/Users/x/my vault", 30, "/l")
    assert "'/Users/x/my vault'" in line
    with pytest.raises(ValueError):
        schedule.render_cron_line("/c", "/v", 90, "/l")  # 90 not expressible
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_schedule.py -v`  → FAIL (module/functions missing).

- [ ] **Step 3: Implement the pure layer**

`src/cairn/schedule.py`:
```python
# SPDX-License-Identifier: Apache-2.0
"""Manage a per-OS scheduled `cairn sweep` (launchd / crontab)."""
from __future__ import annotations

import shlex
from xml.sax.saxutils import escape

PLIST_LABEL = "dev.agentcairn.sweep"
CRON_MARKER = "# agentcairn-sweep"
MIN_INTERVAL_MIN = 5


def parse_interval(s: str) -> int:
    """'30m' | '1h' | '45' -> minutes (bare number = minutes). Floor 5."""
    s = s.strip().lower()
    if s.endswith("h"):
        mins = int(float(s[:-1]) * 60)
    elif s.endswith("m"):
        mins = int(s[:-1])
    else:
        mins = int(s)
    if mins < MIN_INTERVAL_MIN:
        raise ValueError(f"interval must be at least {MIN_INTERVAL_MIN} minutes")
    return mins


def render_plist(cairn: str, vault: str, interval_min: int, log: str) -> str:
    c, v, lg = escape(cairn), escape(vault), escape(log)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>{PLIST_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{c}</string>
    <string>sweep</string>
    <string>--vault</string>
    <string>{v}</string>
  </array>
  <key>StartInterval</key><integer>{interval_min * 60}</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>{lg}</string>
  <key>StandardErrorPath</key><string>{lg}</string>
</dict>
</plist>
"""


def render_cron_line(cairn: str, vault: str, interval_min: int, log: str) -> str:
    if interval_min < 60:
        sched = f"*/{interval_min} * * * *"
    elif interval_min % 60 == 0:
        sched = f"0 */{interval_min // 60} * * *"
    else:
        raise ValueError(
            f"interval {interval_min}m can't be expressed in cron; use a value "
            "under 60 minutes or a whole number of hours"
        )
    cmd = f"{shlex.quote(cairn)} sweep --vault {shlex.quote(vault)} >> {shlex.quote(log)} 2>&1"
    return f"{sched} {cmd}  {CRON_MARKER}"
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_schedule.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/schedule.py tests/test_schedule.py
git commit -m "feat(schedule): interval parsing + plist/cron renderers"
```

---

## Task 2: Side-effecting backends (install/uninstall/status)

**Files:**
- Modify: `src/cairn/schedule.py`
- Test: `tests/test_schedule.py`

**Interfaces:**
- Consumes: Task 1's renderers + constants.
- Produces: `resolve_cairn() -> str`; `log_path() -> Path`; `install(interval_min: int, vault=None) -> None`; `uninstall() -> bool` (True if something removed); `status() -> dict | None` (`{"interval_min": int, ...}` or None). A module-level `_run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess` is the single side-effect seam tests monkeypatch.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_schedule.py`)

```python
import sys
from pathlib import Path


def _fake_run(records):
    def run(cmd, stdin=None):
        records.append((cmd, stdin))
        class R:  # crontab -l returns empty by default
            returncode = 1 if cmd[:2] == ["crontab", "-l"] else 0
            stdout = ""
        return R()
    return run


def test_install_linux_writes_marked_cron(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("HOME", str(tmp_path))
    written = {}
    def run(cmd, stdin=None):
        class R:
            returncode = 1 if cmd[:2] == ["crontab", "-l"] else 0
            stdout = ""
        if cmd == ["crontab", "-"]:
            written["text"] = stdin
        return R()
    monkeypatch.setattr(schedule, "_run", run)
    schedule.install(30, tmp_path / "vault")
    assert "# agentcairn-sweep" in written["text"]
    assert "*/30 * * * *" in written["text"]


def test_install_linux_idempotent(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    state = {"crontab": ""}
    def run(cmd, stdin=None):
        class R:
            returncode = 0 if state["crontab"] else 1
            stdout = state["crontab"]
        if cmd == ["crontab", "-"]:
            state["crontab"] = stdin
        return R()
    monkeypatch.setattr(schedule, "_run", run)
    schedule.install(30, tmp_path / "v")
    schedule.install(15, tmp_path / "v")
    assert state["crontab"].count("# agentcairn-sweep") == 1
    assert "*/15" in state["crontab"]


def test_uninstall_absent_is_false(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(schedule, "_run", _fake_run([]))
    assert schedule.uninstall() is False


def test_install_macos_writes_plist(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(schedule, "_run", _fake_run([]))
    schedule.install(30, tmp_path / "vault")
    plist = tmp_path / "Library" / "LaunchAgents" / "dev.agentcairn.sweep.plist"
    assert plist.exists() and "<integer>1800</integer>" in plist.read_text()
    assert schedule.status()["interval_min"] == 30


def test_unsupported_platform_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "win32")
    with pytest.raises(RuntimeError):
        schedule.install(30, tmp_path / "v")
```

- [ ] **Step 2: Run — expect failure.**  Run: `uv run pytest tests/test_schedule.py -v`.

- [ ] **Step 3: Implement the backends** (append to `src/cairn/schedule.py`)

```python
import re
import shutil
import subprocess
import sys
from pathlib import Path

from cairn.paths import cache_root, resolve_vault


def _run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def resolve_cairn() -> str:
    """Absolute path to the `cairn` binary (launchd/cron have a minimal PATH)."""
    return shutil.which("cairn") or sys.argv[0]


def log_path() -> Path:
    return cache_root() / "sweep.log"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _macos_install(interval_min: int, vault: Path, log: Path) -> None:
    p = _plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_plist(resolve_cairn(), str(vault), interval_min, str(log)))
    _run(["launchctl", "unload", str(p)])  # ignore "not loaded"
    _run(["launchctl", "load", str(p)])


def _macos_uninstall() -> bool:
    p = _plist_path()
    if not p.exists():
        return False
    _run(["launchctl", "unload", str(p)])
    p.unlink()
    return True


def _macos_status() -> dict | None:
    p = _plist_path()
    if not p.exists():
        return None
    m = re.search(r"<key>StartInterval</key>\s*<integer>(\d+)</integer>", p.read_text())
    return {"interval_min": int(m.group(1)) // 60 if m else None, "path": str(p)}


def _read_crontab() -> str:
    r = _run(["crontab", "-l"])
    return r.stdout if r.returncode == 0 else ""


def _write_crontab(text: str) -> None:
    _run(["crontab", "-"], stdin=text if text.endswith("\n") else text + "\n")


def _linux_install(interval_min: int, vault: Path, log: Path) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    line = render_cron_line(resolve_cairn(), str(vault), interval_min, str(log))
    kept = [ln for ln in _read_crontab().splitlines() if CRON_MARKER not in ln]
    kept.append(line)
    _write_crontab("\n".join(kept))


def _linux_uninstall() -> bool:
    cur = _read_crontab().splitlines()
    kept = [ln for ln in cur if CRON_MARKER not in ln]
    if len(kept) == len(cur):
        return False
    _write_crontab("\n".join(kept))
    return True


def _linux_status() -> dict | None:
    for ln in _read_crontab().splitlines():
        if CRON_MARKER in ln:
            sub = re.match(r"\*/(\d+) ", ln)
            hr = re.match(r"0 \*/(\d+) ", ln)
            iv = int(sub.group(1)) if sub else (int(hr.group(1)) * 60 if hr else None)
            return {"interval_min": iv, "line": ln}
    return None


def _backend():
    if sys.platform == "darwin":
        return _macos_install, _macos_uninstall, _macos_status
    if sys.platform.startswith("linux"):
        return _linux_install, _linux_uninstall, _linux_status
    raise RuntimeError(
        f"scheduling isn't supported on {sys.platform} yet — run "
        "`cairn schedule install --print` and add it to your scheduler manually"
    )


def install(interval_min: int, vault=None) -> None:
    inst, _, _ = _backend()
    inst(interval_min, resolve_vault(vault), log_path())


def uninstall() -> bool:
    _, un, _ = _backend()
    return un()


def status() -> dict | None:
    _, _, st = _backend()
    return st()
```

- [ ] **Step 4: Run — expect pass.** Run: `uv run pytest tests/test_schedule.py -v`.

- [ ] **Step 5: Commit**

```bash
git add src/cairn/schedule.py tests/test_schedule.py
git commit -m "feat(schedule): launchd/crontab install/uninstall/status backends"
```

---

## Task 3: `cairn schedule` sub-app + the install hint

**Files:**
- Modify: `src/cairn/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `cairn.schedule` (Task 1+2), `cairn.paths.resolve_vault`, the existing `app = typer.Typer(...)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`; it already has `from cairn.cli import app` + `runner = CliRunner()`)

```python
def test_schedule_install_print_writes_nothing(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    res = runner.invoke(app, ["schedule", "install", "--interval", "30m",
                              "--vault", str(tmp_path / "v"), "--print"])
    assert res.exit_code == 0
    assert "# agentcairn-sweep" in res.output and "*/30 * * * *" in res.output


def test_schedule_status_not_installed(tmp_path, monkeypatch):
    import sys
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("cairn.schedule._run",
                        lambda cmd, stdin=None: type("R", (), {"returncode": 1, "stdout": ""})())
    res = runner.invoke(app, ["schedule", "status"])
    assert res.exit_code == 0 and "not installed" in res.output.lower()
```

- [ ] **Step 2: Run — expect failure** (no `schedule` command). Run: `uv run pytest tests/test_cli.py -k schedule -v`.

- [ ] **Step 3: Implement the sub-app** — add to `src/cairn/cli.py` near the other commands (after `app = typer.Typer(...)` exists; place at module level so the sub-app registers at import):

```python
schedule_app = typer.Typer(
    help="Manage a background schedule that runs `cairn sweep` periodically "
    "(the host-agnostic capture backstop)."
)
app.add_typer(schedule_app, name="schedule")


@schedule_app.command("install")
def schedule_install(
    interval: str = typer.Option("30m", "--interval", help="e.g. 30m, 1h, or minutes."),
    vault: Path = typer.Option(None, "--vault"),
    print_only: bool = typer.Option(False, "--print", help="Render only; write nothing."),
) -> None:
    from cairn import schedule
    from cairn.paths import resolve_vault

    mins = schedule.parse_interval(interval)
    v = resolve_vault(vault)
    log = str(schedule.log_path())
    cairn = schedule.resolve_cairn()
    if print_only:
        rendered = (
            schedule.render_plist(cairn, str(v), mins, log)
            if sys.platform == "darwin"
            else schedule.render_cron_line(cairn, str(v), mins, log)
        )
        typer.echo(rendered)
        return
    schedule.install(mins, v)
    typer.echo(f"Scheduled `cairn sweep` every {mins}m for vault {v}.")


@schedule_app.command("uninstall")
def schedule_uninstall() -> None:
    from cairn import schedule

    typer.echo("Removed agentcairn schedule." if schedule.uninstall() else "No agentcairn schedule found.")


@schedule_app.command("status")
def schedule_status() -> None:
    from cairn import schedule

    st = schedule.status()
    typer.echo(f"installed: runs `cairn sweep` every {st['interval_min']}m" if st else "not installed")
```

Ensure `import sys` and `from pathlib import Path` are present at the top of `cli.py` (Path almost certainly is; add `sys` if missing).

- [ ] **Step 4: Add the hint to `install`** — in the `install` command, after a successful host install completes (both the single-host and `--all` success paths, just before the function returns; NOT in the `--print`/detect-only early returns), add:

```python
    typer.echo(
        "Tip: run `cairn schedule install` to capture sessions periodically in "
        "the background. Useful for more timely ingestion of memories from "
        "long-running sessions."
    )
```

- [ ] **Step 5: Run — expect pass**

Run: `uv run pytest tests/test_cli.py -k schedule -v` then `uv run pytest tests/test_cli.py -q` (no regressions).

- [ ] **Step 6: Commit**

```bash
git add src/cairn/cli.py tests/test_cli.py
git commit -m "feat(cli): cairn schedule sub-app + install hint"
```

---

## Task 4: docs pointer

**Files:**
- Modify: `src/cairn/cli.py` (the `sweep` command docstring/help), `README.md`

- [ ] **Step 1: Point users at the managed scheduler**

In `cli.py`, the `sweep` command help says "(cron maintenance)". Update it to mention the managed command, e.g. append: `Use `cairn schedule install` to run this automatically.` In `README.md`, find where periodic capture / cron is mentioned (search `sweep`/`cron`) and add a short line: running `cairn schedule install` sets up a launchd/crontab job to sweep every 30m. If README has no such section, add a one-liner under the capture/usage section.

- [ ] **Step 2: Verify build + commit**

Run: `uv run pytest -q` (full suite green).
```bash
git add src/cairn/cli.py README.md
git commit -m "docs: point at cairn schedule for periodic capture"
```

---

## Self-Review

**Spec coverage:** render functions + interval (Task 1); launchd + crontab install/uninstall/status, dispatch, unsupported-platform error, idempotency, cairn-path resolution, log path (Task 2); `schedule` sub-app with install/uninstall/status/--print/--interval/--vault + the install hint (Task 3); docs pointer (Task 4). Cron-granularity handling + the 5-min floor are in Task 1. XML-escape (plist) + shlex.quote (cron) are in Task 1's code and asserted by its tests.

**Placeholder scan:** every code step is concrete; the only soft step is Task 4's README edit ("find where cron is mentioned"), which is a doc touch, not logic.

**Type/name consistency:** `parse_interval`/`render_plist`/`render_cron_line`/`resolve_cairn`/`log_path`/`install`/`uninstall`/`status`/`_run` are named identically across tasks and the CLI. `PLIST_LABEL`/`CRON_MARKER` constants are defined once (Task 1) and reused. `status()` returns `{"interval_min": …}` consumed by the CLI in Task 3.
