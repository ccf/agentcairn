# tests/ingest/test_opencode.py
# SPDX-License-Identifier: Apache-2.0

import json
import sqlite3
from pathlib import Path

import pytest

from cairn.ingest.events import EventKind
from cairn.ingest.harness import ParseCtx

# ---------------------------------------------------------------------------
# Helper: build a temp opencode.db with the real SQLite schema
# ---------------------------------------------------------------------------


def _make_db(base: Path) -> Path:
    """Create <base>/opencode.db with message/part/session tables and sample rows."""
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    db_path = base / "opencode.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE session (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            directory TEXT
        );
        CREATE TABLE message (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            time_created INTEGER,
            time_updated INTEGER,
            data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY,
            message_id TEXT,
            session_id TEXT,
            time_created INTEGER,
            data TEXT
        );
    """)

    # Session row
    con.execute(
        "INSERT INTO session VALUES (?,?,?)",
        ("sess1", "proj1", "/Users/alice/myrepo"),
    )

    # User message
    user_data = json.dumps({"role": "user", "time": {"created": 1_000_001}})
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        ("msg1", "sess1", 1_000_001, 1_000_001, user_data),
    )
    # User text part
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (
            "part1",
            "msg1",
            "sess1",
            1_000_001,
            json.dumps({"type": "text", "text": "we deploy with make ship, never npm publish"}),
        ),
    )

    # Assistant message
    asst_data = json.dumps({"role": "assistant", "time": {"created": 1_000_002}})
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        ("msg2", "sess1", 1_000_002, 1_000_002, asst_data),
    )
    # Assistant text part
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        (
            "part2",
            "msg2",
            "sess1",
            1_000_002,
            json.dumps({"type": "text", "text": "Got it."}),
        ),
    )

    # Malformed part row — must be silently skipped
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("part_bad", "msg2", "sess1", 1_000_003, "not valid json{{{"),
    )

    con.commit()
    con.close()
    return db_path


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db_base(tmp_path):
    """Return the base dir (contains opencode.db)."""
    _make_db(tmp_path)
    return tmp_path


# ---------------------------------------------------------------------------
# Test 1: is_present returns True when db exists
# ---------------------------------------------------------------------------


def test_is_present_true(monkeypatch, db_base):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(db_base))
    assert OpenCodeAdapter().is_present() is True


# ---------------------------------------------------------------------------
# Test 2: is_present returns False when db does not exist
# ---------------------------------------------------------------------------


def test_is_present_false(monkeypatch, tmp_path):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(tmp_path / "nonexistent"))
    assert OpenCodeAdapter().is_present() is False


# ---------------------------------------------------------------------------
# Test 3: find returns the db path
# ---------------------------------------------------------------------------


def test_find_returns_db_path(monkeypatch, db_base):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(db_base))
    paths = OpenCodeAdapter().find(root=None, project=None)
    assert len(paths) == 1
    assert paths[0].name == "opencode.db"
    assert paths[0].is_file()


# ---------------------------------------------------------------------------
# Test 4: iter_raw + classify → AUTHORED_USER for user message
# ---------------------------------------------------------------------------


def test_classify_user_message(monkeypatch, db_base):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(db_base))
    a = OpenCodeAdapter()
    db_path = db_base / "opencode.db"
    rows = list(a.iter_raw(db_path))

    user_rows = [r for r in rows if r.get("role") == "user"]
    assert user_rows, "expected at least one user row"
    assert a.classify(user_rows[0]) == EventKind.AUTHORED_USER


# ---------------------------------------------------------------------------
# Test 5: iter_raw + classify → AUTHORED_ASSISTANT for assistant message
# ---------------------------------------------------------------------------


def test_classify_assistant_message(monkeypatch, db_base):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(db_base))
    a = OpenCodeAdapter()
    db_path = db_base / "opencode.db"
    rows = list(a.iter_raw(db_path))

    asst_rows = [r for r in rows if r.get("role") == "assistant"]
    assert asst_rows, "expected at least one assistant row"
    assert a.classify(asst_rows[0]) == EventKind.AUTHORED_ASSISTANT


# ---------------------------------------------------------------------------
# Test 6: to_event text contains "make ship", harness="opencode", role="user",
#          project derived from session.directory
# ---------------------------------------------------------------------------


def test_to_event_user_text_and_project(monkeypatch, db_base):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    monkeypatch.setenv("OPENCODE_DATA_DIR", str(db_base))
    a = OpenCodeAdapter()
    db_path = db_base / "opencode.db"
    rows = list(a.iter_raw(db_path))

    user_row = next(r for r in rows if r.get("role") == "user")
    ctx = ParseCtx(path=db_path)
    ev = a.to_event(user_row, EventKind.AUTHORED_USER, ctx)

    assert ev is not None
    assert "make ship" in ev.text
    assert ev.harness == "opencode"
    assert ev.role == "user"
    assert ev.kind == EventKind.AUTHORED_USER
    # project derived from session.directory = "/Users/alice/myrepo" → "myrepo"
    assert ev.project == "myrepo"


# ---------------------------------------------------------------------------
# Test 7: Unknown/missing role → UNKNOWN
# ---------------------------------------------------------------------------


def test_unknown_role(monkeypatch):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    a = OpenCodeAdapter()
    assert a.classify({"role": "system", "_text": "shutdown"}) == EventKind.UNKNOWN
    assert a.classify({"_text": "no role key"}) == EventKind.UNKNOWN


# ---------------------------------------------------------------------------
# Test 8: Empty-text user message → not AUTHORED_USER + to_event returns None
# ---------------------------------------------------------------------------


def test_empty_text_user_not_candidate(tmp_path):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    db_path = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE session (id TEXT, project_id TEXT, directory TEXT);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER,
                              time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,
                           time_created INTEGER, data TEXT);
    """)
    con.execute("INSERT INTO session VALUES (?,?,?)", ("s1", "p1", "/tmp/proj"))
    user_data = json.dumps({"role": "user", "time": {"created": 1}})
    con.execute("INSERT INTO message VALUES (?,?,?,?,?)", ("m1", "s1", 1, 1, user_data))
    # Only a non-text part → _text will be empty
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p1", "m1", "s1", 1, json.dumps({"type": "tool", "text": "x"})),
    )
    con.commit()
    con.close()

    a = OpenCodeAdapter()
    rows = list(a.iter_raw(db_path))
    assert rows, "expected one row"
    row = rows[0]
    assert row["_text"] == ""
    assert a.classify(row) == EventKind.UNKNOWN
    ctx = ParseCtx(path=db_path)
    assert a.to_event(row, EventKind.AUTHORED_USER, ctx) is None


