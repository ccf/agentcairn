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
