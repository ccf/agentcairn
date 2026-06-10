# SPDX-License-Identifier: Apache-2.0
"""Valid-time helpers for bi-temporal validity. Normalize any YAML temporal value
to a tz-aware UTC datetime, and compute a note's validity status vs `now`.
Half-open interval [valid_from, valid_until): closed start, strict-less end."""

from __future__ import annotations

from datetime import UTC, date, datetime


def parse_temporal(value: object) -> datetime | None:
    """Normalize a frontmatter temporal value to a tz-aware UTC datetime.
    None/"" -> None. naive -> assumed UTC. date-only -> 00:00 UTC. str -> ISO-8601.
    Raises TypeError/ValueError on an unparseable value (caller treats as absent)."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):  # NOTE: datetime is a date subclass — check datetime first
        dt = datetime(value.year, value.month, value.day)
    elif isinstance(value, str):
        dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        raise TypeError(f"unparseable temporal value: {value!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def validity_status(
    valid_from: datetime | None,
    valid_until: datetime | None,
    superseded_by: str | None,
    now: datetime,
) -> str:
    """current | superseded | expired | not_yet_valid (as of `now`)."""
    if superseded_by:
        return "superseded"
    if valid_until is not None and not (now < valid_until):  # half-open: end is exclusive
        return "expired"
    if valid_from is not None and valid_from > now:
        return "not_yet_valid"
    return "current"
