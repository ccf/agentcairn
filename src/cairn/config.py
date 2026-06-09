# SPDX-License-Identifier: Apache-2.0
"""Shared configuration helpers. Env-based knobs resolved with the precedence
explicit-arg → environment → default. Home for future v1.1 knobs too."""

from __future__ import annotations

import os
from collections.abc import Mapping

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def parse_bool(value: str) -> bool:
    """Parse a boolean env/CLI string. Raises ValueError on unrecognized input."""
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ValueError(f"not a boolean: {value!r}")


def resolve_rerank(explicit: bool | None = None, env: Mapping[str, str] | None = None) -> bool:
    """Resolve the reranker on/off setting: explicit arg → CAIRN_RERANK env → True.
    An unparseable CAIRN_RERANK falls back to the default (True) rather than raising,
    so a typo never breaks a query."""
    if explicit is not None:
        return explicit
    if env is None:
        env = os.environ
    raw = env.get("CAIRN_RERANK")
    if raw is None:
        return True
    try:
        return parse_bool(raw)
    except ValueError:
        return True
