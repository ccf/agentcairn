# tests/ingest/test_dedup.py
# SPDX-License-Identifier: Apache-2.0
import stat

from cairn.ingest.dedup import DedupLedger, content_hash


def test_content_hash_is_stable_and_sensitive():
    assert content_hash("hello") == content_hash("hello")
    assert content_hash("hello") != content_hash("hello!")
    assert len(content_hash("hello")) == 64  # sha256 hex


def test_ledger_seen_after_add(tmp_path):
    led = DedupLedger(tmp_path / "ingested.sha256")
    h = content_hash("a memory")
    assert led.seen(h) is False
    led.add(h)
    assert led.seen(h) is True


def test_ledger_persists_across_instances(tmp_path):
    path = tmp_path / "ingested.sha256"
    h = content_hash("durable")
    DedupLedger(path).add(h)
    assert DedupLedger(path).seen(h) is True


def test_ledger_add_is_idempotent(tmp_path):
    path = tmp_path / "ingested.sha256"
    led = DedupLedger(path)
    h = content_hash("x")
    led.add(h)
    led.add(h)
    assert path.read_text().count(h) == 1


def test_ledger_uses_private_cache_defaults(tmp_path):
    path = tmp_path / "cache" / "ingested.sha256"
    DedupLedger(path).add(content_hash("private"))
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
