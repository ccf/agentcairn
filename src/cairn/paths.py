# SPDX-License-Identifier: Apache-2.0
"""Vault-derived paths. The index/ledger/judged-cache are pure functions of the
vault root: explicit arg → env → derived default (`<cache>/indexes/<vault_key>.duckdb`).
This is the single home for the `vault_key` scheme the ledger already used inline."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from cairn.config import cairn_env


def cache_root() -> Path:
    return Path.home() / ".cache" / "agentcairn"


def resolve_vault(explicit: Path | str | None = None, env: Mapping[str, str] | None = None) -> Path:
    """--vault arg → CAIRN_VAULT → ~/agentcairn (matches the `vault` knob default)."""
    if explicit is not None:
        return Path(explicit).expanduser()
    if env is None:
        env = cairn_env()
    v = env.get("CAIRN_VAULT")
    return Path(v).expanduser() if v else Path.home() / "agentcairn"


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
