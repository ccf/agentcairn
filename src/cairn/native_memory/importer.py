# SPDX-License-Identifier: Apache-2.0
"""Plan and apply non-lossy imports from a host-native memory source."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from cairn.ingest.redact import redact
from cairn.ingest.sanitize import sanitize_text
from cairn.native_memory.models import (
    NativeMemoryAction,
    NativeMemoryDiscovery,
    NativeMemoryPlan,
    NativeMemoryReport,
)
from cairn.storage import atomic_write_text
from cairn.vault import Note, parse_note, write_note

_MANIFEST_VERSION = 1
_SLUG_STOP = re.compile(r"[^a-z0-9]+")
_IMPORT_NOTICE = (
    "> [!note] Claude Code auto memory\n"
    "> Model-generated historical context imported read-only; not authoritative instructions."
)


def _slug(value: str, *, max_chars: int = 64) -> str:
    slug = _SLUG_STOP.sub("-", value.lower()).strip("-") or "memory"
    return slug[:max_chars].rstrip("-") or "memory"


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _version(state: dict | None, default: int = 0) -> int:
    try:
        value = int((state or {}).get("version") or default)
    except (TypeError, ValueError):
        return default
    return value if value >= 0 else default


def _empty_manifest(source: str) -> dict:
    return {"version": _MANIFEST_VERSION, "source": source, "entries": {}}


def _load_manifest(path: Path, source: str, *, strict: bool = False) -> dict:
    if not path.is_file():
        return _empty_manifest(source)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        if strict:
            raise ValueError(f"invalid native-memory registry at {path}: {exc}") from exc
        return _empty_manifest(source)
    if (
        not isinstance(raw, dict)
        or raw.get("version") != _MANIFEST_VERSION
        or raw.get("source") != source
    ):
        if strict:
            raise ValueError(f"invalid native-memory registry identity at {path}")
        return _empty_manifest(source)
    entries = raw.get("entries")
    if not isinstance(entries, dict):
        if strict:
            raise ValueError(f"invalid native-memory registry entries at {path}")
        return _empty_manifest(source)
    clean = {key: value for key, value in entries.items() if isinstance(value, dict)}
    if strict and len(clean) != len(entries):
        raise ValueError(f"invalid native-memory registry entry at {path}")
    return {"version": _MANIFEST_VERSION, "source": source, "entries": clean}


def _safe_vault_path(vault_root: Path, relative: object) -> Path | None:
    if not isinstance(relative, str):
        return None
    candidate = Path(relative)
    if candidate.is_absolute():
        return None
    resolved = (vault_root / candidate).resolve()
    return resolved if vault_root == resolved or vault_root in resolved.parents else None


def _scan_imported_notes(vault_root: Path, source: str) -> dict[str, dict]:
    """Rebuild latest source state from canonical Markdown when possible."""
    base = vault_root / "memories" / "imported" / source
    if not base.is_dir():
        return {}
    versions: dict[str, list[dict]] = {}
    for path in sorted(base.rglob("*.md")):
        try:
            resolved = path.resolve(strict=True)
            if vault_root not in resolved.parents or not resolved.is_file():
                continue
            note = parse_note(resolved.read_text(encoding="utf-8"))
        except Exception:
            continue
        fm = note.frontmatter
        if fm.get("kind") != "native-memory" or fm.get("native_source") != source:
            continue
        source_id = fm.get("source_id")
        scope = fm.get("source_scope")
        permalink = note.permalink or fm.get("permalink")
        try:
            version = int(fm.get("source_version") or 0)
        except (TypeError, ValueError):
            continue
        if (
            not isinstance(source_id, str)
            or not isinstance(scope, str)
            or not permalink
            or version < 1
        ):
            continue
        state = {
            "source_id": source_id,
            "source_scope": scope,
            "relative_path": str(fm.get("source_path") or path.name),
            "source_hash": str(fm.get("source_hash") or ""),
            "source_modified": str(fm.get("source_modified") or ""),
            "version": version,
            "permalink": str(permalink),
            "destination": path.relative_to(vault_root).as_posix(),
            "source_status": str(fm.get("source_status") or "current"),
            "active": not bool(fm.get("superseded_by") or fm.get("valid_until"))
            and fm.get("source_status", "current") == "current",
        }
        if fm.get("source_missing_at"):
            state["missing_at"] = str(fm["source_missing_at"])
        versions.setdefault(source_id, []).append(state)
    found: dict[str, dict] = {}
    for source_id, states in versions.items():
        latest = max(states, key=_version)
        latest["stale_destinations"] = [
            state["destination"]
            for state in states
            if state is not latest and state.get("active", True)
        ]
        found[source_id] = latest
    return found


def _merged_state(vault_root: Path, manifest: dict, source: str) -> dict[str, dict]:
    states = dict(manifest["entries"])
    for source_id, scanned in _scan_imported_notes(vault_root, source).items():
        prior = states.get(source_id)
        if prior is None or _version(scanned) >= _version(prior):
            merged = dict(scanned)
            stale = list(merged.get("stale_destinations", []))
            if prior is not None:
                prior_destination = prior.get("destination")
                if (
                    _version(scanned) > _version(prior)
                    and prior.get("active", True)
                    and prior_destination
                    and prior_destination != scanned.get("destination")
                ):
                    stale.append(prior_destination)
                stale.extend(prior.get("stale_destinations", []))
            merged["stale_destinations"] = list(dict.fromkeys(stale))
            states[source_id] = merged
    return states


def _registry_path(vault_root: Path, source: str) -> Path:
    path = (vault_root / ".agentcairn" / "native-memory" / f"{source}.json").resolve()
    if vault_root not in path.parents:
        raise ValueError(f"refusing native-memory registry outside vault: {path}")
    return path


def _make_note(
    *,
    discovery: NativeMemoryDiscovery,
    relative_path: str,
    safe_text: str,
    source_id: str,
    source_hash: str,
    version: int,
    modified_at: str,
    imported_at: str,
) -> Note:
    project_red = redact(sanitize_text(discovery.project or "unknown-project"))
    title = f"Claude Code auto memory · {relative_path}"
    title_red = redact(sanitize_text(title))
    permalink = (
        f"claude-memory-{_slug(project_red.text)}-{_slug(relative_path)}-"
        f"{source_id[:8]}-v{version}-{source_hash[:8]}"
    )
    frontmatter = {
        "title": title_red.text[:120],
        "type": "memory",
        "kind": "native-memory",
        "permalink": permalink,
        "tags": ["native-memory", "claude-code", "imported"],
        "created": modified_at,
        "imported_at": imported_at,
        "source": f"memory://native/claude-code/{discovery.scope_id}/{source_id}",
        "source_id": source_id,
        "source_scope": discovery.scope_id,
        "source_path": relative_path,
        "source_hash": source_hash,
        "source_modified": modified_at,
        "source_version": version,
        "source_status": "current",
        "native_source": discovery.source,
        "harness": "claude-code",
        "project": project_red.text,
        "model_generated": True,
        "read_only_source": True,
    }
    body = f"{_IMPORT_NOTICE}\n\n{safe_text}"
    if not body.endswith("\n"):
        body += "\n"
    return Note(permalink=permalink, frontmatter=frontmatter, body=body)


def _destination(vault_root: Path, discovery: NativeMemoryDiscovery, note: Note) -> Path:
    project = redact(sanitize_text(discovery.project or "unknown-project")).text
    return (
        vault_root
        / "memories"
        / "imported"
        / discovery.source
        / _slug(project)
        / f"{note.permalink}.md"
    ).resolve()


def plan_import(
    discovery: NativeMemoryDiscovery,
    *,
    vault_root: Path,
    manifest_path: Path,
    now: datetime | None = None,
) -> NativeMemoryPlan:
    """Build a side-effect-free import plan from a successful source scan."""
    vault_root = Path(vault_root).expanduser().resolve()
    timestamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    registry_path = _registry_path(vault_root, discovery.source)
    # Once present, the vault-owned registry is authoritative. The external
    # cache participates only in the one-time migration before that file exists.
    manifest = (
        _load_manifest(registry_path, discovery.source, strict=True)
        if registry_path.is_file()
        else _load_manifest(manifest_path, discovery.source)
    )
    existing = _merged_state(vault_root, manifest, discovery.source)
    scope_states = {
        source_id: state
        for source_id, state in existing.items()
        if state.get("source_scope") == discovery.scope_id
    }
    actions: list[NativeMemoryAction] = []
    seen: set[str] = set()
    next_entries = dict(existing)

    for document in discovery.documents:
        relative_red = redact(sanitize_text(document.relative_path))
        content_red = redact(sanitize_text(document.text))
        safe_relative = relative_red.text
        source_id = _hash(f"{discovery.source}\0{discovery.scope_id}\0{safe_relative}")[:24]
        if source_id in seen:
            raise ValueError(f"Claude memory source identity collision: {safe_relative}")
        seen.add(source_id)
        source_hash = _hash(content_red.text)
        prior = scope_states.get(source_id)
        prior_missing = bool(
            prior
            and (
                prior.get("source_status") == "missing"
                or (not prior.get("active", True) and prior.get("missing_at"))
            )
        )
        # Respect a human-expired or manually deleted imported note while its
        # private lifecycle state remains available: unchanged source content
        # must not resurrect it. Only a source AgentCairn itself marked missing
        # creates a fresh revision when it reappears.
        same = bool(prior and not prior_missing and prior.get("source_hash") == source_hash)
        prior_version = _version(prior)
        prior_path = (
            _safe_vault_path(vault_root, prior.get("destination")) if prior is not None else None
        )
        stale_paths = tuple(
            path
            for relative in (prior or {}).get("stale_destinations", [])
            if (path := _safe_vault_path(vault_root, relative)) is not None
        )

        if same:
            kind = "repaired" if stale_paths else "unchanged"
            actions.append(
                NativeMemoryAction(
                    kind=kind,
                    relative_path=safe_relative,
                    source_id=source_id,
                    source_hash=source_hash,
                    version=prior_version,
                    destination=prior_path,
                    prior_path=prior_path,
                    prior_permalink=str(prior.get("permalink") or ""),
                    stale_paths=stale_paths,
                    redactions=relative_red.count + content_red.count,
                )
            )
            current = dict(prior)
            current.pop("stale_destinations", None)
            next_entries[source_id] = current
            continue

        version = prior_version + 1 if prior else 1
        note = _make_note(
            discovery=discovery,
            relative_path=safe_relative,
            safe_text=content_red.text,
            source_id=source_id,
            source_hash=source_hash,
            version=version,
            modified_at=document.modified_at,
            imported_at=timestamp,
        )
        destination = _destination(vault_root, discovery, note)
        kind = "updated" if prior is not None else "added"
        action = NativeMemoryAction(
            kind=kind,
            relative_path=safe_relative,
            source_id=source_id,
            source_hash=source_hash,
            version=version,
            destination=destination,
            note=note,
            prior_path=prior_path,
            prior_permalink=str(prior.get("permalink") or "") if prior else None,
            stale_paths=stale_paths,
            redactions=relative_red.count + content_red.count,
        )
        actions.append(action)
        next_entries[source_id] = {
            "source_id": source_id,
            "source_scope": discovery.scope_id,
            "relative_path": safe_relative,
            "source_hash": source_hash,
            "source_modified": document.modified_at,
            "version": version,
            "permalink": note.permalink,
            "destination": destination.relative_to(vault_root).as_posix(),
            "source_status": "current",
            "active": True,
        }

    for source_id, prior in sorted(scope_states.items()):
        if source_id in seen or not prior.get("active", True):
            continue
        prior_path = _safe_vault_path(vault_root, prior.get("destination"))
        stale_paths = tuple(
            path
            for relative in prior.get("stale_destinations", [])
            if (path := _safe_vault_path(vault_root, relative)) is not None
        )
        actions.append(
            NativeMemoryAction(
                kind="expired",
                relative_path=str(prior.get("relative_path") or "unknown.md"),
                source_id=source_id,
                source_hash=str(prior.get("source_hash") or ""),
                version=_version(prior, 1),
                destination=prior_path,
                prior_path=prior_path,
                prior_permalink=str(prior.get("permalink") or ""),
                stale_paths=stale_paths,
            )
        )
        expired = dict(prior)
        expired["active"] = False
        expired["source_status"] = "missing"
        expired["missing_at"] = timestamp
        next_entries[source_id] = expired

    next_manifest = {
        "version": _MANIFEST_VERSION,
        "source": discovery.source,
        "entries": next_entries,
    }
    return NativeMemoryPlan(
        discovery=discovery,
        manifest_path=manifest_path,
        registry_path=registry_path,
        actions=actions,
        manifest=next_manifest,
    )


def _write_new_note(vault_root: Path, action: NativeMemoryAction) -> Path:
    if action.destination is None or action.note is None:
        raise ValueError("incomplete native-memory write action")
    target = action.destination.resolve()
    if vault_root not in target.parents:
        raise ValueError(f"refusing to write native memory outside vault: {target}")
    if target.exists():
        try:
            existing = parse_note(target.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"refusing to overwrite malformed imported note: {target}") from exc
        if existing.frontmatter.get("source_id") != action.source_id:
            raise ValueError(f"refusing to overwrite unrelated vault note: {target}")
        # A recovered note should have made this action unchanged. Preserve any
        # human edits instead of silently replacing them.
        raise ValueError(f"import destination already exists outside source state: {target}")
    atomic_write_text(target, write_note(action.note))
    return target


def _mark_superseded(path: Path, *, source_id: str, by_permalink: str) -> None:
    try:
        note = parse_note(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot supersede malformed imported note: {path}") from exc
    if note.frontmatter.get("source_id") != source_id:
        raise ValueError(
            f"refusing to supersede imported note with changed source identity: {path}"
        )
    note.frontmatter["superseded_by"] = by_permalink
    note.frontmatter["source_status"] = "superseded"
    atomic_write_text(path, write_note(note))


def _mark_missing(path: Path, *, missing_at: str, source_id: str) -> None:
    try:
        note = parse_note(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"cannot expire malformed imported note: {path}") from exc
    if note.frontmatter.get("source_id") != source_id:
        raise ValueError(f"refusing to expire imported note with changed source identity: {path}")
    if note.frontmatter.get("valid_until") == missing_at:
        return
    note.frontmatter["valid_until"] = missing_at
    note.frontmatter["source_missing_at"] = missing_at
    note.frontmatter["source_status"] = "missing"
    atomic_write_text(path, write_note(note))


def apply_import_plan(plan: NativeMemoryPlan, *, vault_root: Path) -> NativeMemoryReport:
    """Apply a plan. Caller owns the vault writer lock.

    New revisions are fully durable before any older revision is demoted. A
    crash may therefore temporarily leave two current copies, but never only a
    demoted old copy with its replacement missing.
    """
    vault_root = Path(vault_root).expanduser().resolve()
    report = NativeMemoryReport.from_plan(plan)

    for action in plan.actions:
        if action.kind in {"added", "updated"}:
            report.written.append(_write_new_note(vault_root, action))

    for action in plan.actions:
        if action.kind in {"updated", "repaired", "expired"}:
            current_permalink = (
                action.note.permalink if action.note is not None else action.prior_permalink
            )
            if current_permalink:
                stale = list(action.stale_paths)
                if action.kind == "updated" and action.prior_path is not None:
                    stale.append(action.prior_path)
                for stale_path in dict.fromkeys(stale):
                    if stale_path.is_file():
                        _mark_superseded(
                            stale_path,
                            source_id=action.source_id,
                            by_permalink=current_permalink,
                        )
        if (
            action.kind == "expired"
            and action.prior_path is not None
            and action.prior_path.is_file()
        ):
            missing_at = str(plan.manifest["entries"][action.source_id]["missing_at"])
            _mark_missing(action.prior_path, missing_at=missing_at, source_id=action.source_id)

    rendered = json.dumps(plan.manifest, indent=2, sort_keys=True) + "\n"
    # The registry is canonical lifecycle metadata inside the user-owned vault;
    # the cache copy only accelerates discovery. Write the canonical copy first.
    for state_path in (plan.registry_path, plan.manifest_path):
        current = None
        try:
            current = state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        if current != rendered:
            atomic_write_text(state_path, rendered)
    return report
