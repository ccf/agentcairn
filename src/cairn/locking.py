# SPDX-License-Identifier: Apache-2.0
"""Cross-process serialization for mutations of a vault and its derived index.

The lock file is only a stable rendezvous point.  Ownership is enforced by the
operating system, so a crashed process cannot leave a stale lock behind merely
because the file still exists.
"""

from __future__ import annotations

import errno
import os
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from cairn import paths
from cairn.storage import ensure_private_dir


class VaultBusyError(RuntimeError):
    """Raised when another process is already mutating the same vault."""

    def __init__(self, vault: Path, lock_path: Path, owner: str = "") -> None:
        detail = f" ({owner})" if owner else ""
        super().__init__(
            f"vault is busy: {vault}; another AgentCairn writer holds {lock_path}{detail}. "
            "Retry after the active sweep, reindex, or remember call finishes."
        )
        self.vault = vault
        self.lock_path = lock_path
        self.owner = owner


def writer_lock_path(vault: Path | str) -> Path:
    """Return the stable advisory-lock path for ``vault``."""
    configured = os.environ.get("CAIRN_LOCK_DIR")
    lock_dir = Path(configured).expanduser() if configured else paths.cache_root() / "locks"
    return lock_dir / f"{paths.vault_key(vault)}.lock"


def _open_lock(path: Path) -> BinaryIO:
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    handle = os.fdopen(fd, "r+b", buffering=0)
    # ``msvcrt.locking`` locks bytes from the current file position and needs
    # the byte to exist.  A concurrent first opener writing the same NUL is safe.
    if os.fstat(handle.fileno()).st_size == 0:
        handle.write(b"\0")
    return handle


def _acquire(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI/users
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release(handle: BinaryIO) -> None:
    handle.seek(0)
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI/users
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _owner(handle: BinaryIO) -> str:
    try:
        handle.seek(1)
        return handle.read().decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _is_contention(exc: OSError) -> bool:
    return isinstance(exc, BlockingIOError) or exc.errno in {
        errno.EACCES,
        errno.EAGAIN,
        errno.EDEADLK,
    }


@contextmanager
def vault_writer_lock(
    vault: Path | str,
    *,
    operation: str = "write",
    timeout: float = 0.0,
    poll_interval: float = 0.05,
) -> Iterator[None]:
    """Hold the per-vault writer lock for the duration of the context.

    ``timeout=0`` is deliberately fail-fast: hooks and MCP calls should report
    contention instead of hanging behind a potentially slow embedding run.
    Positive timeouts use short bounded polling and still raise
    :class:`VaultBusyError` with owner metadata when exhausted.
    """
    resolved_vault = Path(vault).expanduser().resolve()
    lock_path = writer_lock_path(resolved_vault)
    handle = _open_lock(lock_path)
    deadline = time.monotonic() + max(timeout, 0.0)
    acquired = False
    try:
        while True:
            try:
                _acquire(handle)
                acquired = True
                break
            except OSError as exc:
                if not _is_contention(exc):
                    raise
                if time.monotonic() >= deadline:
                    raise VaultBusyError(resolved_vault, lock_path, _owner(handle)) from exc
                time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))

        owner = (
            f"pid={os.getpid()} host={socket.gethostname()} operation={operation} "
            f"acquired={datetime.now(UTC).isoformat()}"
        )
        handle.seek(1)
        handle.truncate()
        handle.write(owner.encode("utf-8"))
        os.fsync(handle.fileno())
        yield
    finally:
        if acquired:
            _release(handle)
        handle.close()
