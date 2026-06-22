# tests/ingest/test_opencode.py
# SPDX-License-Identifier: Apache-2.0

import pytest

from cairn.ingest.events import EventKind
from cairn.ingest.harness import ParseCtx


@pytest.fixture()
def storage(tmp_path):
    """Build a minimal OpenCode storage tree under tmp_path."""
    msg_dir = tmp_path / "storage" / "message" / "sess1"
    msg_dir.mkdir(parents=True)
    part_dir = tmp_path / "storage" / "part"
    part_dir.mkdir(parents=True)

    # User message
    (msg_dir / "msg1.json").write_text(
        '{"role":"user","time":{"created":1234567890}}', encoding="utf-8"
    )
    # User text part
    user_part = part_dir / "msg1"
    user_part.mkdir()
    (user_part / "p1.json").write_text(
        '{"type":"text","text":"we deploy with make ship, never npm publish"}',
        encoding="utf-8",
    )

    # Assistant message
    (msg_dir / "msg2.json").write_text(
        '{"role":"assistant","time":{"created":1234567891}}', encoding="utf-8"
    )
    # Assistant text part
    asst_part = part_dir / "msg2"
    asst_part.mkdir()
    (asst_part / "p1.json").write_text('{"type":"text","text":"Got it."}', encoding="utf-8")

    # Malformed file — must be silently skipped
    (msg_dir / "junk.json").write_text("not valid json{{{", encoding="utf-8")

    return tmp_path / "storage"


def test_is_present_with_env(monkeypatch, storage):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(storage.parent))
    assert OpenCodeAdapter().is_present() is True


def test_is_present_without_env(monkeypatch, tmp_path):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    # Point away from any real opencode data dir
    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "nonexistent"))
    assert OpenCodeAdapter().is_present() is False


def test_find_returns_session_dir(monkeypatch, storage):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(storage.parent))
    sessions = OpenCodeAdapter().find(root=None, project=None)
    assert len(sessions) == 1
    assert sessions[0].name == "sess1"


def test_iter_raw_and_classify(monkeypatch, storage):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(storage.parent))
    a = OpenCodeAdapter()
    sess_dir = storage / "message" / "sess1"

    rows = list(a.iter_raw(sess_dir))
    # malformed file is skipped, so only 2 rows
    assert len(rows) == 2

    roles = {r["role"] for r in rows}
    assert roles == {"user", "assistant"}

    user_row = next(r for r in rows if r["role"] == "user")
    asst_row = next(r for r in rows if r["role"] == "assistant")

    assert a.classify(user_row) == EventKind.AUTHORED_USER
    assert a.classify(asst_row) == EventKind.AUTHORED_ASSISTANT


def test_to_event_user(monkeypatch, storage):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(storage.parent))
    a = OpenCodeAdapter()
    sess_dir = storage / "message" / "sess1"
    ctx = ParseCtx(path=sess_dir)

    rows = list(a.iter_raw(sess_dir))
    user_row = next(r for r in rows if r["role"] == "user")

    ev = a.to_event(user_row, EventKind.AUTHORED_USER, ctx)
    assert ev is not None
    assert "make ship" in ev.text
    assert ev.harness == "opencode"
    assert ev.role == "user"
    assert ev.kind == EventKind.AUTHORED_USER


def test_unknown_role_is_not_a_candidate():
    # A non-user/assistant role, or a missing role key, must fail closed to UNKNOWN.
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    a = OpenCodeAdapter()
    assert a.classify({"role": "system", "_text": "shutting down"}) == EventKind.UNKNOWN
    assert a.classify({"_text": "no role key at all"}) == EventKind.UNKNOWN


def test_user_with_no_text_is_not_authored_user(tmp_path):
    # A user message whose parts join to empty text (here: only a non-text part)
    # must not become AUTHORED_USER, and to_event must return None.
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    storage = tmp_path / "storage"
    msg_dir = storage / "message" / "sess1"
    msg_dir.mkdir(parents=True)
    (msg_dir / "msg1.json").write_text(
        '{"role":"user","time":{"created":1234567890}}', encoding="utf-8"
    )
    # Only a non-"text" part → joined _text is empty.
    part_dir = storage / "part" / "msg1"
    part_dir.mkdir(parents=True)
    (part_dir / "p1.json").write_text('{"type":"tool","text":"x"}', encoding="utf-8")

    a = OpenCodeAdapter()
    sess_dir = storage / "message" / "sess1"
    (row,) = list(a.iter_raw(sess_dir))  # _text populated by the real adapter flow
    assert row["_text"] == ""
    assert a.classify(row) == EventKind.UNKNOWN
    ctx = ParseCtx(path=sess_dir)
    assert a.to_event(row, EventKind.AUTHORED_USER, ctx) is None


def test_to_event_missing_time_is_safe(tmp_path):
    # A user row lacking a `time` field must yield timestamp None, not raise.
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    a = OpenCodeAdapter()
    ctx = ParseCtx(path=tmp_path / "message" / "sess1")
    row = {"role": "user", "_text": "ship it", "_session_id": "sess1"}
    ev = a.to_event(row, EventKind.AUTHORED_USER, ctx)
    assert ev is not None
    assert ev.timestamp is None
    assert ev.text == "ship it"


def test_comma_separated_data_dir(monkeypatch, tmp_path):
    # Both comma-separated roots must be searched: only path_b has a session.
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    path_a = tmp_path / "a"
    path_a.mkdir()
    path_b = tmp_path / "b"
    sess_dir = path_b / "storage" / "message" / "sess1"
    sess_dir.mkdir(parents=True)

    monkeypatch.setenv("OPENCODE_DATA_DIR", f"{path_a},{path_b}")
    a = OpenCodeAdapter()
    assert a.is_present() is True
    found = a.find(root=None, project=None)
    assert [p.name for p in found] == ["sess1"]
    assert found[0] == sess_dir
