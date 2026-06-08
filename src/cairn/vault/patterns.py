# SPDX-License-Identifier: Apache-2.0
"""Locked parsing patterns for the markdown memory contract. Single source of
truth — import these; never re-inline a regex elsewhere."""

import re

OBSERVATION_RE = re.compile(r"^\s*[-*]\s+\[(?!\[)([^\]]+)\]\s*(.*)$")
RELATION_RE = re.compile(
    r'^\s*[-*]\s+(?:"([^"]+)"\s+|([A-Za-z][\w -]*?)\s+)?\[\[([^\]|]+)(?:\|[^\]]+)?\]\]\s*$'
)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")
CONTEXT_RE = re.compile(r"\(([^)]*)\)\s*$")
TAG_RE = re.compile(r"(?:^|\s)#([\w\-/]+)")
INLINE_FIELD_BRACKET_RE = re.compile(r"\[([\w-]+)::\s*([^\]]+)\]")
INLINE_FIELD_PAREN_RE = re.compile(r"\(([\w-]+)::\s*([^)]+)\)")
INLINE_FIELD_LINE_RE = re.compile(r"^([\w-]+)::\s+(.+)$")
INLINE_CODE_RE = re.compile(r"`[^`]*`")
