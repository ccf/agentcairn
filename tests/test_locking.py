# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from cairn.locking import VaultBusyError, vault_writer_lock, writer_lock_path


def _hold_lock(vault: str, cache: str, ready, release) -> None:
    """Subprocess target: hold the real OS lock until the parent releases us."""
    import cairn.locking as locking

    locking.paths.cache_root = lambda: Path(cache)
    with locking.vault_writer_lock(vault, operation="test-holder"):
        ready.set()
        release.wait(10)


def test_vault_writer_lock_serializes_processes_and_reports_owner(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    cache = tmp_path / "cache"
    monkeypatch.delenv("CAIRN_LOCK_DIR")
    monkeypatch.setattr("cairn.locking.paths.cache_root", lambda: cache)

    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    holder = ctx.Process(target=_hold_lock, args=(str(vault), str(cache), ready, release))
    holder.start()
    try:
        assert ready.wait(10), "lock holder did not start"
        with pytest.raises(VaultBusyError) as caught:
            with vault_writer_lock(vault, operation="test-contender"):
                pass
        message = str(caught.value)
        assert "vault is busy" in message
        assert f"pid={holder.pid}" in message
        assert "Retry after" in message
    finally:
        release.set()
        holder.join(10)
        if holder.is_alive():
            holder.terminate()
            holder.join(5)

    assert holder.exitcode == 0
    # The rendezvous file persists, but an exited owner releases the OS lock.
    with vault_writer_lock(vault, operation="test-after-release"):
        assert writer_lock_path(vault).exists()


def test_different_vaults_do_not_contend(tmp_path, monkeypatch):
    monkeypatch.delenv("CAIRN_LOCK_DIR")
    monkeypatch.setattr("cairn.locking.paths.cache_root", lambda: tmp_path / "cache")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    with vault_writer_lock(first, operation="first"):
        with vault_writer_lock(second, operation="second"):
            assert writer_lock_path(first) != writer_lock_path(second)
