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
import os
from datetime import UTC, datetime
from pathlib import Path

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
    """Usage tracking is on unless CAIRN_USAGE=0."""
    return os.environ.get("CAIRN_USAGE", "1") != "0"


def ledger_path() -> Path:
    """$CAIRN_USAGE_PATH if set, else ~/.cache/agentcairn/usage.jsonl."""
    env = os.environ.get("CAIRN_USAGE_PATH")
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
