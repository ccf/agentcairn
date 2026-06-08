# SPDX-License-Identifier: Apache-2.0
from cairn.vault.parse import parse_inline_fields


def test_line_level_field():
    assert parse_inline_fields("rating:: 9") == {"rating": "9"}


def test_bracket_and_paren_fields():
    fields = parse_inline_fields("Great coffee [rating:: 9] and (origin:: Ethiopia).")
    assert fields == {"rating": "9", "origin": "Ethiopia"}


def test_no_fields_returns_empty():
    assert parse_inline_fields("plain prose with no fields") == {}
