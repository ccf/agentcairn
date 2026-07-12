# SPDX-License-Identifier: Apache-2.0
"""Private filesystem primitives, including crash-safe atomic replacement."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700


def ensure_private_dir(path: Path, *, mode: int = PRIVATE_DIR_MODE) -> Path:
    """Create ``path`` and missing parents with private defaults.

    Existing directories are deliberately left untouched. In particular, callers
    may safely use this for a user-owned vault without changing permissions the
    user already chose for that vault or any of its existing directories.
    """
    path = Path(path)
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent

    for directory in reversed(missing):
        try:
            directory.mkdir(mode=mode)
            directory.chmod(mode)
        except FileExistsError:
            # Another process may have created it between the exists() check and
            # mkdir(). Preserve that process's permissions just as we preserve any
            # other pre-existing directory.
            if not directory.is_dir():
                raise
    return path


def _mode_for_replacement(path: Path, default_mode: int) -> int:
    """Keep an existing file's mode; use a private mode for a new file."""
    try:
        return stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        return default_mode


def _fsync_dir(path: Path) -> None:
    """Best-effort directory sync so a successful rename survives a crash."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        # Some platforms/filesystems do not support fsync on directories. The
        # file itself was still flushed and atomically replaced.
        pass
    finally:
        os.close(fd)


def _chmod_open_file(fd: int, path: Path, mode: int) -> None:
    """Set an open file's mode, with a path fallback on non-POSIX Python."""
    fchmod = getattr(os, "fchmod", None)
    if fchmod is not None:
        fchmod(fd, mode)
    else:  # pragma: no cover - Windows does not expose os.fchmod
        path.chmod(mode)


def atomic_write_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    """Atomically replace a text file without weakening its permissions.

    A unique temporary file is created in the destination directory, flushed,
    fsynced, and renamed with :func:`os.replace`. Existing destination modes are
    preserved exactly; newly-created files default to ``0600``. Any temporary
    file is removed if writing or replacement fails.
    """
    path = Path(path)
    ensure_private_dir(path.parent)
    replacement_mode = _mode_for_replacement(path, mode)
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temp_path = Path(temp_name)
        stream = os.fdopen(fd, "w", encoding=encoding)
        fd = -1  # stream owns the descriptor from here on
        with stream:
            stream.write(text)
            stream.flush()
            _chmod_open_file(stream.fileno(), temp_path, replacement_mode)
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        temp_path = None  # os.replace consumed the temporary path
        _fsync_dir(path.parent)
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


def append_private_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    """Append text, creating the file and missing directories privately.

    Existing file and directory permissions are never changed. ``O_EXCL`` makes
    the secure-create decision race-free when multiple hooks start together.
    """
    path = Path(path)
    ensure_private_dir(path.parent)
    flags = os.O_WRONLY | os.O_APPEND
    created = False
    try:
        fd = os.open(path, flags | os.O_CREAT | os.O_EXCL, mode)
        created = True
    except FileExistsError:
        fd = os.open(path, flags)
    try:
        if created:
            _chmod_open_file(fd, path, mode)
        with os.fdopen(fd, "a", encoding=encoding) as stream:
            fd = -1  # stream owns the descriptor
            stream.write(text)
            stream.flush()
    finally:
        if fd >= 0:
            os.close(fd)
