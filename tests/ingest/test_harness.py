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
