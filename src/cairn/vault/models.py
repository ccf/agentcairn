# SPDX-License-Identifier: Apache-2.0
"""Parsed representations of a memory note. These are the public types the
rest of agentcairn consumes; keep their signatures stable."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Observation:
    category: str
    content: str
    tags: list[str] = field(default_factory=list)
    context: str | None = None


@dataclass
class Relation:
    rel_type: str
    target: str  # the [[Target]] name; may not yet exist (forward reference)


@dataclass
class Note:
    permalink: str | None = None
    frontmatter: dict[str, Any] = field(default_factory=dict)
    body: str = ""
    observations: list[Observation] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    wikilinks: list[str] = field(default_factory=list)  # all [[targets]] found in body
    inline_fields: dict[str, str] = field(default_factory=dict)
