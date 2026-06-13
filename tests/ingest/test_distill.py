# tests/ingest/test_distill.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

import pytest

from cairn.ingest.distill import ExtractiveDistiller, write_derived_note
from cairn.ingest.models import Candidate
from cairn.vault import parse_note


def _candidate(text="We decided to pin the store path to ~/.agentmemory/data."):
    return Candidate(
        text=text,
        session_id="sess-9",
        cwd="/Users/x/proj",
        git_branch="main",
        timestamp="2026-06-08T10:00:00Z",
        source_path=Path("/x/.claude/projects/p/sess-9.jsonl"),
    )


def test_distiller_builds_non_lossy_note_with_backlink():
    note = ExtractiveDistiller().distill(_candidate())
    assert note.frontmatter["type"] == "memory"
    assert note.frontmatter["source"] == "memory://session/sess-9"
    assert note.frontmatter["created"] == "2026-06-08T10:00:00Z"
    assert 0.0 <= note.frontmatter["importance"] <= 1.0
    assert note.permalink  # slug present
    # the candidate text is preserved verbatim in the body (non-lossy)
    assert "pin the store path" in note.body


def test_distiller_permalink_is_stable_for_same_content():
    a = ExtractiveDistiller().distill(_candidate())
    b = ExtractiveDistiller().distill(_candidate())
    assert a.permalink == b.permalink


def test_write_derived_note_lands_under_vault_root(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = ExtractiveDistiller().distill(_candidate())
    path = write_derived_note(note, vault, subdir="memories")
    assert path.exists()
    assert vault in path.parents
    # round-trips through the real parser
    parsed = parse_note(path.read_text())
    assert parsed.frontmatter["source"] == "memory://session/sess-9"


def test_write_derived_note_rejects_path_traversal(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = ExtractiveDistiller().distill(_candidate())
    note.permalink = "../../etc/evil"  # malicious slug
    with pytest.raises(ValueError):
        write_derived_note(note, vault, subdir="memories")


def test_mark_superseded_sets_frontmatter(tmp_path):
    from cairn.ingest.distill import mark_superseded
    from cairn.vault import parse_note

    p = tmp_path / "old.md"
    p.write_text(
        "---\ntitle: Old\ntype: memory\npermalink: old\n---\n\n- [context] old fact #ingested\n",
        encoding="utf-8",
    )
    mark_superseded(p, "new-permalink")
    note = parse_note(p.read_text(encoding="utf-8"))
    assert note.frontmatter.get("superseded_by") == "new-permalink"
    assert "old fact" in note.body  # body preserved
