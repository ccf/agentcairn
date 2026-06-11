# tests/ingest/test_sanitize.py
# SPDX-License-Identifier: Apache-2.0

from cairn.ingest.sanitize import sanitize_text


def test_strips_ansi_sgr_and_keeps_text():
    raw = "\x1b[1mContext Usage\x1b[22m\x1b[38;2;136;136;136m colored \x1b[0m"
    assert sanitize_text(raw) == "Context Usage colored "


def test_strips_osc_sequences():
    raw = "before\x1b]8;;https://example.com\x07link\x1b]8;;\x07after"
    assert sanitize_text(raw) == "beforelinkafter"


def test_strips_control_bytes_but_keeps_tab_newline():
    raw = "line1\nline2\tcol\x00\x07\x1f\x7fend\r\n"
    out = sanitize_text(raw)
    assert "\x00" not in out and "\x07" not in out and "\x1f" not in out and "\x7f" not in out
    assert "line1\nline2\tcol" in out
    assert out.endswith("end\r\n")  # tab, newline, CR preserved


def test_sanitize_is_noop_for_plain_prose():
    prose = "We decided to always rebase-merge; see PR #44.\nSecond line."
    assert sanitize_text(prose) == prose


def test_sanitize_is_idempotent():
    raw = "\x1b[1mhi\x1b[0m\x07"
    once = sanitize_text(raw)
    assert sanitize_text(once) == once == "hi"