# ---------------------------------------------------------------------------
# Test 9: Missing time field → safe None timestamp (no crash)
# ---------------------------------------------------------------------------


def test_missing_time_safe(monkeypatch, tmp_path):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    a = OpenCodeAdapter()
    ctx = ParseCtx(path=tmp_path / "opencode.db")
    # Row without a "time" key at all
    row = {"role": "user", "_text": "ship it", "_session_id": "sess1", "_cwd": None}
    ev = a.to_event(row, EventKind.AUTHORED_USER, ctx)
    assert ev is not None
    assert ev.timestamp is None
    assert ev.text == "ship it"


# ---------------------------------------------------------------------------
# Test 10: Comma-separated OPENCODE_DATA_DIR searches both bases
# ---------------------------------------------------------------------------


def test_comma_separated_data_dir(monkeypatch, tmp_path):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    base_a = tmp_path / "a"
    base_a.mkdir()
    # base_a has no db

    base_b = tmp_path / "b"
    _make_db(base_b)

    monkeypatch.setenv("OPENCODE_DATA_DIR", f"{base_a},{base_b}")
    a = OpenCodeAdapter()
    assert a.is_present() is True
    paths = a.find(root=None, project=None)
    assert len(paths) == 1
    assert paths[0] == base_b / "opencode.db"


# ---------------------------------------------------------------------------
# Test 11: Malformed message data row is skipped (no crash)
# ---------------------------------------------------------------------------


def test_malformed_row_skipped(tmp_path):
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    db_path = tmp_path / "opencode.db"
    con = sqlite3.connect(str(db_path))
    con.executescript("""
        CREATE TABLE session (id TEXT, project_id TEXT, directory TEXT);
        CREATE TABLE message (id TEXT, session_id TEXT, time_created INTEGER,
                              time_updated INTEGER, data TEXT);
        CREATE TABLE part (id TEXT, message_id TEXT, session_id TEXT,
                           time_created INTEGER, data TEXT);
    """)
    con.execute("INSERT INTO session VALUES (?,?,?)", ("s1", "p1", "/tmp/proj"))
    # Malformed data JSON
    con.execute(
        "INSERT INTO message VALUES (?,?,?,?,?)",
        ("bad_msg", "s1", 1, 1, "not valid json{{{"),
    )
    # Valid message
    good_data = json.dumps({"role": "user", "time": {"created": 2}})
    con.execute("INSERT INTO message VALUES (?,?,?,?,?)", ("good_msg", "s1", 2, 2, good_data))
    con.execute(
        "INSERT INTO part VALUES (?,?,?,?,?)",
        ("p1", "good_msg", "s1", 2, json.dumps({"type": "text", "text": "hello"})),
    )
    con.commit()
    con.close()

    a = OpenCodeAdapter()
    rows = list(a.iter_raw(db_path))
    # malformed row skipped, only good_msg survives
    assert len(rows) == 1
    assert rows[0]["_text"] == "hello"


def test_iter_raw_nonexistent_db_yields_nothing(tmp_path):
    # A nonexistent / unreadable DB must yield no rows and never raise (fail-closed).
    from cairn.ingest.harness.opencode import OpenCodeAdapter

    a = OpenCodeAdapter()
    assert list(a.iter_raw(tmp_path / "nope" / "opencode.db")) == []
