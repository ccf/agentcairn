# src/cairn/ingest/sanitize.py
# SPDX-License-Identifier: Apache-2.0
"""Text hygiene for ingested transcript content.

Two concerns, both about keeping non-prose junk out of the Markdown vault:

1. `sanitize_text` strips terminal escape sequences (ANSI SGR colors, cursor
   moves, OSC) and stray C0 control bytes. Slash-command output and tool dumps
   captured in transcripts carry these, and they were leaking verbatim into notes.
2. `is_framing_noise` recognizes harness-injected user-role turns — slash-command
   output/markers and compaction summaries — that are not real user prose and
   should never become memories.
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


# User-role turns whose text starts with one of these are harness framing, not prose:
# slash-command output/markers and tool-result dumps Claude Code injects as "user".
_FRAMING_PREFIXES = (
    "<local-command-stdout",
    "<local-command-stderr",
    "<bash-stdout",
    "<bash-stderr",
    "<command-name",
    "<command-message",
    "<command-args",
    "<system-reminder",
)
# Compaction summaries the harness injects when a conversation is continued.
_CONTINUED_PREFIX = "this session is being continued from a previous conversation"


def is_framing_noise(text: str) -> bool:
    """True if a user-role turn is harness-injected framing (slash-command output,
    tool dumps, command markers, or a compaction summary) rather than real prose."""
    s = text.lstrip()
    if s.startswith(_FRAMING_PREFIXES):
        return True
    return s[: len(_CONTINUED_PREFIX) + 8].lower().startswith(_CONTINUED_PREFIX)
