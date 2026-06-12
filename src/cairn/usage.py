# SPDX-License-Identifier: Apache-2.0
"""Local, no-telemetry token-savings ledger.

Records real recall events ({full_haystack_tokens, recalled_tokens}) to a JSONL
file the user owns, and summarizes them. Best-effort by design: a ledger failure
must NEVER break or slow recall. The estimator here is the single shared one —
the benchmark imports it so the personal number and the published benchmark
number use the identical model. A model of context size, not a measured cost.
"""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime
from pathlib import Path

from cairn.config import cairn_env

_CHARS_PER_TOKEN = 4
_SCHEMA = 1


def estimate_tokens(text: str | None) -> int:
    """Estimate tokens from character length (~4 chars/token, rounded up).

    Empty/None counts as 0. Deliberately simple and model-agnostic; labeled as an
    estimate wherever it surfaces.
    """
    if not text:
        return 0
    return (len(text) + _CHARS_PER_TOKEN - 1) // _CHARS_PER_TOKEN


def enabled() -> bool:
    """Usage tracking is on unless CAIRN_USAGE is set falsy (0/false/no/off)."""
    from cairn.config import parse_bool

    raw = cairn_env().get("CAIRN_USAGE", "1")
    try:
        return parse_bool(raw)
    except ValueError:
        return True


def ledger_path() -> Path:
    """$CAIRN_USAGE_PATH (env or config file) else ~/.cache/agentcairn/usage.jsonl."""
    env = cairn_env().get("CAIRN_USAGE_PATH")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".cache" / "agentcairn" / "usage.jsonl"


def record(event: str, *, full: int, recalled: int, k: int) -> None:
    """Append one ledger row. No-op when disabled; swallows ALL IO errors so a
    broken/unwritable ledger can never break recall."""
    if not enabled():
        return
    try:
        row = {
            "v": _SCHEMA,
            "ts": datetime.now(UTC).isoformat(),
            "event": event,
            "k": int(k),
            "full": int(full),
            "recalled": int(recalled),
        }
        p = ledger_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a") as f:
            f.write(json.dumps(row) + "\n")
    except Exception:
        pass  # analytics must never break recall


def summarize(path: Path | None = None) -> dict:
    """Aggregate the ledger into a summary. Tolerant of malformed/partial lines."""
    p = path or ledger_path()
    try:
        text = p.read_text()
    except OSError:
        text = ""
    fulls: list[int] = []
    recs: list[int] = []
    first_ts: str | None = None
    last_ts: str | None = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            full = int(row["full"])
            recalled = int(row["recalled"])
        except (ValueError, TypeError, KeyError):
            continue
        fulls.append(full)
        recs.append(recalled)
        ts = row.get("ts")
        if isinstance(ts, str):
            first_ts = ts if first_ts is None else min(first_ts, ts)
            last_ts = ts if last_ts is None else max(last_ts, ts)
    total_full = sum(fulls)
    total_recalled = sum(recs)
    factors = [f / r for f, r in zip(fulls, recs, strict=False) if r > 0]
    return {
        "recalls": len(fulls),
        "total_full": total_full,
        "total_recalled": total_recalled,
        "total_saved": max(0, total_full - total_recalled),
        "mean_factor": statistics.mean(factors) if factors else 0.0,
        "median_factor": statistics.median(factors) if factors else 0.0,
        "lifetime_factor": (total_full / total_recalled) if total_recalled > 0 else 0.0,
        "first_ts": first_ts,
        "last_ts": last_ts,
    }


def _human(n: int) -> str:
    """Compact token count: 2.3M / 12.4K / 980."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(int(n))


def oneline(summary: dict | None = None) -> str:
    """One-line SessionStart string; '' when there are no recalls / no savings.

    The factor is the lifetime ratio total_full/total_recalled (robust to call
    count), not a per-event average.
    """
    s = summary or summarize()
    if s.get("recalls", 0) <= 0 or s.get("total_saved", 0) <= 0:
        return ""
    factor = s.get("lifetime_factor", 0.0)
    return (
        f"\U0001fab9 agentcairn has saved you ~{_human(s['total_saved'])} tokens "
        f"across {s['recalls']} recalls (≈{factor:.0f}× smaller than your full vault)"
    )
