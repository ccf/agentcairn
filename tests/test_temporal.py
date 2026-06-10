# SPDX-License-Identifier: Apache-2.0
from datetime import UTC, date, datetime

import pytest

from cairn.temporal import db_now, from_db, parse_temporal, to_db, validity_status


def test_parse_temporal_variants():
    assert parse_temporal(None) is None
    assert parse_temporal("") is None
    assert parse_temporal(date(2024, 1, 2)) == datetime(2024, 1, 2, tzinfo=UTC)  # date -> 00:00 UTC
    assert parse_temporal(datetime(2024, 1, 2, 8, 0)) == datetime(
        2024, 1, 2, 8, tzinfo=UTC
    )  # naive -> UTC
    assert parse_temporal("2024-01-02T08:00:00Z") == datetime(2024, 1, 2, 8, tzinfo=UTC)
    aware = datetime(2024, 1, 2, 8, 0, tzinfo=UTC)
    assert parse_temporal(aware) == aware


def test_parse_temporal_malformed_raises():
    with pytest.raises((TypeError, ValueError)):
        parse_temporal("not-a-date")
    with pytest.raises(TypeError):
        parse_temporal(123)


def test_validity_status_half_open_boundary():
    now = datetime(2024, 6, 1, tzinfo=UTC)
    # valid_until == now -> EXPIRED (strict end: now < valid_until is false)
    assert validity_status(None, now, None, now) == "expired"
    # valid_until just after now -> current
    assert validity_status(None, datetime(2024, 6, 1, 0, 0, 1, tzinfo=UTC), None, now) == "current"


def test_to_db_strips_tz_to_utc_wall_clock():
    """to_db(aware_utc) returns naive datetime with the same UTC wall-clock value."""
    aware = parse_temporal("2024-06-15T10:30:45Z")  # datetime(2024,6,15,10,30,45, tzinfo=UTC)
    naive = to_db(aware)
    assert naive is not None
    assert naive.tzinfo is None
    assert naive == datetime(2024, 6, 15, 10, 30, 45)


def test_to_db_non_utc_aware_converts_to_utc_wall_clock():
    """to_db(aware non-UTC) converts to UTC first, then drops tz."""
    import datetime as _dt

    tz_plus2 = _dt.timezone(_dt.timedelta(hours=2))
    aware = datetime(2024, 6, 15, 12, 30, 45, tzinfo=tz_plus2)  # 10:30:45 UTC
    naive = to_db(aware)
    assert naive is not None
    assert naive.tzinfo is None
    assert naive == datetime(2024, 6, 15, 10, 30, 45)


def test_to_db_none_returns_none():
    assert to_db(None) is None


def test_from_db_attaches_utc():
    """from_db(naive) returns an aware UTC datetime with the same wall-clock."""
    naive = datetime(2024, 6, 15, 10, 30, 45)
    aware = from_db(naive)
    assert aware is not None
    assert aware.tzinfo is UTC
    assert aware == datetime(2024, 6, 15, 10, 30, 45, tzinfo=UTC)


def test_from_db_none_returns_none():
    assert from_db(None) is None


def test_round_trip_to_db_from_db():
    """from_db(to_db(aware)) == aware — the round-trip is lossless."""
    aware = parse_temporal("2024-06-15T10:30:45Z")
    assert from_db(to_db(aware)) == aware


def test_db_now_is_naive():
    """db_now() returns a naive datetime (no tzinfo)."""
    n = db_now()
    assert isinstance(n, datetime)
    assert n.tzinfo is None


def test_validity_status_cases():
    now = datetime(2024, 6, 1, tzinfo=UTC)
    assert validity_status(None, None, None, now) == "current"  # no fields
    assert validity_status(None, None, "other-note", now) == "superseded"  # superseded wins
    assert validity_status(datetime(2024, 7, 1, tzinfo=UTC), None, None, now) == "not_yet_valid"
    assert validity_status(datetime(2024, 1, 1, tzinfo=UTC), None, None, now) == "current"
    assert (
        validity_status(
            datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC), None, now
        )
        == "expired"
    )
