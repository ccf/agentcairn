# tests/ingest/test_redact.py
# SPDX-License-Identifier: Apache-2.0
import pytest

from cairn.ingest.redact import redact

# Golden corpus of FAKE-but-realistically-shaped secrets. NONE may survive redaction.
GOLDEN_SECRETS = [
    ("aws_access_key", "AKIAIOSFODNN7EXAMPLE"),
    ("aws_secret", "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("github_pat", "ghp_16C7e42F292c6912E7710c838347Ae178B4a"),
    ("github_fine_grained", "github_pat_11ABCDE0Q0abcdefHIJklm_nOpQrStUvWxYz0123456789ABCDEFghij"),
    ("openai", "sk-proj-abcdEFGH1234ijklMNOP5678qrstUVWX90abQRSTuvwx12"),
    ("anthropic", "sk-ant-api03-aBcd1234EfGh5678IjKl90MnOpQrStUvWxYz-aB12cd34Ef56_gh78"),
    ("google_api", "AIzaSyA1234567890abcdefghijklmnopqrstuv"),
    ("slack", "xoxb-123456789012-1234567890123-AbCdEfGhIjKlMnOpQrStUvWx"),
    ("bearer", "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abc"),
    ("password_assign", 'password = "hunter2-not-a-real-pw-zzz"'),
    (
        "private_key",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    ),
]

# Strings that must SURVIVE (no over-redaction): ordinary text + a git SHA + a permalink.
SAFE_STRINGS = [
    "Let's refactor the parser to handle forward references.",
    "The commit is f3d17de96b66ad5f56a3f29cf8bcb57b7aed83fe on feat/v1-search.",
    "permalink: coffee-brewing-method",
    "Run uv run pytest -q to check the suite.",
]


@pytest.mark.parametrize("name,secret", GOLDEN_SECRETS, ids=[s[0] for s in GOLDEN_SECRETS])
def test_every_golden_secret_is_redacted(name, secret):
    text = f"here is the value: {secret} -- keep it safe"
    result = redact(text)
    assert result.count >= 1, f"{name} produced no redaction"
    # the literal secret payload must not appear anywhere in the output
    payload = secret.split("=", 1)[-1].split(":", 1)[-1].strip().strip('"')
    assert payload not in result.text, f"{name} payload leaked"
    assert "[REDACTED" in result.text


@pytest.mark.parametrize("safe", SAFE_STRINGS)
def test_safe_strings_are_not_redacted(safe):
    result = redact(safe)
    assert result.text == safe
    assert result.count == 0


def test_multiple_secrets_counted():
    text = "k1=AKIAIOSFODNN7EXAMPLE and k2=ghp_16C7e42F292c6912E7710c838347Ae178B4a"
    result = redact(text)
    assert result.count >= 2
    assert "AKIAIOSFODNN7EXAMPLE" not in result.text
    assert "ghp_16C7e42F292c6912E7710c838347Ae178B4a" not in result.text


def test_high_entropy_token_redacted():
    # a long random-looking token not matching any named pattern
    text = "token: Zk9Q2mVx7Lp4Rt6Yw1Nf3Hd8Bc5Jg0Ks2Pv4Ua7Wb9Xe1Tc3"
    result = redact(text)
    assert result.count >= 1
    assert "high_entropy" in result.kinds


def test_url_password_with_slash_redacted():
    # Passwords can contain '/' (e.g. AWS secret keys); the slash must not defeat
    # the URL-credential regex and leak the password to disk.
    for url in [
        "postgres://user:wJalrXUtnFEMI/K7MDENG/bPxRf@db.internal:5432/prod",
        "redis://:cache/pass/77@cache:6379",
    ]:
        r = redact(url)
        assert r.count >= 1
        for frag in ["wJalrXUtnFEMI", "K7MDENG", "bPxRf", "cache/pass"]:
            assert frag not in r.text, f"slash-password fragment leaked: {frag}"
    # false-positive survivors: SSH remote (no '://') and a host:port URL (no '@')
    assert redact("git@github.com:org/repo").count == 0
    assert redact("http://host:8080/path").count == 0


# ---------------------------------------------------------------------------
# Gap 1 — long hex secrets bypass _looks_secret (only 7–40 char hex are SHAs)
# ---------------------------------------------------------------------------

_HEX64_LOWER = "3d7e1a9c4b2f8e0d5a6c1b9e3f2d4a7b8c0e1f5a2d3c4b5a6e7f8c9d0b1e2a3f"
_HEX64_UPPER = _HEX64_LOWER.upper()


def test_long_lowercase_hex_secret_is_redacted():
    """64-char lowercase hex (signing secret) must be caught by entropy heuristic."""
    text = f"signing_secret: {_HEX64_LOWER}"
    result = redact(text)
    assert result.count >= 1, "64-char lowercase hex secret was not redacted"
    assert _HEX64_LOWER not in result.text, "64-char lowercase hex secret leaked"


def test_long_uppercase_hex_secret_is_redacted():
    """64-char UPPERCASE hex must also be redacted."""
    text = f"signing_secret: {_HEX64_UPPER}"
    result = redact(text)
    assert result.count >= 1, "64-char UPPERCASE hex secret was not redacted"
    assert _HEX64_UPPER not in result.text, "64-char UPPERCASE hex secret leaked"


def test_long_hyphenated_slug_survives():
    """A long human-readable hyphenated slug must NOT be over-redacted."""
    slug = "feature-request-for-the-new-cli-interface-v2"
    result = redact(slug)
    assert result.text == slug, f"Slug was wrongly redacted: {result.text!r}"
    assert result.count == 0


def test_40_char_git_sha_survives():
    """A 40-char git SHA must survive unchanged (known-safe allowlist)."""
    sha = "f3d17de96b66ad5f56a3f29cf8bcb57b7aed83fe"
    text = f"commit {sha} on main"
    result = redact(text)
    assert sha in result.text, "40-char git SHA was wrongly redacted"
    assert result.count == 0


# ---------------------------------------------------------------------------
# Gap 2 — compound key-name assignments bypass secret_assignment pattern
# ---------------------------------------------------------------------------


def test_signing_secret_compound_key_redacted():
    """signing_secret=... must be caught (keyword is a suffix of a compound name)."""
    text = "signing_secret=hunter2xyz"
    result = redact(text)
    assert result.count >= 1, "signing_secret= was not redacted"
    assert "hunter2xyz" not in result.text, "signing_secret value leaked"


def test_stripe_secret_key_screaming_snake_redacted():
    """STRIPE_SECRET_KEY=... (SCREAMING_SNAKE) must be redacted."""
    text = "STRIPE_SECRET_KEY=sk_live_abc123def456"
    result = redact(text)
    assert result.count >= 1, "STRIPE_SECRET_KEY= was not redacted"
    assert "sk_live_abc123def456" not in result.text, "STRIPE_SECRET_KEY value leaked"


def test_database_password_compound_key_redacted():
    """DATABASE_PASSWORD=... must be redacted."""
    text = "DATABASE_PASSWORD=mypass99x"
    result = redact(text)
    assert result.count >= 1, "DATABASE_PASSWORD= was not redacted"
    assert "mypass99x" not in result.text, "DATABASE_PASSWORD value leaked"


def test_secretary_prose_survives():
    """'secretary' contains 'secret' but must not trigger false-positive redaction."""
    text = "my secretary scheduled the meeting"
    result = redact(text)
    assert result.text == text, f"'secretary' prose was wrongly redacted: {result.text!r}"
    assert result.count == 0


def test_existing_password_assign_still_redacted():
    """Regression: the original password = '...' golden case must still redact."""
    text = 'password = "hunter2-not-a-real-pw-zzz"'
    result = redact(text)
    assert result.count >= 1
    assert "hunter2-not-a-real-pw-zzz" not in result.text


def test_existing_aws_secret_access_key_still_redacted():
    """Regression: aws_secret_access_key=... golden case must still redact."""
    text = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    result = redact(text)
    assert result.count >= 1
    assert "wJalrXUtnFEMI" not in result.text


# ---------------------------------------------------------------------------
# C1 — URL / connection-string credential redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url,password",
    [
        (
            "DATABASE_URL=postgres://app_user:Sup3rS3cret_DB_p4ss@db.internal:5432/prod",
            "Sup3rS3cret_DB_p4ss",
        ),
        ("postgres://user:pass1234@host", "pass1234"),
        ("mysql://root:rootpass99@host", "rootpass99"),
        ("redis://:cachepass77@cache", "cachepass77"),
        ("https://admin:hunter2xyz@host", "hunter2xyz"),
        ("amqp://guest:guestpw12@rabbit", "guestpw12"),
    ],
    ids=[
        "database_url_postgres",
        "postgres_plain",
        "mysql_plain",
        "redis_empty_user",
        "https_basic_auth",
        "amqp_guest",
    ],
)
def test_url_credentials_redacted(url, password):
    """Passwords in scheme://user:password@host URLs must be redacted."""
    result = redact(url)
    assert result.count >= 1, f"No redaction for URL: {url!r}"
    assert password not in result.text, f"Password {password!r} leaked in: {result.text!r}"
    assert "[REDACTED" in result.text


