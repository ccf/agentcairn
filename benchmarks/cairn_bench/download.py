# SPDX-License-Identifier: Apache-2.0
"""Fetch the pinned real datasets into ~/.cache/agentcairn/bench, revision/commit-pinned
via manifest.toml; SHA256 recorded and verified on/after first fetch. LoCoMo is CC BY-NC 4.0
and is NEVER vendored — only cached locally.

Uses `tomllib` (stdlib 3.11) to read manifest.toml. `huggingface_hub` is imported
lazily inside `fetch` so the base install (without the bench dependency group) can
import this module without error — only the hf download branch needs it.

`verify_sha` treats an empty `expected` string as "record on first fetch" and returns
without raising. This allows the manifest to ship with blank sha256 fields that are
filled in by the user after their first verified download.
"""

from __future__ import annotations

import hashlib
import tomllib
import urllib.request
from pathlib import Path

CACHE = Path.home() / ".cache" / "agentcairn" / "bench"
MANIFEST = Path(__file__).parent.parent / "manifest.toml"


def sha256_of(path: Path) -> str:
    """Return the hex SHA-256 digest of the file at `path`."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_sha(path: Path, expected: str) -> None:
    """Verify the SHA-256 of `path` against `expected`.

    Treats an empty `expected` as "record on first fetch" — returns without raising,
    allowing manifest entries with blank sha256 fields to pass silently. A non-empty
    `expected` that does not match raises ValueError.
    """
    if not expected:
        return  # empty in manifest = "record on first verified fetch"
    actual = sha256_of(path)
    if actual != expected:
        raise ValueError(f"SHA256 mismatch for {path}: got {actual}, expected {expected}")


def _manifest() -> dict:
    return tomllib.loads(MANIFEST.read_text())


def _dest_path(dataset: str, entry: dict) -> Path:
    """Return a cache path that encodes the manifest pin so a pin change → re-fetch.

    The pin is the ``revision`` (for HF entries) or the ``url`` (for URL entries).
    A SHA-256 prefix of the pin is embedded in the filename so that updating
    manifest.toml automatically routes to a new, uncached file.
    Old stale files under the cache are orphaned (not cleaned up).
    """
    pin = entry.get("revision") or entry.get("url", "")
    tag = hashlib.sha256(pin.encode()).hexdigest()[:8]
    return CACHE / f"{dataset}_{tag}.json"


def fetch(dataset: str) -> Path:
    """Download (if absent) and SHA-verify a dataset; return the cached path.

    Args:
        dataset: Key from manifest.toml (e.g. "longmemeval_s", "locomo").

    Returns:
        Path to the locally cached file.

    The cache path is pin-aware (via ``_dest_path``): changing ``revision`` or ``url``
    in manifest.toml routes to a new cache file, preventing stale-data reuse.

    The `huggingface_hub` import for "hf" entries is lazy — only activated when needed,
    so the base install (without the bench dependency group) can import this module.
    """
    entry = _manifest()[dataset]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = _dest_path(dataset, entry)
    if entry["kind"] == "url":
        if not dest.exists():
            urllib.request.urlretrieve(entry["url"], dest)  # noqa: S310 (pinned https)
    elif entry["kind"] == "hf":
        from huggingface_hub import hf_hub_download  # lazy — bench dep group only

        if not dest.exists():
            src = hf_hub_download(
                repo_id=entry["repo_id"],
                filename=entry["filename"],
                revision=entry["revision"],
                repo_type="dataset",
            )
            dest.write_bytes(Path(src).read_bytes())
    else:
        raise ValueError(f"unknown manifest kind: {entry['kind']}")
    verify_sha(dest, entry.get("sha256", ""))
    return dest
