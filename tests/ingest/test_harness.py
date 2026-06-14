# tests/ingest/test_harness.py
# SPDX-License-Identifier: Apache-2.0

import pytest

from cairn.ingest import harness as harness_pkg
from cairn.ingest.events import EventKind
from cairn.ingest.harness import (
    ParseCtx,
    TranscriptRef,
    get_adapter,
    present_harnesses,
)


class _FakeAdapter:
    def __init__(self, name, root, files=()):
        self.name = name
        self._root = root
        self._files = list(files)

    def default_root(self):
        return self._root

    def is_present(self):
        return self._root.is_dir()

    def find(self, *, root, project):
        return list(self._files)

    def iter_raw(self, path):
        return iter(())

    def classify(self, raw):
        return EventKind.UNKNOWN

    def to_event(self, raw, kind, ctx):
        return None


def test_get_adapter_unknown_raises():
    with pytest.raises(ValueError):
        get_adapter("definitely-not-a-harness")


def test_get_adapter_returns_registered(monkeypatch, tmp_path):
    fake = _FakeAdapter("fake", tmp_path)
    monkeypatch.setitem(harness_pkg.REGISTRY, "fake", fake)
    assert get_adapter("fake") is fake


def test_present_harnesses_filters_by_root(monkeypatch, tmp_path):
    present = _FakeAdapter("present", tmp_path)  # tmp_path exists
    absent = _FakeAdapter("absent", tmp_path / "nope")  # missing dir
    monkeypatch.setitem(harness_pkg.REGISTRY, "present", present)
    monkeypatch.setitem(harness_pkg.REGISTRY, "absent", absent)
    names = [a.name for a in present_harnesses(["present", "absent"])]
    assert names == ["present"]


def test_present_harnesses_unknown_name_raises(monkeypatch):
    with pytest.raises(ValueError):
        present_harnesses(["definitely-not-a-harness"])


def test_parsectx_and_ref_shapes(tmp_path):
    ref = TranscriptRef(path=tmp_path / "a.jsonl", harness="fake")
    assert ref.path.name == "a.jsonl" and ref.harness == "fake"
    ctx = ParseCtx(path=tmp_path / "a.jsonl")
    assert ctx.session_id is None and ctx.cwd is None and ctx.git_branch is None


def test_claude_code_adapter_classify_and_event(tmp_path):
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter()
    assert a.name == "claude-code"
    raw = {
        "type": "user",
        "sessionId": "sess-1",
        "cwd": "/Users/x/proj",
        "gitBranch": "main",
        "timestamp": "2026-06-08T10:00:00Z",
        "message": {"role": "user", "content": "fix the bug"},
    }
    kind = a.classify(raw)
    assert kind == EventKind.AUTHORED_USER
    ctx = ParseCtx(path=tmp_path / "s.jsonl")
    ev = a.to_event(raw, kind, ctx)
    assert ev.text == "fix the bug"
    assert ev.kind == EventKind.AUTHORED_USER
    assert ev.harness == "claude-code"
    assert ev.project == "proj"
    assert ctx.session_id == "sess-1" and ctx.cwd == "/Users/x/proj"


def test_claude_code_adapter_skips_textless_row(tmp_path):
    from cairn.ingest.harness.claude_code import ClaudeCodeAdapter

    a = ClaudeCodeAdapter()
    raw = {"type": "user", "sessionId": "skipme", "message": {"role": "user", "content": ""}}
    ctx = ParseCtx(path=tmp_path / "s.jsonl")
    assert a.to_event(raw, a.classify(raw), ctx) is None
    assert ctx.session_id is None  # a skipped row must not set provenance


def test_claude_code_adapter_registered():
    assert get_adapter("claude-code").name == "claude-code"


def test_find_transcripts_auto_detect_unions(monkeypatch, tmp_path):
    from cairn.ingest import harness as hp
    from cairn.ingest.locate import find_transcripts

    a_dir = tmp_path / "a"
    a_dir.mkdir()
    fa = a_dir / "1.jsonl"
    fa.write_text("{}\n")
    b_dir = tmp_path / "b"
    b_dir.mkdir()
    fb = b_dir / "2.jsonl"
    fb.write_text("{}\n")

    monkeypatch.setitem(hp.REGISTRY, "ha", _FakeAdapter("ha", a_dir, files=[fa]))
    monkeypatch.setitem(hp.REGISTRY, "hb", _FakeAdapter("hb", b_dir, files=[fb]))

    refs = find_transcripts(harness=None, harnesses=["ha", "hb"])
    assert {r.path.name for r in refs} == {"1.jsonl", "2.jsonl"}
    assert {r.harness for r in refs} == {"ha", "hb"}


