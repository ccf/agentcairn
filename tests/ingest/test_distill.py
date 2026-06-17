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


def test_distiller_persists_project_and_harness_when_present():
    cand = Candidate(
        text="We decided to pin the store path to ~/.agentmemory/data.",
        session_id="sess-9",
        cwd="/Users/x/proj",
        git_branch="main",
        timestamp="2026-06-08T10:00:00Z",
        source_path=Path("/x/.claude/projects/p/sess-9.jsonl"),
        project="agentcairn",
        harness="claude-code",
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.frontmatter["project"] == "agentcairn"
    assert note.frontmatter["harness"] == "claude-code"


def test_distiller_omits_origin_keys_when_absent():
    cand = Candidate(
        text="We decided to pin the store path to ~/.agentmemory/data.",
        session_id="sess-9",
        cwd="/Users/x/proj",
        git_branch="main",
        timestamp="2026-06-08T10:00:00Z",
        source_path=Path("/x/.claude/projects/p/sess-9.jsonl"),
        project=None,
        harness=None,
    )
    note = ExtractiveDistiller().distill(cand)
    assert "project" not in note.frontmatter
    assert "harness" not in note.frontmatter


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


def test_distiller_session_summary_note_shape():
    from pathlib import Path

    from cairn.ingest.distill import ExtractiveDistiller
    from cairn.ingest.models import Candidate

    cand = Candidate(
        text="This session is being continued…\nSummary: did X, fixed Y.",
        session_id="sess-7",
        cwd="/x",
        git_branch=None,
        timestamp="2026-06-16T03:00:00Z",
        source_path=Path("/x/t.jsonl"),
        project="agentcairn",
        harness="claude-code",
        kind="summary",
    )
    note = ExtractiveDistiller().distill(cand)
    assert note.frontmatter["kind"] == "session-summary"
    assert "session-summary" in note.frontmatter["tags"]
    assert note.frontmatter["type"] == "memory"
    assert note.frontmatter["project"] == "agentcairn"
    assert note.frontmatter["harness"] == "claude-code"
    assert note.frontmatter["source"] == "memory://session/sess-7"
    assert "did X, fixed Y." in note.body  # verbatim summary retained
    assert note.permalink.startswith("session-summary-")


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


def test_supersession_timestamp_guard(tmp_path):
    """An older summary written later must NOT supersede a newer on-disk note;
    a genuinely newer summary still supersedes the older one (Bugbot: write order
    isn't timestamp order off the consolidating path)."""
    from pathlib import Path

    from cairn.ingest.distill import (
        ExtractiveDistiller,
        supersede_prior_session_summaries,
        write_derived_note,
    )
    from cairn.ingest.models import Candidate

    (tmp_path / "memories").mkdir()
    newer = Candidate(
        text="newer session summary content",
        session_id="s1",
        cwd="/x",
        git_branch=None,
        timestamp="2026-06-16T05:00:00Z",
        source_path=Path("/x/t.jsonl"),
        project="agentcairn",
        harness="claude-code",
        kind="summary",
    )
    p_new = write_derived_note(ExtractiveDistiller().distill(newer), tmp_path)

    # An OLDER incoming summary must NOT supersede the newer on-disk note.
    n = supersede_prior_session_summaries(
        tmp_path, "memories", "s1", "older-perma", "2026-06-16T01:00:00Z"
    )
    assert n == 0
    assert "superseded_by" not in p_new.read_text(encoding="utf-8")

    # A genuinely NEWER incoming summary DOES supersede the older on-disk note.
    n2 = supersede_prior_session_summaries(
        tmp_path, "memories", "s1", "newest-perma", "2026-06-16T09:00:00Z"
    )
    assert n2 == 1
    assert "superseded_by: newest-perma" in p_new.read_text(encoding="utf-8")
