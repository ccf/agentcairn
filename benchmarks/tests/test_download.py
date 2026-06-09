# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib

import pytest
from cairn_bench.download import sha256_of, verify_sha


def test_sha_roundtrip(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("[]")
    digest = sha256_of(f)
    assert digest == hashlib.sha256(b"[]").hexdigest()
    verify_sha(f, digest)  # exact match: no raise
    verify_sha(f, "")  # empty expected = "record on first fetch": no raise
    with pytest.raises(ValueError):
        verify_sha(f, "deadbeef")  # mismatch raises