def _codex_line(type_, payload):
    import json

    return json.dumps({"type": type_, "payload": payload, "timestamp": "2026-03-08T13:35:29Z"})


def _msg(role, text, block_type):
    return {"type": "message", "role": role, "content": [{"type": block_type, "text": text}]}


def test_codex_adapter_classifies_each_kind():
    from cairn.ingest.harness.codex import CodexAdapter

    a = CodexAdapter()
    assert a.name == "codex"

    def C(p):
        return a.classify({"type": "response_item", "payload": p})

    assert C({"type": "function_call"}) == EventKind.TOOL_RESULT
    assert C({"type": "function_call_output"}) == EventKind.TOOL_RESULT
    assert C({"type": "custom_tool_call"}) == EventKind.TOOL_RESULT
    assert C({"type": "web_search_call"}) == EventKind.TOOL_RESULT
    assert C({"type": "reasoning"}) == EventKind.AUTHORED_ASSISTANT
    assert C(_msg("assistant", "done", "output_text")) == EventKind.AUTHORED_ASSISTANT
    assert (
        C(_msg("developer", "<permissions instructions>x", "input_text"))
        == EventKind.META_INJECTION
    )
    assert C(_msg("user", "Review the code base please", "input_text")) == EventKind.AUTHORED_USER
    # injected AGENTS.md / INSTRUCTIONS blocks arrive as role=user -> tag-backstop demotes them
    assert (
        C(_msg("user", "# AGENTS.md instructions for /repo", "input_text"))
        == EventKind.META_INJECTION
    )
    assert C(_msg("user", "<INSTRUCTIONS>\n# Primer", "input_text")) == EventKind.META_INJECTION
    assert a.classify({"type": "compacted", "payload": {}}) == EventKind.COMPACT_SUMMARY
    assert a.classify({"type": "session_meta", "payload": {}}) == EventKind.SYSTEM
    assert a.classify({"type": "turn_context", "payload": {}}) == EventKind.SYSTEM
    assert a.classify({"type": "event_msg", "payload": {}}) == EventKind.SYSTEM
    assert a.classify({"type": "weird_future_type", "payload": {}}) == EventKind.UNKNOWN


def test_codex_adapter_parses_session_and_user_turn(tmp_path):
    from cairn.ingest.locate import parse_transcript

    day = tmp_path / "2026" / "03" / "08"
    day.mkdir(parents=True)
    f = day / "rollout-2026-03-08T09-35-29-abc.jsonl"
    f.write_text(
        "\n".join(
            [
                _codex_line("session_meta", {"id": "sess-codex", "cwd": "/Users/x/insights"}),
                _codex_line("turn_context", {"cwd": "/Users/x/insights"}),
                _codex_line("event_msg", {"foo": "bar"}),  # UI noise -> no event
                _codex_line(
                    "response_item", _msg("user", "# AGENTS.md instructions", "input_text")
                ),
                _codex_line(
                    "response_item", _msg("developer", "<permissions instructions>", "input_text")
                ),
                _codex_line(
                    "response_item", _msg("user", "Review the roadmap and advise", "input_text")
                ),
                _codex_line("response_item", _msg("assistant", "Here is my take.", "output_text")),
                _codex_line("response_item", {"type": "function_call", "name": "shell"}),
            ]
        )
        + "\n"
    )
    tr = parse_transcript(TranscriptRef(path=f, harness="codex"))
    assert tr.session_id == "sess-codex"
    assert tr.cwd == "/Users/x/insights"
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == ["Review the roadmap and advise"]
    assert all(e.harness == "codex" for e in tr.events)
    assert authored[0].project == "insights"


def test_codex_adapter_find_rglobs_and_filters_project(tmp_path):
    from cairn.ingest.harness.codex import CodexAdapter

    a = CodexAdapter()
    day = tmp_path / "2026" / "03" / "08"
    day.mkdir(parents=True)
    keep = day / "rollout-keep.jsonl"
    keep.write_text(_codex_line("session_meta", {"id": "s1", "cwd": "/Users/x/insights"}) + "\n")
    drop = day / "rollout-drop.jsonl"
    drop.write_text(_codex_line("session_meta", {"id": "s2", "cwd": "/Users/x/other"}) + "\n")
    assert {p.name for p in a.find(root=tmp_path, project=None)} == {
        "rollout-keep.jsonl",
        "rollout-drop.jsonl",
    }
    assert [p.name for p in a.find(root=tmp_path, project="/Users/x/insights")] == [
        "rollout-keep.jsonl"
    ]