@pytest.mark.parametrize(
    "url",
    [
        "git@github.com:org/repo.git",
        "http://host:8080/path",
    ],
    ids=["ssh_remote_no_scheme", "host_port_no_at"],
)
def test_url_false_positive_survivors(url):
    """SSH remotes and host:port URLs must NOT be redacted."""
    result = redact(url)
    assert result.text == url, f"False-positive redaction for: {url!r} -> {result.text!r}"
    assert result.count == 0


# ---------------------------------------------------------------------------
# I1 — quoted multi-word secret values
# ---------------------------------------------------------------------------


def test_quoted_multiword_secret_redacted():
    """The entire quoted passphrase must be gone, not just the first word."""
    text = 'password = "correct horse battery staple"'
    result = redact(text)
    assert result.count >= 1, "No redaction for quoted multi-word secret"
    for word in ("correct", "horse", "battery", "staple"):
        assert word not in result.text, f"Tail word {word!r} leaked in: {result.text!r}"


def test_quoted_multiword_secret_single_quotes():
    """Single-quoted passphrase must also be fully redacted."""
    text = "api_key = 'multi word secret value here'"
    result = redact(text)
    assert result.count >= 1
    assert "multi" not in result.text
    assert "secret" not in result.text or "[REDACTED" in result.text


