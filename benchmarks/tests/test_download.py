# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import hashlib

import pytest
from cairn_bench.download import _dest_path, sha256_of, verify_sha


def test_dest_path_pin_change():
    """Different pins must produce different dest paths; same pin → same path."""
    url_entry_a = {"kind": "url", "url": "u/AAA"}
    url_entry_b = {"kind": "url", "url": "u/BBB"}
    hf_entry_v1 = {"kind": "hf", "revision": "abc123", "repo_id": "org/ds", "filename": "data.json"}
    hf_entry_v2 = {"kind": "hf", "revision": "def456", "repo_id": "org/ds", "filename": "data.json"}

    # different url pin → different path (no network)
    assert _dest_path("locomo", url_entry_a) != _dest_path("locomo", url_entry_b)
    # same entry → same path (deterministic)
    assert _dest_path("locomo", url_entry_a) == _dest_path("locomo", url_entry_a)
    # different HF revision → different path (no network)
    assert _dest_path("myds", hf_entry_v1) != _dest_path("myds", hf_entry_v2)


def test_sha_roundtrip(tmp_path):
    f = tmp_path / "x.json"
    f.write_text("[]")
    digest = sha256_of(f)
    assert digest == hashlib.sha256(b"[]").hexdigest()
    verify_sha(f, digest)  # exact match: no raise
    verify_sha(f, "")  # empty expected = "record on first fetch": no raise
    with pytest.raises(ValueError):
        verify_sha(f, "deadbeef")  # mismatch raises
