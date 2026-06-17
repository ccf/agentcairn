# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn import paths


def test_resolve_vault_precedence(monkeypatch, tmp_path):
    # explicit wins
    assert paths.resolve_vault(tmp_path / "x", env={}) == (tmp_path / "x")
    # env next
    assert paths.resolve_vault(None, env={"CAIRN_VAULT": str(tmp_path / "y")}) == (tmp_path / "y")
    # default last
    assert paths.resolve_vault(None, env={}) == Path.home() / "agentcairn"


def test_vault_key_stable_and_distinct(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    assert paths.vault_key(a) == paths.vault_key(a)  # stable
    assert paths.vault_key(a) != paths.vault_key(b)  # distinct
    assert len(paths.vault_key(a)) == 16


def test_default_index_is_vault_scoped(tmp_path):
    idx = paths.default_index(tmp_path / "v")
    assert idx == paths.cache_root() / "indexes" / f"{paths.vault_key(tmp_path / 'v')}.duckdb"


def test_resolve_index_precedence(tmp_path):
    vault = tmp_path / "v"
    # explicit wins
    assert paths.resolve_index(tmp_path / "x.duckdb", vault, env={}) == (tmp_path / "x.duckdb")
    # CAIRN_INDEX next
    assert paths.resolve_index(None, vault, env={"CAIRN_INDEX": str(tmp_path / "e.duckdb")}) == (
        tmp_path / "e.duckdb"
    )
    # vault-derived default last
    assert paths.resolve_index(None, vault, env={}) == paths.default_index(vault)


def test_ledger_helpers_match_existing_scheme(tmp_path):
    vault = tmp_path / "v"
    assert (
        paths.default_ledger(vault)
        == paths.cache_root() / "ledgers" / f"{paths.vault_key(vault)}.sha256"
    )
    assert (
        paths.judged_cache(vault)
        == paths.cache_root() / "ledgers" / f"{paths.vault_key(vault)}.judged.jsonl"
    )
