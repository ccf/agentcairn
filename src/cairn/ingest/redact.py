# src/cairn/ingest/redact.py
# SPDX-License-Identifier: Apache-2.0
"""Secret/credential redaction. MANDATORY before any hash or write — we persist
plaintext, so a leak here is the system's worst failure mode (see spec §11, §14).

Two layers: named-pattern regexes (precise) + a Shannon-entropy heuristic for long
high-entropy tokens the patterns miss. Tuned for zero leakage of the golden corpus
with low false positives (git SHAs and prose must survive)."""

from __future__ import annotations

import math
import re

from cairn.ingest.models import RedactionResult

# (kind, compiled pattern). Order matters: multi-line/private-key first.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL
        ),
    ),
    ("anthropic_key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai_key", re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{20,}")),
    ("github_fine_grained", re.compile(r"github_pat_[A-Za-z0-9_]{30,}")),
    ("github_token", re.compile(r"gh[posru]_[A-Za-z0-9]{30,}")),
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._-]{12,}")),
    # key=value / key: value assignments for sensitive names (value may be quoted)
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:aws_secret_access_key|secret_access_key|api[_-]?key|secret|token|password|passwd|pwd)\b"
            r"\s*[:=]\s*['\"]?([^\s'\"]{6,})['\"]?"
        ),
    ),
]

# Entropy heuristic bounds: only long, structureless tokens are candidates.
_ENTROPY_MIN_LEN = 24
_ENTROPY_BITS = 3.5
_TOKEN_RE = re.compile(rf"[A-Za-z0-9+/_-]{{{_ENTROPY_MIN_LEN},}}")
# git SHAs are pure hex (7–40 chars) — allow them to survive
_HEX_RE = re.compile(r"(?i)^[0-9a-f]{7,40}$")


def _shannon_entropy(s: str) -> float:
    if not s:
        return 0.0
    freq: dict[str, int] = {}
    for ch in s:
        freq[ch] = freq.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in freq.values())


def _looks_secret(token: str) -> bool:
    if _HEX_RE.match(token):
        return False  # hex digests / git SHAs are not secrets — don't over-redact
    if (
        not re.search(r"[A-Z]", token)
        or not re.search(r"[a-z]", token)
        or not re.search(r"[0-9]", token)
    ):
        return False  # require mixed case + digits to look like a credential
    return _shannon_entropy(token) >= _ENTROPY_BITS


def redact(text: str) -> RedactionResult:
    """Return a RedactionResult whose .text is safe to hash and write.

    Entropy heuristic runs first so standalone high-entropy tokens (not matched
    by any named pattern) are always tagged as 'high_entropy'. Named patterns run
    second and catch any remaining well-known credential shapes."""
    kinds: list[str] = []
    out = text

    # Pass 1: entropy heuristic — catches long high-entropy tokens before named
    # patterns consume them.
    def _entropy_sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if _looks_secret(tok):
            kinds.append("high_entropy")
            return "[REDACTED:high_entropy]"
        return tok

    out = _TOKEN_RE.sub(_entropy_sub, out)

    # Pass 2: named-pattern regexes — precise matches for known credential shapes.
    for kind, pat in _PATTERNS:

        def _sub(m: re.Match[str], _kind: str = kind) -> str:
            kinds.append(_kind)
            return f"[REDACTED:{_kind}]"

        out = pat.sub(_sub, out)

    return RedactionResult(text=out, count=len(kinds), kinds=kinds)
