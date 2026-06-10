# SPDX-License-Identifier: Apache-2.0
from datetime import UTC, date, datetime

import pytest

from cairn.temporal import parse_temporal, validity_status


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
