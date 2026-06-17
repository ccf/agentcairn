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
