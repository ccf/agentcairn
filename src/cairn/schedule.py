# SPDX-License-Identifier: Apache-2.0
"""Manage a per-OS scheduled `cairn sweep` (launchd / crontab)."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from cairn.paths import cache_root, resolve_vault

PLIST_LABEL = "dev.agentcairn.sweep"
CRON_MARKER = "# agentcairn-sweep"
MIN_INTERVAL_MIN = 5


def parse_interval(s: str) -> int:
    """'30m' | '1h' | '45' -> minutes (bare number = minutes). Floor 5."""
    s = s.strip().lower()
    if s.endswith("h"):
        mins = int(float(s[:-1]) * 60)
    elif s.endswith("m"):
        mins = int(float(s[:-1]))
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


# ---------------------------------------------------------------------------
# Side-effecting backends: launchd (macOS) + crontab (Linux)
# ---------------------------------------------------------------------------


def _run(cmd: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=stdin, capture_output=True, text=True)


def resolve_cairn() -> str:
    """Absolute path to the `cairn` binary (launchd/cron have a minimal PATH)."""
    return shutil.which("cairn") or str(Path(sys.argv[0]).resolve())


def log_path() -> Path:
    return cache_root() / "sweep.log"


def _plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{PLIST_LABEL}.plist"


def _macos_install(interval_min: int, vault: Path, log: Path) -> None:
    p = _plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_plist(resolve_cairn(), str(vault), interval_min, str(log)))
    _run(["launchctl", "unload", str(p)])  # best-effort; ignore returncode
    r = _run(["launchctl", "load", str(p)])
    if r.returncode != 0:
        raise RuntimeError(f"launchctl load failed: {r.stderr.strip()}")


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
    r = _run(["crontab", "-"], stdin=text if text.endswith("\n") else text + "\n")
    if r.returncode != 0:
        raise RuntimeError(f"crontab write failed: {r.stderr.strip()}")


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


def _supported() -> bool:
    return sys.platform == "darwin" or sys.platform.startswith("linux")


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
    inst(interval_min, resolve_vault(vault).resolve(), log_path())


def uninstall() -> bool:
    if not _supported():
        return False
    _, un, _ = _backend()
    return un()


def status() -> dict | None:
    if not _supported():
        return None
    _, _, st = _backend()
    return st()
