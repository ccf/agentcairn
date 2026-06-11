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


def test_encode_cwd_normalizes_trailing_slash():
    # a trailing slash (common from tab-completion) must map to the SAME encoded
    # dir Claude Code uses, which never has one.
    assert encode_cwd("/Users/x/proj/") == "-Users-x-proj"
    assert encode_cwd("/Users/x/proj/") == encode_cwd("/Users/x/proj")
    assert encode_cwd("/") == "-"  # root edge case, not all-empty


def test_session_id_comes_from_first_accepted_turn(tmp_path):
    # A content-type row that is skipped (no text) must NOT set provenance;
    # session_id should come from the first row that yields a real turn.
    t = tmp_path / "file.jsonl"
    t.write_text(
        "\n".join(
            [
                # user row with empty content -> skipped, must not set session_id
                _line("user", role="user", content="", sessionId="skipme"),
                # first ACCEPTED turn -> its sessionId wins
                _line("user", role="user", content="real question here", sessionId="real-sess"),
            ]
        )
        + "\n"
    )
    tr = parse_transcript(t)
    assert tr.session_id == "real-sess"
    assert [turn.text for turn in tr.turns] == ["real question here"]


def test_find_transcripts_project_filter_tolerates_trailing_slash(tmp_path):
    proj = tmp_path / "-Users-x-proj"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text("{}\n")
    # passing the project WITH a trailing slash must still find the transcript
    found = find_transcripts(root=tmp_path, project="/Users/x/proj/")
    assert [p.name for p in found] == ["a.jsonl"]


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


def test_classify_authored_user():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {"type": "user", "message": {"role": "user", "content": "fix the bug please"}}
    assert classify_claude_code(obj) == EventKind.AUTHORED_USER


def test_classify_tool_result():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert (
        classify_claude_code({"type": "user", "toolUseResult": {}, "message": {}})
        == EventKind.TOOL_RESULT
    )


def test_classify_meta_via_isMeta():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert (
        classify_claude_code({"type": "user", "isMeta": True, "message": {}})
        == EventKind.META_INJECTION
    )


def test_classify_meta_via_origin_task_notification():
    # <task-notification> carries NO isMeta/toolUseResult — only an `origin` object.
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {"type": "user", "origin": {"kind": "task-notification"}, "message": {}}
    assert classify_claude_code(obj) == EventKind.META_INJECTION


def test_classify_compact_summary():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {
        "type": "user",
        "isCompactSummary": True,
        "isVisibleInTranscriptOnly": True,
        "message": {},
    }
    assert classify_claude_code(obj) == EventKind.COMPACT_SUMMARY


def test_classify_assistant_and_system():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert (
        classify_claude_code({"type": "assistant", "message": {}}) == EventKind.AUTHORED_ASSISTANT
    )
    assert classify_claude_code({"type": "system"}) == EventKind.SYSTEM


def test_classify_unknown_is_failclosed():
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    assert classify_claude_code({"type": "last-prompt"}) == EventKind.UNKNOWN
    assert classify_claude_code({}) == EventKind.UNKNOWN
