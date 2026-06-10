# SPDX-License-Identifier: Apache-2.0
"""Valid-time helpers for bi-temporal validity. Normalize any YAML temporal value
to a tz-aware UTC datetime, and compute a note's validity status vs `now`.
Half-open interval [valid_from, valid_until): closed start, strict-less end."""

from __future__ import annotations

from datetime import UTC, date, datetime


def to_db(dt: datetime | None) -> datetime | None:
    """Aware datetime -> naive-UTC for binding into a DuckDB TIMESTAMP (DuckDB
    converts aware values to local time; binding naive-UTC stores the instant verbatim)."""
    return None if dt is None else dt.astimezone(UTC).replace(tzinfo=None)


def from_db(dt: datetime | None) -> datetime | None:
    """Naive-UTC read back from a DuckDB TIMESTAMP -> aware UTC."""
    return None if dt is None else dt.replace(tzinfo=UTC)


def db_now() -> datetime:
    """Current time as naive-UTC, for binding into TIMESTAMP comparisons."""
    return datetime.now(UTC).replace(tzinfo=None)


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


_VALIDITY_PENALTY: float = 0.5


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


def validity_factor(
    valid_from: datetime | None,
    valid_until: datetime | None,
    superseded_by: str | None,
    now: datetime,
) -> float:
    """Return ``_VALIDITY_PENALTY`` when the note is not "current" as of ``now``,
    else 1.0.  Used by the rerank path to preserve the validity demote that the
    SQL RRF path applied (the cross-encoder score discards it)."""
    if validity_status(valid_from, valid_until, superseded_by, now) == "current":
        return 1.0
    return _VALIDITY_PENALTY
