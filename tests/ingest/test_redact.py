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
