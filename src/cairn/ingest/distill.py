# src/cairn/ingest/distill.py
# SPDX-License-Identifier: Apache-2.0
"""Distill a Candidate into a non-lossy derived Note and write it to the vault.

Non-lossy law (spec §6): distillation only ADDS a derived note that links back to
its origin (the `source` frontmatter field); it never edits a source. v1 uses a
deterministic ExtractiveDistiller; the Distiller protocol leaves room for an
agent-loop / LLM distiller (Plan 5) behind the same interface."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from cairn.ingest.dedup import content_hash
from cairn.ingest.importance import score
from cairn.ingest.models import Candidate
from cairn.vault import Note, parse_note, write_note

_SLUG_STOP = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_words: int = 6) -> str:
    words = _SLUG_STOP.sub(" ", text.lower()).split()
    return "-".join(words[:max_words]) or "memory"


def _truncate_title(text: str, limit: int = 80) -> str:
    """First line, cut at a word boundary with an ellipsis — never mid-word
    (the '…malformed. Ca' bug). Whitespace-only text falls back to 'memory'."""
    lines = text.strip().splitlines()
    if not lines:
        return "memory"
    first = lines[0].strip()
    if len(first) <= limit:
        return first
    cut = first[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.") + "…"


class Distiller(Protocol):
    def distill(self, candidate: Candidate) -> Note: ...


class ExtractiveDistiller:
    """Deterministic baseline: capture the candidate verbatim as a memory note with
    provenance. No LLM — the agent-loop distiller (Plan 5) is the quality path."""

    def distill(self, candidate: Candidate) -> Note:
        h = content_hash(candidate.text)
        if candidate.kind == "summary":
            proj = candidate.project or "session"
            day = (candidate.timestamp or "")[:10]
            title = f"Session summary · {proj}" + (f" · {day}" if day else "")
            # Encode the session id in the slug so two different sessions in the same
            # project can never collide on the permalink (which would overwrite a note
            # without superseding it).
            sid_slug = _slugify(candidate.session_id or "")[:16] or "x"
            slug = f"session-summary-{_slugify(proj)}-{sid_slug}-{h[:8]}"
            frontmatter = {
                "title": title,
                "type": "memory",
                "kind": "session-summary",
                "permalink": slug,
                "tags": ["session-summary", "ingested"],
                "created": candidate.timestamp,
                "source": f"memory://session/{candidate.session_id}",
                "importance": round(candidate.importance, 3)
                if candidate.importance is not None
                else 0.9,
            }
            if candidate.project:
                frontmatter["project"] = candidate.project
            if candidate.harness:
                frontmatter["harness"] = candidate.harness
            body = (
                "- [context] Session summary (model-generated) #session-summary\n"
                f"- [verbatim] {candidate.text.strip()}\n"
            )
            return Note(permalink=slug, frontmatter=frontmatter, body=body)
        slug = f"{_slugify(candidate.text)}-{h[:8]}"
        j = candidate.judgment
        title = (j.title if j and j.title else None) or _truncate_title(candidate.text)
        imp = candidate.importance if candidate.importance is not None else score(candidate.text)
        frontmatter = {
            "title": title,
            "type": "memory",
            "permalink": slug,
            "tags": ["ingested"],
            "created": candidate.timestamp,
            "source": f"memory://session/{candidate.session_id}",
            "importance": round(imp, 3),
        }
        if candidate.project:
            frontmatter["project"] = candidate.project
        if candidate.harness:
            frontmatter["harness"] = candidate.harness
        verbatim = candidate.text.strip()
        if j and j.distilled:
            body = f"- [context] {j.distilled.strip()} #ingested\n- [verbatim] {verbatim}\n"
        else:
            body = f"- [context] {verbatim} #ingested\n"
        return Note(permalink=slug, frontmatter=frontmatter, body=body)


def mark_superseded(path: Path, by_permalink: str) -> None:
    """Set `superseded_by: <by_permalink>` in an existing note's frontmatter,
    preserving body/observations. Idempotent (re-setting the same value rewrites
    identical content). The reindex picks up the change and demotes it in recall."""
    note = parse_note(path.read_text(encoding="utf-8"))
    if note.frontmatter.get("superseded_by") == by_permalink:
        return
    note.frontmatter["superseded_by"] = by_permalink
    path.write_text(write_note(note), encoding="utf-8")


def supersede_prior_session_summaries(
    vault_root: Path,
    subdir: str,
    session_id: str,
    new_permalink: str,
    new_created: str | None = None,
) -> int:
    """Mark existing session-summary notes for `session_id` (other than `new_permalink`,
    not already superseded) as superseded_by the new one. Returns count. Fail-safe:
    skips malformed notes, never raises.

    Only supersedes notes STRICTLY OLDER than `new_created` (ISO timestamps compare
    lexically): write order isn't guaranteed to be timestamp order off the
    consolidating path, so a guard prevents an older summary written later from
    wrongly superseding a newer on-disk note. When either timestamp is missing,
    fall back to superseding (best-effort)."""
    src = f"memory://session/{session_id}"
    base = Path(vault_root) / subdir
    if not base.exists():
        return 0
    n = 0
    for path in base.glob("*.md"):
        try:
            note = parse_note(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        fm = note.frontmatter
        if not (
            fm.get("kind") == "session-summary"
            and fm.get("source") == src
            and fm.get("permalink") != new_permalink
            and not fm.get("superseded_by")
        ):
            continue
        # Skip only notes STRICTLY newer than the incoming one (write order isn't
        # timestamp order off the consolidating path, so an older summary written later
        # must not supersede a newer on-disk note). Equal timestamps DO supersede
        # (write-order tiebreak), so a same-second re-compaction still leaves exactly one
        # current summary per session.
        prior_created = fm.get("created")
        if (
            new_created is not None
            and prior_created is not None
            and str(prior_created) > str(new_created)
        ):
            continue
        try:
            mark_superseded(path, new_permalink)
            n += 1
        except Exception:
            pass
    return n


def write_derived_note(note: Note, vault_root: Path, *, subdir: str = "memories") -> Path:
    """Serialize `note` and write it under vault_root/subdir. Path-traversal guarded:
    the resolved target MUST stay within vault_root, else ValueError."""
    vault_root = Path(vault_root).resolve()
    target = (vault_root / subdir / f"{note.permalink}.md").resolve()
    if vault_root not in target.parents:
        raise ValueError(f"refusing to write outside vault root: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(write_note(note), encoding="utf-8")
    return target
