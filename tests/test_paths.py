# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import duckdb

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


def _make_index(path, note_path):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE notes (permalink TEXT, path TEXT)")
    con.execute("INSERT INTO notes VALUES ('a', ?)", [note_path])
    con.close()


def test_migrate_legacy_index_rehomes_by_inferred_vault(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    legacy = tmp_path / "cache" / "index.duckdb"
    vault_root = "/Users/x/somevault"
    _make_index(legacy, f"{vault_root}/memories/a.md")
    moved = paths.migrate_legacy_index(env={})
    assert moved == paths.default_index(vault_root)
    assert moved.exists() and not legacy.exists()


def test_migrate_legacy_index_noops_when_cairn_index_set(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    legacy = tmp_path / "cache" / "index.duckdb"
    _make_index(legacy, "/Users/x/v/memories/a.md")
    assert paths.migrate_legacy_index(env={"CAIRN_INDEX": "/somewhere/i.duckdb"}) is None
    assert legacy.exists()  # untouched


def test_migrate_legacy_index_noops_when_no_legacy(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    assert paths.migrate_legacy_index(env={}) is None


def test_index_for_triggers_migration(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "cache_root", lambda: tmp_path / "cache")
    legacy = tmp_path / "cache" / "index.duckdb"
    vault_root = "/Users/x/v2"
    _make_index(legacy, f"{vault_root}/memories/a.md")
    got = paths.index_for(None, vault_root, env={})
    assert got == paths.default_index(vault_root) and got.exists()