def test_resolve_harnesses_precedence():
    from cairn.cli import _resolve_harnesses

    # explicit flag wins, comma-split + trimmed
    assert _resolve_harnesses("claude-code, codex", {"CAIRN_HARNESSES": "codex"}) == [
        "claude-code",
        "codex",
    ]
    # env used when no flag
    assert _resolve_harnesses(None, {"CAIRN_HARNESSES": "codex"}) == ["codex"]
    # nothing -> None (auto-detect all present)
    assert _resolve_harnesses(None, {}) is None
    # empty/whitespace flag -> treated as unset
    assert _resolve_harnesses("  ", {}) is None


def test_codex_adapter_non_string_cwd_does_not_crash(tmp_path):
    # Bugbot #71: a non-string cwd in session_meta/turn_context must degrade to
    # None (no project) rather than crash .rstrip()/Path() in find()/to_event.
    from cairn.ingest.harness.codex import CodexAdapter
    from cairn.ingest.locate import parse_transcript

    day = tmp_path / "2026" / "03" / "08"
    day.mkdir(parents=True)
    f = day / "rollout-weird.jsonl"
    f.write_text(
        "\n".join(
            [
                _codex_line("session_meta", {"id": "s1", "cwd": {"unexpected": "object"}}),
                _codex_line("response_item", _msg("user", "Ship the thing", "input_text")),
            ]
        )
        + "\n"
    )
    a = CodexAdapter()
    # find()'s project filter must not crash on a non-string cwd (treated as None)
    assert a.find(root=tmp_path, project="/Users/x/insights") == []
    # parse must not crash; the non-string cwd becomes None -> project None
    tr = parse_transcript(TranscriptRef(path=f, harness="codex"))
    assert tr.cwd is None
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == ["Ship the thing"]
    assert authored[0].project is None


def _agy_line(type_, source, content=None, created_at="2026-06-14T14:19:09Z"):
    import json

    o = {
        "step_index": 0,
        "source": source,
        "type": type_,
        "status": "DONE",
        "created_at": created_at,
    }
    if content is not None:
        o["content"] = content
    return json.dumps(o)


_AGY_USER = (
    "<USER_REQUEST>\n"
    "Remember: I always squash-merge agentcairn release branches.\n"
    "</USER_REQUEST>\n"
    "<ADDITIONAL_METADATA>\n"
    "The current local time is: 2026-06-14T10:19:09-04:00.\n"
    "</ADDITIONAL_METADATA>\n"
    "<USER_SETTINGS_CHANGE>\n"
    "The user changed setting `Model Selection` to Gemini 3.5 Flash.\n"
    "</USER_SETTINGS_CHANGE>"
)


def test_antigravity_user_request_extracts_only_request_block():
    from cairn.ingest.harness.antigravity import _user_request

    out = _user_request(_AGY_USER)
    assert out == "Remember: I always squash-merge agentcairn release branches."
    assert "ADDITIONAL_METADATA" not in out
    assert "Model Selection" not in out
    assert "local time" not in out
    assert _user_request("no request here") == ""
    assert _user_request(None) == ""


def test_antigravity_classify_each_kind():
    from cairn.ingest.harness.antigravity import AntigravityAdapter

    a = AntigravityAdapter()
    assert a.name == "antigravity"
    assert (
        a.classify({"type": "USER_INPUT", "source": "USER_EXPLICIT", "content": _AGY_USER})
        == EventKind.AUTHORED_USER
    )
    assert (
        a.classify(
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "<USER_REQUEST>\n/model\n</USER_REQUEST>",
            }
        )
        == EventKind.META_INJECTION
    )
    assert (
        a.classify({"type": "USER_INPUT", "source": "SYSTEM", "content": _AGY_USER})
        == EventKind.META_INJECTION
    )
    assert (
        a.classify(
            {
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "<USER_REQUEST></USER_REQUEST>",
            }
        )
        == EventKind.META_INJECTION
    )
    assert (
        a.classify({"type": "PLANNER_RESPONSE", "source": "MODEL", "content": "done"})
        == EventKind.AUTHORED_ASSISTANT
    )
    assert a.classify({"type": "CONVERSATION_HISTORY", "source": "SYSTEM"}) == EventKind.UNKNOWN
    assert a.classify({"type": "WEIRD_FUTURE", "source": "x"}) == EventKind.UNKNOWN


