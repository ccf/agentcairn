# tests/ingest/test_events.py
# SPDX-License-Identifier: Apache-2.0
from pathlib import Path

from cairn.ingest.events import EventKind, NormalizedEvent, project_from_cwd


def test_event_kind_values():
    assert EventKind.AUTHORED_USER.value == "authored_user"
    assert EventKind.TOOL_RESULT.value == "tool_result"
    assert EventKind.META_INJECTION.value == "meta_injection"
    assert EventKind.COMPACT_SUMMARY.value == "compact_summary"
    assert EventKind.UNKNOWN.value == "unknown"


def test_project_from_cwd():
    assert project_from_cwd("/Users/ccf/git/agentcairn") == "agentcairn"
    assert project_from_cwd("/Users/ccf/git/agentcairn/") == "agentcairn"  # trailing slash
    assert project_from_cwd("/") is None
    assert project_from_cwd(None) is None
    assert project_from_cwd("") is None


def test_normalized_event_is_frozen_and_carries_provenance():
    e = NormalizedEvent(
        kind=EventKind.AUTHORED_USER,
        role="user",
        text="hi",
        timestamp="t",
        session_id="s",
        project="agentcairn",
        git_branch="main",
        source_path=Path("/x.jsonl"),
    )
    assert e.kind is EventKind.AUTHORED_USER
    assert e.project == "agentcairn"
    import dataclasses

    assert dataclasses.is_dataclass(e)


def test_real_noise_shapes_are_not_authored(tmp_path):
    """Lock the audited noise classes by their REAL structural shape — none may
    classify as AUTHORED_USER, and none may survive parse->select as a candidate."""
    import json

    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code, parse_transcript
    from cairn.ingest.pipeline import select_candidates

    noise = [
        {
            "type": "user",
            "origin": {"kind": "task-notification"},
            "sessionId": "s",
            "message": {"role": "user", "content": "<task-notification> bg task done"},
        },
        {
            "type": "user",
            "isMeta": True,
            "sessionId": "s",
            "message": {
                "role": "user",
                "content": "Base directory for this skill: /Users/ccf/.claude/skills/x",
            },
        },
        {
            "type": "user",
            "toolUseResult": {},
            "sessionId": "s",
            "message": {"role": "user", "content": "\x1b[1mContext Usage\x1b[22m 49.8k/1m tokens"},
        },
        {
            "type": "user",
            "isCompactSummary": True,
            "isVisibleInTranscriptOnly": True,
            "sessionId": "s",
            "message": {
                "role": "user",
                "content": "This session is being continued from a previous conversation.",
            },
        },
    ]
    for obj in noise:
        assert classify_claude_code(obj) != EventKind.AUTHORED_USER

    authored = {
        "type": "user",
        "sessionId": "s",
        "cwd": "/Users/x/proj",
        "message": {"role": "user", "content": "we decided to always rebase-merge the branch"},
    }
    t = tmp_path / "mixed.jsonl"
    t.write_text("\n".join(json.dumps(o) for o in [*noise, authored]) + "\n")
    tr = parse_transcript(t)
    cands = select_candidates(tr)
    assert [c.text for c in cands] == ["we decided to always rebase-merge the branch"]
    # ANSI was stripped from the (excluded) tool-result during parse, too
    assert all("\x1b" not in e.text for e in tr.events)


def test_unknown_entry_shape_fails_closed(tmp_path):
    import json

    from cairn.ingest.locate import parse_transcript
    from cairn.ingest.pipeline import select_candidates

    # an unrecognized type, and a user row with a known injection flag -> excluded
    t = tmp_path / "weird.jsonl"
    t.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "future-thing",
                        "sessionId": "s",
                        "message": {"role": "x", "content": "hi"},
                    }
                ),
                json.dumps(
                    {
                        "type": "user",
                        "isMeta": True,
                        "sessionId": "s",
                        "message": {"role": "user", "content": "injected content here"},
                    }
                ),
            ]
        )
        + "\n"
    )
    assert select_candidates(parse_transcript(t)) == []


def test_legacy_unflagged_tag_rows_are_not_authored():
    """Claude Code <=2.1.150 injected slash-command/tool rows WITHOUT isMeta/
    origin/toolUseResult — structurally identical to authored prose. The
    tag-prefix backstop must catch them (found in the 2026-06-12 rebuild: 19
    such notes leaked through the structural filter)."""
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    legacy = [
        "<command-message>loop</command-message><command-args>watch the PR</command-args>",
        "<local-command-stdout>Context Usage ...</local-command-stdout>",
        "<bash-stdout></bash-stdout><bash-stderr>var.signoz_otlp_endpoint</bash-stderr>",
        "<task-notification>\n<task-id>x</task-id>",
        "<system-reminder>note</system-reminder>",
    ]
    for content in legacy:
        obj = {"type": "user", "message": {"role": "user", "content": content}}  # NO flags
        kind = classify_claude_code(obj)
        assert kind != EventKind.AUTHORED_USER, f"legacy row leaked as authored: {content[:40]}"


def test_authored_prose_starting_with_angle_bracket_survives():
    """A real user message that merely STARTS with '<' must stay authored —
    the backstop matches known harness tags only, not any '<'."""
    from cairn.ingest.events import EventKind
    from cairn.ingest.locate import classify_claude_code

    obj = {
        "type": "user",
        "message": {"role": "user", "content": "<div> renders before <span>, why?"},
    }
    assert classify_claude_code(obj) == EventKind.AUTHORED_USER
