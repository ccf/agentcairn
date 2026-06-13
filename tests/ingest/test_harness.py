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