def test_existing_password_assign_still_redacted_after_i1():
    """Regression: password = '...' golden case still works after I1 fix."""
    text = 'password = "hunter2-not-a-real-pw-zzz"'
    result = redact(text)
    assert result.count >= 1
    assert "hunter2-not-a-real-pw-zzz" not in result.text


def test_existing_aws_secret_still_redacted_after_i1():
    """Regression: aws_secret_access_key=... still works after I1 fix."""
    text = "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
    result = redact(text)
    assert result.count >= 1
    assert "wJalrXUtnFEMI" not in result.text


# ---------------------------------------------------------------------------
# Over-firing fix — structured identifiers must SURVIVE the entropy net
# (real damage observed in the 2026-06-11 vault audit)
# ---------------------------------------------------------------------------

OVERFIRE_SURVIVORS = [
    (
        "plugin_cache_path",
        "/Users/ccf/.claude/plugins/cache/claude-plugins-official/superpowers/5.1.0/skills/brainstorming",
    ),
    ("github_url", "https://github.com/ccf/agentcairn/blob/main/CHANGELOG.md"),
    ("git_branch", "the branch feat/v1.1-bitemporal-validity-and-recall has the fix"),
    ("skill_slug", "use superpowers:subagent-driven-development for this"),
    ("plan_filename", "see docs/plans/2026-06-10-agentcairn-claude-code-plugin.md for details"),
    ("permalink_slug", "permalink: all-of-the-above-angles-are-31b5c3dc"),
    # underscore identifiers (corpus replay found these as the residual FP class)
    ("mcp_tool_name", "call mcp__plugin_playwright_playwright__browser_take_screenshot next"),
    ("snake_case_fn", "the fix is in wrap_app_handling_exceptions in starlette"),
]


@pytest.mark.parametrize("name,text", OVERFIRE_SURVIVORS, ids=[s[0] for s in OVERFIRE_SURVIVORS])
def test_structured_identifiers_survive_unredacted(name, text):
    result = redact(text)
    assert result.text == text, f"{name} was wrongly redacted: {result.text!r}"
    assert result.count == 0


# ---------------------------------------------------------------------------
# Bare AWS secret value — exactly-40-char base64 (may contain / and +), no
# key-name prefix. The narrowed entropy class can't span '/', so this shape
# needs its own pattern. Guarded: must contain upper+lower+digit.
# ---------------------------------------------------------------------------

_BARE_AWS = "wJalr/UtnFEMIK7MDENGbPxRfiCY+EXAMPLEKEYz"  # 40 chars, has / + upper lower digit


def test_bare_aws_secret_value_redacted():
    text = f"the old secret was {_BARE_AWS} rotate it"
    result = redact(text)
    assert result.count >= 1, "bare 40-char AWS secret value was not redacted"
    assert _BARE_AWS not in result.text
    assert "aws_secret_value" in result.kinds


def test_contiguous_base64_still_caught_by_entropy():
    # 32+ contiguous mixed-case alnum (no separators) — entropy net territory
    tok = "Zk9Q2mVx7Lp4Rt6Yw1Nf3Hd8Bc5Jg0Ks2Pv4Ua7"
    result = redact(f"value {tok} end")
    assert result.count >= 1
    assert tok not in result.text


def test_aws_guard_rejects_lowercase_only():
    # 40 chars, lowercase-only -> fails the upper+lower+digit guard. Deliberately
    # repetitive (low Shannon entropy) so the entropy net can't fire either —
    # this isolates the aws_secret_value guard.
    s = "abcdabcdabcdabcdabcdabcdabcdabcdabcdabcd"
    assert len(s) == 40
    result = redact(s)
    assert result.text == s
    assert result.count == 0


def test_aws_guard_rejects_40char_hex_sha():
    sha = "f3d17de96b66ad5f56a3f29cf8bcb57b7aed83fe"  # git SHA-1: single-case hex
    result = redact(f"commit {sha} on main")
    assert sha in result.text
    assert result.count == 0
