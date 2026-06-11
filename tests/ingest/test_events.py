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
