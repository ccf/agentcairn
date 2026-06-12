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

# URL / connection-string credential pattern.
# Matches scheme://[user]:password@host but NOT SSH remotes (git@…, no ://)
# or plain host:port URLs (no @ sign).
# Groups: (1) scheme://user:  (2) password  (3) @
# Password class allows '/' (AWS secret keys and many passwords contain it) and
# anchors on the FIRST '@', so a slash in the password can't defeat the match and
# leak the credential. Over-redacting an exotic 'host:port/p@th' URL is acceptable
# (safe direction); leaking a password is not.
_URL_CRED_RE = re.compile(r"([a-z][a-z0-9+.-]*://[^/\s:@]*:)([^@\s]+)(@)", re.IGNORECASE)

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
    # key=value / key: value assignments for sensitive names (value may be quoted).
    # Use non-letter lookaround instead of \b so compound identifiers like
    # signing_secret= and DATABASE_PASSWORD= are caught, while English words
    # like "secretary" (keyword followed by a letter) are not.
    # The optional (?:[_-][A-Za-z0-9]+)* suffix absorbs trailing segments
    # such as _KEY or _ACCESS_KEY before the assignment operator.
    (
        "secret_assignment",
        re.compile(
            r"(?i)(?<![A-Za-z])"
            r"(?:aws_secret_access_key|secret_access_key|api[_-]?key|secret|token|password|passwd|pwd)"
            r"(?:[_-][A-Za-z0-9]+)*"
            r'(?![A-Za-z0-9])\s*[:=]\s*(?:"[^"]{6,}"|\'[^\']{6,}\'|[^\s\'"]{6,})'
        ),
    ),
]

# Bare AWS-style secret value: exactly 40 chars of base64-ish material (may
# contain '/' and '+'), standing alone (not inside a longer run). The narrowed
# _TOKEN_RE cannot span '/', so this shape gets a dedicated, guarded pass.
_AWS_SECRET_VALUE_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{40}(?![A-Za-z0-9+/=])")


def _mixed_charset(token: str) -> bool:
    return (
        bool(re.search(r"[A-Z]", token))
        and bool(re.search(r"[a-z]", token))
        and bool(re.search(r"[0-9]", token))
    )


# Entropy heuristic bounds: only long, structureless tokens are candidates.
_ENTROPY_MIN_LEN = 24
_ENTROPY_BITS = 3.5
# No '/', '-', or '_' in the class: paths, URLs, branches, hyphenated slugs, and
# snake_case/dunder identifiers (mcp__plugin_*, wrap_app_handling_exceptions) must
# never form an entropy candidate — the 2026-06-11 audit + corpus replay showed
# such structured identifiers were the overwhelming false-positive source.
# Separator-bearing bare secrets are covered by the dedicated aws_secret_value
# pass; known vendor shapes (github_pat_*, sk-proj-*, …) by named patterns.
_TOKEN_RE = re.compile(rf"[A-Za-z0-9+]{{{_ENTROPY_MIN_LEN},}}")
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
        return False  # 7-40 char hex (git SHAs, short digests) — not secrets
    has_upper = bool(re.search(r"[A-Z]", token))
    has_lower = bool(re.search(r"[a-z]", token))
    has_digit = bool(re.search(r"[0-9]", token))
    if has_upper and has_lower and has_digit:
        return _shannon_entropy(token) >= _ENTROPY_BITS
    # No mixed case: raise the bar so long hyphenated slugs (entropy ~3.77)
    # survive while 64-hex signing secrets (entropy ~3.94) are caught.
    return _shannon_entropy(token) >= 3.8


def redact(text: str) -> RedactionResult:
    """Return a RedactionResult whose .text is safe to hash and write.

    URL credential pass runs first (before entropy) so short passwords in
    connection strings are caught even if they fall below the entropy threshold.
    The bare AWS secret-value pass runs second, before entropy, so the entropy
    pass cannot partially consume a separator-bearing 40-char secret.
    Entropy heuristic runs third for long high-entropy standalone tokens.
    Named patterns run last for precise well-known credential shapes."""
    kinds: list[str] = []
    out = text

    # Pass 0: URL / connection-string credentials — run BEFORE entropy so that
    # short passwords like "pass1234" in scheme://user:pass@host are caught.
    def _url_sub(m: re.Match[str]) -> str:
        kinds.append("url_credential")
        return f"{m.group(1)}[REDACTED:url_credential]{m.group(3)}"

    out = _URL_CRED_RE.sub(_url_sub, out)

    # Pass 1: bare AWS-style secret values — exactly-40-char base64 runs with a
    # mixed-charset guard (hex SHAs and prose-ish strings are single-case or
    # letter-only and never fire). Must run BEFORE the entropy pass: the
    # narrowed _TOKEN_RE would otherwise consume the segment after a '/' and
    # leak the prefix (e.g. 'wJalr/' before '[REDACTED:high_entropy]').
    def _aws_sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if _mixed_charset(tok):
            kinds.append("aws_secret_value")
            return "[REDACTED:aws_secret_value]"
        return tok

    out = _AWS_SECRET_VALUE_RE.sub(_aws_sub, out)

    # Pass 2: entropy heuristic — catches long high-entropy tokens before named
    # patterns consume them.
    def _entropy_sub(m: re.Match[str]) -> str:
        tok = m.group(0)
        if _looks_secret(tok):
            kinds.append("high_entropy")
            return "[REDACTED:high_entropy]"
        return tok

    out = _TOKEN_RE.sub(_entropy_sub, out)

    # Pass 3: named-pattern regexes — precise matches for known credential shapes.
    for kind, pat in _PATTERNS:

        def _sub(m: re.Match[str], _kind: str = kind) -> str:
            kinds.append(_kind)
            return f"[REDACTED:{_kind}]"

        out = pat.sub(_sub, out)

    return RedactionResult(text=out, count=len(kinds), kinds=kinds)
