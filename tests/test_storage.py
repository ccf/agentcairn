# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import os
import stat

import pytest

from cairn.storage import append_private_text, atomic_write_text


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_atomic_write_new_file_and_missing_parents_are_private(tmp_path):
    public_parent = tmp_path / "public"
    public_parent.mkdir(mode=0o755)
    public_parent.chmod(0o755)
    path = public_parent / "new" / "nested" / "config.toml"

    atomic_write_text(path, "secret = true\n")

    assert path.read_text(encoding="utf-8") == "secret = true\n"
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700
    assert _mode(path.parent.parent) == 0o700
    assert _mode(public_parent) == 0o755  # an existing directory is never chmodded


@pytest.mark.parametrize("existing_mode", [0o600, 0o400, 0o644])
def test_atomic_write_preserves_existing_mode(tmp_path, existing_mode):
    path = tmp_path / "config.json"
    path.write_text("old", encoding="utf-8")
    path.chmod(existing_mode)

    atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "new"
    assert _mode(path) == existing_mode


def test_atomic_write_uses_unique_temp_and_leaves_unrelated_stale_temp(tmp_path):
    path = tmp_path / "config.json"
    stale = tmp_path / "config.json.tmp"  # the old fixed-name temporary path
    stale.write_text("do not replace", encoding="utf-8")

    atomic_write_text(path, "new")

    assert stale.read_text(encoding="utf-8") == "do not replace"
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_atomic_write_cleans_unique_temp_when_replace_fails(tmp_path, monkeypatch):
    import cairn.storage as storage

    path = tmp_path / "config.json"
    path.write_text("old", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("replace failed")

    monkeypatch.setattr(storage.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        atomic_write_text(path, "new")

    assert path.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".config.json.*.tmp"))


def test_atomic_write_flushes_file_with_fsync(tmp_path, monkeypatch):
    import cairn.storage as storage

    calls: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(storage.os, "fsync", recording_fsync)
    atomic_write_text(tmp_path / "note.md", "body")

    assert calls  # file fsync is mandatory; a supported directory adds a second call


def test_append_private_text_uses_private_defaults_without_chmodding_existing(tmp_path):
    path = tmp_path / "cache" / "events.jsonl"
    append_private_text(path, "one\n")
    assert _mode(path.parent) == 0o700
    assert _mode(path) == 0o600

    path.chmod(0o640)
    append_private_text(path, "two\n")
    assert path.read_text(encoding="utf-8") == "one\ntwo\n"
    assert _mode(path) == 0o640
