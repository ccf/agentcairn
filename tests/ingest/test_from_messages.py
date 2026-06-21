# tests/ingest/test_from_messages.py
# SPDX-License-Identifier: Apache-2.0

from cairn.ingest import transcript_from_messages
from cairn.ingest.events import EventKind


def test_maps_roles_to_event_kinds():
    msgs = [
        {"role": "user", "content": "I deploy with make ship, never npm publish."},
        {"role": "assistant", "content": "Got it."},
        {"role": "system", "content": "ignore me"},
    ]
    t = transcript_from_messages(msgs, session_id="s1", cwd="/tmp/proj")
    assert t.session_id == "s1"
    kinds = [(e.kind, e.role) for e in t.events]
    assert (EventKind.AUTHORED_USER, "user") in kinds
    assert (EventKind.AUTHORED_ASSISTANT, "assistant") in kinds
    user_ev = next(e for e in t.events if e.kind == EventKind.AUTHORED_USER)
    assert "make ship" in user_ev.text
    assert user_ev.harness == "hermes"


def test_handles_content_list_and_skips_empty():
    msgs = [
        {
            "role": "user",
            "content": [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}],
        },
        {"role": "user", "content": ""},
    ]
    t = transcript_from_messages(msgs, session_id="s2")
    texts = [e.text for e in t.events if e.kind == EventKind.AUTHORED_USER]
    assert texts == ["hello world"]
