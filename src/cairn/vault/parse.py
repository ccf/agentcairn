# SPDX-License-Identifier: Apache-2.0
"""Parse markdown text into Note objects per the locked contract."""

from __future__ import annotations

from cairn.vault.models import Observation
from cairn.vault.patterns import CONTEXT_RE, OBSERVATION_RE, TAG_RE


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
