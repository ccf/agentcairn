# SPDX-License-Identifier: Apache-2.0
"""Opt-in group access for vault writes (#159).

Shared-GID container setups (same group, different UID — common on Docker/NAS)
cannot read a 0600 vault, so an Obsidian sync sidecar sees nothing to replicate.
The knob widens NEW vault notes/dirs to 0660/0770.

It is opt-in on purpose: on macOS every local user's primary group is `staff`,
and shared groups like `users` are common on NAS hosts, so a group-readable
default would expose the vault to other accounts without anyone granting it.
The two invariants that matter here are (1) the default stays private, and
(2) the knob NEVER widens anything outside the vault.
"""

import stat

from cairn.config import resolve_vault_group_writable
from cairn.storage import (
    atomic_write_text,
    atomic_write_vault_text,
    ensure_private_dir,
    ensure_vault_dir,
)


def _mode(p) -> int:
    return stat.S_IMODE(p.stat().st_mode)


# --- the resolver ------------------------------------------------------------


def test_defaults_to_false():
    assert resolve_vault_group_writable({}) is False


def test_reads_the_env_knob():
    assert resolve_vault_group_writable({"CAIRN_VAULT_GROUP_WRITABLE": "1"}) is True
    assert resolve_vault_group_writable({"CAIRN_VAULT_GROUP_WRITABLE": "true"}) is True
    assert resolve_vault_group_writable({"CAIRN_VAULT_GROUP_WRITABLE": "0"}) is False


def test_unparseable_value_falls_back_to_private():
    """A typo must never silently widen permissions."""
    assert resolve_vault_group_writable({"CAIRN_VAULT_GROUP_WRITABLE": "yes-please"}) is False


# --- the default stays private (the regression that matters most) ------------


def test_vault_writes_are_private_by_default(tmp_path):
    note = tmp_path / "vault" / "memories" / "n.md"
    atomic_write_vault_text(note, "hi")
    assert _mode(note) == 0o600
    assert _mode(note.parent) == 0o700


def test_vault_dir_is_private_by_default(tmp_path):
    d = ensure_vault_dir(tmp_path / "vault")
    assert _mode(d) == 0o700


# --- the knob widens vault writes -------------------------------------------


def test_knob_makes_vault_notes_group_accessible(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_VAULT_GROUP_WRITABLE", "1")
    note = tmp_path / "vault" / "memories" / "n.md"
    atomic_write_vault_text(note, "hi")
    assert _mode(note) == 0o660


def test_knob_widens_parent_dirs_the_write_creates(tmp_path, monkeypatch):
    """A 0660 note inside a 0700 dir is still unreadable — the group cannot
    traverse it. The created parents must widen too."""
    monkeypatch.setenv("CAIRN_VAULT_GROUP_WRITABLE", "1")
    note = tmp_path / "vault" / "memories" / "deep" / "n.md"
    atomic_write_vault_text(note, "hi")
    assert _mode(note.parent) == 0o770
    assert _mode(note.parent.parent) == 0o770


def test_knob_widens_ensure_vault_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_VAULT_GROUP_WRITABLE", "1")
    assert _mode(ensure_vault_dir(tmp_path / "vault")) == 0o770


# --- scoping: the knob must NOT leak outside the vault -----------------------


def test_knob_does_not_widen_non_vault_files(tmp_path, monkeypatch):
    """The index, ledgers, lock files, host configs and ~/.agentcairn/config.toml
    (which can hold API keys) stay private even with the knob on."""
    monkeypatch.setenv("CAIRN_VAULT_GROUP_WRITABLE", "1")
    cache_file = tmp_path / "cache" / "usage.jsonl"
    atomic_write_text(cache_file, "{}")
    assert _mode(cache_file) == 0o600
    assert _mode(cache_file.parent) == 0o700


def test_knob_does_not_widen_ensure_private_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CAIRN_VAULT_GROUP_WRITABLE", "1")
    assert _mode(ensure_private_dir(tmp_path / "cache")) == 0o700


# --- existing permissions are still preserved --------------------------------


def test_existing_file_mode_is_preserved(tmp_path, monkeypatch):
    """Rewrites keep whatever the user chose; the knob only affects NEW files."""
    note = tmp_path / "vault" / "n.md"
    atomic_write_vault_text(note, "one")
    note.chmod(0o640)
    monkeypatch.setenv("CAIRN_VAULT_GROUP_WRITABLE", "1")
    atomic_write_vault_text(note, "two")
    assert _mode(note) == 0o640
    assert note.read_text() == "two"