def test_antigravity_parses_transcript(tmp_path):
    from cairn.ingest.locate import parse_transcript

    logs = tmp_path / "brain" / "abc-uuid-123" / ".system_generated" / "logs"
    logs.mkdir(parents=True)
    f = logs / "transcript.jsonl"
    f.write_text(
        "\n".join(
            [
                _agy_line("USER_INPUT", "USER_EXPLICIT", _AGY_USER),
                _agy_line("CONVERSATION_HISTORY", "SYSTEM"),
                _agy_line("PLANNER_RESPONSE", "MODEL", "Acknowledged."),
                _agy_line("USER_INPUT", "USER_EXPLICIT", "<USER_REQUEST>\n/help\n</USER_REQUEST>"),
            ]
        )
        + "\n"
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    import json as _j

    (cache / "last_conversations.json").write_text(_j.dumps({"/Users/x/proj": "abc-uuid-123"}))

    tr = parse_transcript(TranscriptRef(path=f, harness="antigravity"))
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == [
        "Remember: I always squash-merge agentcairn release branches."
    ]
    assert all(e.harness == "antigravity" for e in tr.events)
    assert authored[0].session_id == "abc-uuid-123"
    assert authored[0].project == "proj"
    assert all("ADDITIONAL_METADATA" not in e.text for e in tr.events)


def test_antigravity_find_globs_and_project_filter(tmp_path):
    import json as _j

    from cairn.ingest.harness.antigravity import AntigravityAdapter

    a = AntigravityAdapter()
    for uuid in ("keep-uuid", "drop-uuid"):
        d = tmp_path / "brain" / uuid / ".system_generated" / "logs"
        d.mkdir(parents=True)
        (d / "transcript.jsonl").write_text(
            _agy_line("USER_INPUT", "USER_EXPLICIT", _AGY_USER) + "\n"
        )
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "last_conversations.json").write_text(
        _j.dumps({"/Users/x/keep": "keep-uuid", "/Users/x/other": "drop-uuid"})
    )
    root = tmp_path / "brain"
    assert {p.parent.parent.parent.name for p in a.find(root=root, project=None)} == {
        "keep-uuid",
        "drop-uuid",
    }
    kept = a.find(root=root, project="/Users/x/keep")
    assert [p.parent.parent.parent.name for p in kept] == ["keep-uuid"]


def test_antigravity_registered():
    assert get_adapter("antigravity").name == "antigravity"


def test_antigravity_shallow_path_does_not_crash(tmp_path):
    # parse_transcript accepts arbitrary Paths; a shallow path must degrade, not IndexError.
    from cairn.ingest.locate import parse_transcript

    f = tmp_path / "transcript.jsonl"
    f.write_text(_agy_line("USER_INPUT", "USER_EXPLICIT", _AGY_USER) + "\n")
    tr = parse_transcript(TranscriptRef(path=f, harness="antigravity"))
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert authored and authored[0].project is None  # no cwd resolvable, but no crash


def test_antigravity_find_missing_root_and_bad_cache(tmp_path):
    from cairn.ingest.harness.antigravity import AntigravityAdapter, _conversation_cwd

    a = AntigravityAdapter()
    assert a.find(root=tmp_path / "nope", project=None) == []  # missing root → []
    # malformed last_conversations.json → empty map, no crash
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "last_conversations.json").write_text("{ not json")
    assert _conversation_cwd(tmp_path / "brain") == {}


def _make_cursor_db(path, rows):
    """Build a fixture Cursor state.vscdb: a cursorDiskKV(key, value) table whose
    values are JSON bubbles. `rows` is a list of (key, dict)."""
    import json as _j
    import sqlite3

    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        [(k, _j.dumps(v)) for k, v in rows],
    )
    con.commit()
    con.close()


def test_cursor_classify():
    from cairn.ingest.harness.cursor import CursorAdapter

    a = CursorAdapter()
    assert a.name == "cursor"
    assert a.classify({"type": 1, "text": "combine the repos"}) == EventKind.AUTHORED_USER
    assert a.classify({"type": 1, "text": "/Users/x/f.py please review"}) == EventKind.AUTHORED_USER
    assert a.classify({"type": 1, "text": "   "}) == EventKind.UNKNOWN
    assert a.classify({"type": 1}) == EventKind.UNKNOWN
    assert a.classify({"type": 2, "text": "assistant reply"}) == EventKind.UNKNOWN


