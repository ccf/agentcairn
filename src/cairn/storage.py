# SPDX-License-Identifier: Apache-2.0
"""Private filesystem primitives, including crash-safe atomic replacement."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

PRIVATE_FILE_MODE = 0o600
PRIVATE_DIR_MODE = 0o700

# Opt-in modes for shared-GID setups (Docker/NAS containers that share a group
# but not a UID). Only ever applied to VAULT writes — the index, ledgers, lock
# files, host configs, and ~/.agentcairn/config.toml stay private regardless,
# since sharing an Obsidian vault is no reason to widen the cache (or a file
# that can hold API keys). See #159.
GROUP_FILE_MODE = 0o660
GROUP_DIR_MODE = 0o770


def vault_file_mode(env=None) -> int:
    """File mode for new vault notes: 0660 when opted in, else 0600."""
    from cairn.config import resolve_vault_group_writable

    return GROUP_FILE_MODE if resolve_vault_group_writable(env) else PRIVATE_FILE_MODE


def vault_dir_mode(env=None) -> int:
    """Directory mode for new vault dirs: 0770 when opted in, else 0700."""
    from cairn.config import resolve_vault_group_writable

    return GROUP_DIR_MODE if resolve_vault_group_writable(env) else PRIVATE_DIR_MODE


def ensure_vault_dir(path: Path) -> Path:
    """`ensure_private_dir` for paths inside the vault (honors the group knob)."""
    return ensure_private_dir(path, mode=vault_dir_mode())


def atomic_write_vault_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """`atomic_write_text` for vault notes (honors the group knob).

    Named separately rather than threading a flag through every caller so the
    vault/cache split stays visible at each call site.
    """
    atomic_write_text(
        path, text, encoding=encoding, mode=vault_file_mode(), dir_mode=vault_dir_mode()
    )


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
    dir_mode: int = PRIVATE_DIR_MODE,
) -> None:
    """Atomically replace a text file without weakening its permissions.

    A unique temporary file is created in the destination directory, flushed,
    fsynced, and renamed with :func:`os.replace`. Existing destination modes are
    preserved exactly; newly-created files default to ``0600``. Any temporary
    file is removed if writing or replacement fails.

    ``dir_mode`` covers parent directories this call has to create — without it a
    group-writable vault note could land in a 0700 directory nobody in the group
    can traverse.
    """
    path = Path(path)
    ensure_private_dir(path.parent, mode=dir_mode)
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
