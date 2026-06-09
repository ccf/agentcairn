# tests/ingest/test_locate.py
# SPDX-License-Identifier: Apache-2.0
import json
from pathlib import Path

from cairn.ingest.locate import encode_cwd, find_transcripts, parse_transcript


def _line(type_, role=None, content=None, **extra):
    d = {"type": type_, "sessionId": "sess-1", **extra}
    if role is not None:
        d["message"] = {"role": role, "content": content}
    return json.dumps(d)


def _write_transcript(path: Path) -> None:
    lines = [
        _line("mode", mode="default"),  # metadata -> skipped
        _line(
            "user",
            role="user",
            content="fix the bug",
            cwd="/Users/x/proj",
            timestamp="2026-06-08T10:00:00Z",
            gitBranch="main",
        ),
        _line(
            "assistant",
            role="assistant",
            content=[
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "Fixed it."},
                {"type": "tool_use", "name": "Edit"},
            ],
            cwd="/Users/x/proj",
            timestamp="2026-06-08T10:00:05Z",
        ),
        _line("system"),  # metadata -> skipped
        "{ this is a truncated/corrupt line",  # malformed -> skipped, no crash
    ]
    path.write_text("\n".join(lines) + "\n")


def test_encode_cwd_matches_claude_layout():
    assert encode_cwd("/Users/ccf/git/agentcairn") == "-Users-ccf-git-agentcairn"


def test_find_transcripts_empty_when_missing(tmp_path):
    # graceful: no projects dir -> [] (never raise)
    assert find_transcripts(root=tmp_path / "nope") == []


def test_find_transcripts_filters_by_project(tmp_path):
    proj = tmp_path / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text("{}\n")
    other = tmp_path / "-Users-x-other"
    other.mkdir(parents=True)
    (other / "b.jsonl").write_text("{}\n")
    found = find_transcripts(root=tmp_path, project="/Users/x/proj")
    assert [p.name for p in found] == ["a.jsonl"]


def test_parse_transcript_extracts_turns_and_provenance(tmp_path):
    t = tmp_path / "s.jsonl"
    _write_transcript(t)
    tr = parse_transcript(t)
    assert tr.session_id == "sess-1"
    assert tr.cwd == "/Users/x/proj"
    assert tr.git_branch == "main"
    # only user string + assistant text block survive; thinking/tool_use/metadata dropped
    assert [(turn.role, turn.text) for turn in tr.turns] == [
        ("user", "fix the bug"),
        ("assistant", "Fixed it."),
    ]


def test_parse_transcript_unknown_harness_raises():
    import pytest

    with pytest.raises(ValueError):
        find_transcripts(harness="codex")


def test_session_id_from_first_content_line(tmp_path):
    """M1: session_id must come from the FIRST content line; later lines must not override."""
    import json

    t = tmp_path / "default-stem.jsonl"
    lines = [
        json.dumps(
            {
                "type": "user",
                "sessionId": "first-session",
                "message": {"role": "user", "content": "first message"},
            }
        ),
        json.dumps(
            {
                "type": "assistant",
                "sessionId": "second-session",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "reply"}],
                },
            }
        ),
    ]
    t.write_text("\n".join(lines) + "\n")
    tr = parse_transcript(t)
    assert tr.session_id == "first-session", (
        f"session_id should be from first content line, got {tr.session_id!r}"
    )