def test_cursor_parses_user_bubbles(tmp_path):
    from cairn.ingest.locate import parse_transcript

    db = tmp_path / "state.vscdb"
    _make_cursor_db(
        db,
        [
            (
                "bubbleId:comp-1:b1",
                {
                    "type": 1,
                    "text": "Combine the repos into one",
                    "workspaceProjectDir": "/Users/x/proj",
                    "createdAt": "2025-12-20T23:29:17.798Z",
                },
            ),
            ("bubbleId:comp-1:b2", {"type": 2, "text": "Here is a plan"}),
            (
                "bubbleId:comp-1:b3",
                {
                    "type": 1,
                    "text": "/Users/x/f.py please review",
                    "workspaceProjectDir": "/Users/x/proj",
                },
            ),
            (
                "bubbleId:comp-1:b4",
                {"type": 1, "text": "   ", "workspaceProjectDir": "/Users/x/proj"},
            ),
        ],
    )
    tr = parse_transcript(TranscriptRef(path=db, harness="cursor"))
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert [e.text for e in authored] == [
        "Combine the repos into one",
        "/Users/x/f.py please review",
    ]
    assert all(e.harness == "cursor" for e in authored)
    assert authored[0].session_id == "comp-1"
    assert authored[0].project == "proj"
    assert authored[0].timestamp == "2025-12-20T23:29:17.798Z"


def test_cursor_find_present_and_absent(tmp_path):
    from cairn.ingest.harness.cursor import CursorAdapter

    a = CursorAdapter()
    assert a.find(root=tmp_path, project=None) == []
    gs = tmp_path / "globalStorage"
    gs.mkdir()
    db = gs / "state.vscdb"
    _make_cursor_db(db, [("bubbleId:c:b", {"type": 1, "text": "hi"})])
    assert a.find(root=tmp_path, project=None) == [db]
    assert a.find(root=tmp_path, project="/Users/x/anything") == [db]


def test_cursor_iter_raw_missing_table_is_graceful(tmp_path):
    import sqlite3

    from cairn.ingest.harness.cursor import CursorAdapter

    db = tmp_path / "state.vscdb"
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE other (k TEXT)")
    con.commit()
    con.close()
    a = CursorAdapter()
    assert list(a.iter_raw(db)) == []


def test_cursor_registered():
    assert get_adapter("cursor").name == "cursor"


def test_cursor_iter_raw_skips_malformed_value(tmp_path):
    # A non-JSON value must not crash the whole DB's ingestion — good rows around it survive.
    import sqlite3

    from cairn.ingest.harness.cursor import CursorAdapter

    db = tmp_path / "state.vscdb"
    _make_cursor_db(
        db,
        [
            ("bubbleId:c:b1", {"type": 1, "text": "first real prompt"}),
            ("bubbleId:c:b3", {"type": 1, "text": "second real prompt"}),
        ],
    )
    con = sqlite3.connect(str(db))
    con.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        ("bubbleId:c:b2", "{ not json"),
    )
    con.commit()
    con.close()
    texts = [b.get("text") for b in CursorAdapter().iter_raw(db)]
    assert texts == ["first real prompt", "second real prompt"]  # malformed row silently skipped


def test_cursor_iter_raw_is_read_only(tmp_path):
    # iter_raw must never mutate the source DB (opened immutable=1): no -wal/-journal, same bytes.
    import hashlib

    from cairn.ingest.harness.cursor import CursorAdapter

    db = tmp_path / "state.vscdb"
    _make_cursor_db(db, [("bubbleId:c:b1", {"type": 1, "text": "hello"})])
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    list(CursorAdapter().iter_raw(db))  # full drain
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before  # unchanged
    assert not (tmp_path / "state.vscdb-wal").exists()
    assert not (tmp_path / "state.vscdb-journal").exists()


def test_cursor_non_string_createdat_coerced(tmp_path):
    # A numeric createdAt must not crash and must coerce to None (timestamp is str|None).
    from cairn.ingest.locate import parse_transcript

    db = tmp_path / "state.vscdb"
    _make_cursor_db(db, [("bubbleId:c:b1", {"type": 1, "text": "hi", "createdAt": 1734567890})])
    tr = parse_transcript(TranscriptRef(path=db, harness="cursor"))
    authored = [e for e in tr.events if e.kind == EventKind.AUTHORED_USER]
    assert authored and authored[0].timestamp is None
