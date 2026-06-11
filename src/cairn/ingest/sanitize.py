# src/cairn/ingest/sanitize.py
# SPDX-License-Identifier: Apache-2.0
"""Text hygiene for ingested transcript content.

`sanitize_text` strips terminal escape sequences (ANSI SGR colors, cursor moves,
OSC) and stray C0 control bytes. Slash-command output and tool dumps captured in
transcripts carry these, and they were leaking verbatim into notes.
"""

from __future__ import annotations

import re

# ANSI / VT escape sequences:
#   CSI  ESC [ ... <final @-~>      (SGR colors, cursor moves, …)
#   OSC  ESC ] ... <BEL | ESC \>    (window title, hyperlinks, …)
#   2-byte ESC <@-_>                (other simple escapes)
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;:?]*[ -/]*[@-~]"  # CSI
    r"|\x1b\].*?(?:\x07|\x1b\\)"  # OSC ... BEL or ST
    r"|\x1b[@-Z\\-_]",  # two-char escapes
    re.DOTALL,
)

# C0 control bytes except tab (09), newline (0A), carriage return (0D); plus DEL (7F).
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(text: str) -> str:
    """Strip ANSI/OSC escape sequences and stray control bytes, keeping \n and \t.
    Idempotent, and a no-op for normal prose (no escapes -> returned unchanged)."""
    if not text:
        return text
    out = _ANSI_RE.sub("", text)
    out = _CTRL_RE.sub("", out)
    return out
