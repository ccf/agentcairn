# SPDX-License-Identifier: Apache-2.0
"""Parse markdown text into Note objects per the locked contract."""

from __future__ import annotations

from cairn.vault.models import Observation, Relation
from cairn.vault.patterns import (
    CONTEXT_RE,
    INLINE_FIELD_BRACKET_RE,
    INLINE_FIELD_LINE_RE,
    INLINE_FIELD_PAREN_RE,
    OBSERVATION_RE,
    RELATION_RE,
    TAG_RE,
)


def parse_observation_line(line: str) -> Observation | None:
    m = OBSERVATION_RE.match(line)
    if not m:
        return None
    category = m.group(1).strip()
    remainder = m.group(2).strip()

    context: str | None = None
    ctx = CONTEXT_RE.search(remainder)
    if ctx:
        context = ctx.group(1).strip()
        remainder = remainder[: ctx.start()].strip()

    tags = TAG_RE.findall(remainder)
    content = TAG_RE.sub("", remainder).strip()

    return Observation(category=category, content=content, tags=tags, context=context)


def parse_relation_line(line: str) -> Relation | None:
    m = RELATION_RE.match(line)
    if not m:
        return None
    quoted, bare, target = m.group(1), m.group(2), m.group(3)
    rel_type = (quoted or bare or "links_to").strip()
    return Relation(rel_type=rel_type, target=target.strip())


def parse_inline_fields(text: str) -> dict[str, str]:
    """Extract Dataview-style inline fields from a single line of text."""
    fields: dict[str, str] = {}
    line_m = INLINE_FIELD_LINE_RE.match(text.strip())
    if line_m:
        fields[line_m.group(1)] = line_m.group(2).strip()
    for rx in (INLINE_FIELD_BRACKET_RE, INLINE_FIELD_PAREN_RE):
        for key, value in rx.findall(text):
            fields[key] = value.strip()
    return fields
