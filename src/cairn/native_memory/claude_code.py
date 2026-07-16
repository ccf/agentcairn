# SPDX-License-Identifier: Apache-2.0
"""Discover Claude Code's per-project, model-authored auto memory."""

from __future__ import annotations

import hashlib
import json
import os
import stat as stat_module
import subprocess
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from cairn.ingest.harness.claude_code import encode_cwd, legacy_encode_cwd
from cairn.ingest.redact import redact
from cairn.ingest.sanitize import sanitize_text
from cairn.native_memory.models import NativeMemoryDiscovery, NativeMemoryDocument

_MAX_MEMORY_FILE_BYTES = 2 * 1024 * 1024
_EXCLUDED_INSTRUCTION_FILES = {"claude.md", "claude.local.md"}
_MISSING = object()


def _git_output(project: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(project), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = proc.stdout.strip()
    return value if proc.returncode == 0 and value else None


def resolve_project_identity(project: Path) -> tuple[Path, Path]:
    """Return ``(worktree_root, shared_repository_identity)``.

    Claude scopes memory to the Git repository and shares it across worktrees.
    Outside Git, the selected directory is both values.
    """
    selected = Path(project).expanduser()
    if not selected.exists() or not selected.is_dir():
        raise ValueError(f"project directory does not exist: {selected}")
    selected = selected.resolve()
    top_raw = _git_output(selected, "rev-parse", "--show-toplevel")
    if top_raw is None:
        return selected, selected
    worktree = Path(top_raw).expanduser().resolve()
    common_raw = _git_output(worktree, "rev-parse", "--git-common-dir")
    if common_raw is None:
        return worktree, worktree
    common = Path(common_raw).expanduser()
    if not common.is_absolute():
        common = worktree / common
    common = common.resolve()
    identity = common.parent if common.name == ".git" else worktree
    return worktree, identity


def _claude_config_root(env: Mapping[str, str]) -> Path:
    configured = env.get("CLAUDE_CONFIG_DIR")
    return Path(configured).expanduser() if configured else Path.home() / ".claude"


def _settings_object(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read Claude settings {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"invalid Claude settings JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Claude settings must be a JSON object: {path}")
    return data


def _validated_memory_dir(raw: object, *, settings_path: Path) -> Path:
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        raise ValueError(
            f"Claude autoMemoryDirectory in {settings_path} must be absolute or start with '~/'."
        )
    raw = raw.strip()
    if not (raw.startswith("~/") or Path(raw).is_absolute()):
        raise ValueError(
            f"Claude autoMemoryDirectory in {settings_path} must be absolute or start with '~/'."
        )
    resolved = Path(raw).expanduser().resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("refusing root-like Claude autoMemoryDirectory")
    return resolved


def _setting(path: Path, key: str) -> object:
    data = _settings_object(path)
    return _MISSING if data is None or key not in data else data[key]


def _managed_settings_dir() -> Path:
    if sys.platform == "darwin":
        return Path("/Library/Application Support/ClaudeCode")
    if os.name == "nt":  # pragma: no cover - Windows CI exercises path construction only
        return Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode"
    return Path("/etc/claude-code")


def _managed_memory_dir() -> Path | None:
    """Resolve file-based managed settings, including ordered drop-ins.

    Server/MDM-delivered settings are not safely introspectable here; users of
    those tiers can always make the effective source explicit with ``--source``.
    """
    directory = _managed_settings_dir()
    paths = [directory / "managed-settings.json"]
    dropins = directory / "managed-settings.d"
    if dropins.is_dir():
        paths.extend(
            path
            for path in sorted(dropins.glob("*.json"))
            if not path.name.startswith(".") and path.is_file()
        )
    value: object = _MISSING
    policy_helper = False
    for path in paths:
        data = _settings_object(path)
        if data is None:
            continue
        if "autoMemoryDirectory" in data:
            value = data["autoMemoryDirectory"]
        if "policyHelper" in data:
            policy_helper = True
    if policy_helper:
        raise ValueError(
            "Claude managed policy uses policyHelper, so AgentCairn cannot prove the "
            "effective auto-memory directory; pass --source <dir>."
        )
    if value is _MISSING:
        return None
    return _validated_memory_dir(value, settings_path=directory / "managed-settings.json")


def _configured_memory_dir(config_root: Path, project_root: Path) -> Path | None:
    managed = _managed_memory_dir()
    if managed is not None:
        return managed

    # Claude may honor these layers only after workspace trust. AgentCairn does
    # not attempt to replay that security decision; if either layer redirects
    # memory, require the user to name the effective directory explicitly.
    project_settings = (
        project_root / ".claude" / "settings.local.json",
        project_root / ".claude" / "settings.json",
    )
    redirected = [
        path for path in project_settings if _setting(path, "autoMemoryDirectory") is not _MISSING
    ]
    if redirected:
        rendered = ", ".join(str(path) for path in redirected)
        raise ValueError(
            "Claude project/local settings redirect auto memory, but workspace-trust "
            f"precedence cannot be proven ({rendered}); pass --source <dir>."
        )

    user_settings = config_root / "settings.json"
    value = _setting(user_settings, "autoMemoryDirectory")
    if value is _MISSING:
        return None
    return _validated_memory_dir(value, settings_path=user_settings)


def _scope_id(identity: Path) -> str:
    remote = _git_output(identity, "config", "--get", "remote.origin.url")
    roots = _git_output(identity, "rev-list", "--max-parents=0", "HEAD")
    if roots:
        roots = ",".join(sorted(roots.splitlines()))
    inside_git = _git_output(identity, "rev-parse", "--is-inside-work-tree") == "true"
    if roots:
        material = f"git-roots:{roots}\0project:{identity.name}"
    elif remote:
        material = f"git-remote:{remote}\0project:{identity.name}"
    elif inside_git:
        material = f"git-project:{identity.name}"
    else:
        # Outside Git, Claude itself scopes by absolute cwd, so preserve that
        # distinction even though it is intentionally machine-local.
        material = f"path:{identity}"
    safe = redact(sanitize_text(material)).text
    return hashlib.sha256(f"claude-code\0{safe}".encode()).hexdigest()[:24]


def _memory_candidates(config_root: Path, roots: tuple[Path, ...]) -> list[Path]:
    candidates: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for encoded in dict.fromkeys((encode_cwd(str(root)), legacy_encode_cwd(str(root)))):
            candidate = config_root / "projects" / encoded / "memory"
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
    return candidates


def _read_documents(root: Path) -> tuple[NativeMemoryDocument, ...]:
    resolved_root = root.resolve(strict=True)
    candidates = [
        path for path in root.glob("*.md") if path.name.lower() not in _EXCLUDED_INSTRUCTION_FILES
    ]
    candidates.sort(key=lambda p: (p.name != "MEMORY.md", p.name.casefold()))

    # Validate the complete snapshot before reading any content. A symlink that
    # escapes the selected memory directory must not become an implicit import.
    validated: list[tuple[Path, Path]] = []
    for path in candidates:
        try:
            target = path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"cannot safely resolve Claude memory file {path}: {exc}") from exc
        if resolved_root not in target.parents:
            raise ValueError(f"refusing Claude memory symlink outside source root: {path}")
        if not target.is_file():
            continue
        size = target.stat().st_size
        if size > _MAX_MEMORY_FILE_BYTES:
            raise ValueError(
                f"Claude memory file is too large ({size} bytes; limit "
                f"{_MAX_MEMORY_FILE_BYTES}): {path.name}"
            )
        validated.append((path, target))

    documents: list[NativeMemoryDocument] = []
    for path, target in validated:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(target, flags)
        try:
            stat = os.fstat(fd)
            if not stat_module.S_ISREG(stat.st_mode):
                raise ValueError(f"Claude memory source is not a regular file: {path}")
            with os.fdopen(fd, "rb") as stream:
                fd = -1
                raw = stream.read(_MAX_MEMORY_FILE_BYTES + 1)
        finally:
            if fd >= 0:
                os.close(fd)
        if len(raw) > _MAX_MEMORY_FILE_BYTES:
            raise ValueError(
                f"Claude memory file is too large (limit {_MAX_MEMORY_FILE_BYTES}): {path.name}"
            )
        documents.append(
            NativeMemoryDocument(
                path=target,
                relative_path=path.name,
                text=raw.decode("utf-8", errors="replace"),
                modified_at=datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            )
        )
    return tuple(documents)


class ClaudeCodeMemorySource:
    """Current-project Claude auto-memory source.

    An explicit ``memory_dir`` wins. Otherwise Claude's config root and trusted
    user setting are honored, followed by current and legacy encoded repo paths.
    """

    name = "claude-code"

    def __init__(
        self,
        *,
        memory_dir: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._memory_dir = Path(memory_dir).expanduser() if memory_dir is not None else None
        self._env = dict(os.environ if env is None else env)

    def discover(self, project_root: Path) -> NativeMemoryDiscovery:
        worktree, identity = resolve_project_identity(project_root)
        config_root = _claude_config_root(self._env)

        if self._memory_dir is not None:
            root = self._memory_dir.resolve()
        else:
            configured = _configured_memory_dir(config_root, worktree)
            if configured is not None:
                root = configured
            else:
                roots = tuple(dict.fromkeys((identity, worktree, Path(project_root).resolve())))
                existing = [p for p in _memory_candidates(config_root, roots) if p.is_dir()]
                if len(existing) > 1:
                    rendered = ", ".join(str(path) for path in existing)
                    raise ValueError(
                        "multiple Claude memory directories match this project; "
                        f"choose one with --source: {rendered}"
                    )
                if not existing:
                    expected = _memory_candidates(config_root, (identity,))[0]
                    raise FileNotFoundError(
                        f"no Claude Code auto memory found for {identity} (expected {expected})"
                    )
                root = existing[0]

        if not root.exists() or not root.is_dir():
            raise FileNotFoundError(f"Claude Code auto-memory directory not found: {root}")
        try:
            documents = _read_documents(root)
        except OSError as exc:
            raise ValueError(f"cannot read Claude Code auto memory at {root}: {exc}") from exc
        return NativeMemoryDiscovery(
            source=self.name,
            root=root.resolve(),
            scope_id=_scope_id(identity),
            project=identity.name or worktree.name or None,
            project_root=identity,
            documents=documents,
        )
