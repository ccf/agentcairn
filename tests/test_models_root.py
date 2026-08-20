# SPDX-License-Identifier: Apache-2.0
"""The ONNX model cache must live somewhere persistent, chosen by us.

Regression guard: fastembed defaults its cache to `tempfile.gettempdir()`, which
macOS purges on a timer. When it does, the small config/tokenizer blobs go first
and the snapshot symlinks dangle, so every model load fails with
`Could not find config.json` — which crashes `cairn sweep` (capture stops
silently) and drops recall to BM25 through its fail-open fallback.

The load-bearing behaviour is that we ALWAYS pass an explicit `cache_dir`, so
fastembed never gets to choose the temp default.
"""

import sys
import types
from pathlib import Path

import pytest

from cairn import paths


def test_models_root_lives_under_the_cairn_cache():
    assert paths.models_root() == paths.cache_root() / "models"


def test_explicit_fastembed_cache_path_wins(monkeypatch, tmp_path):
    """A user who deliberately pointed fastembed elsewhere keeps that location."""
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", str(tmp_path / "mymodels"))
    assert paths.models_root() == tmp_path / "mymodels"


def test_explicit_path_is_user_expanded(monkeypatch):
    monkeypatch.setenv("FASTEMBED_CACHE_PATH", "~/somewhere/models")
    assert paths.models_root() == Path.home() / "somewhere" / "models"


def test_production_default_is_not_under_the_os_temp_dir():
    """The shipped default (unpatched by the test-isolation fixture) is $HOME-based.

    conftest repoints `cache_root` into pytest's tmp_path, so read the real
    implementation's target rather than the isolated one.
    """
    real_default = Path.home() / ".cache" / "agentcairn" / "models"
    import tempfile

    assert not real_default.is_relative_to(Path(tempfile.gettempdir()).resolve())


def _stub_fastembed(monkeypatch, captured: dict) -> None:
    """Install a fake `fastembed` so we can assert the kwargs we pass it."""

    class _FakeEmbedding:
        def __init__(self, model_name: str, cache_dir: str | None = None, **kw):
            captured["embed"] = {"model_name": model_name, "cache_dir": cache_dir}

        def embed(self, texts):
            return iter([__import__("numpy").zeros(3) for _ in texts])

    class _FakeCrossEncoder:
        def __init__(self, model_name: str, cache_dir: str | None = None, **kw):
            captured["rerank"] = {"model_name": model_name, "cache_dir": cache_dir}

    fastembed = types.ModuleType("fastembed")
    fastembed.TextEmbedding = _FakeEmbedding
    rerank_mod = types.ModuleType("fastembed.rerank")
    ce_mod = types.ModuleType("fastembed.rerank.cross_encoder")
    ce_mod.TextCrossEncoder = _FakeCrossEncoder
    monkeypatch.setitem(sys.modules, "fastembed", fastembed)
    monkeypatch.setitem(sys.modules, "fastembed.rerank", rerank_mod)
    monkeypatch.setitem(sys.modules, "fastembed.rerank.cross_encoder", ce_mod)


def test_embedder_passes_an_explicit_cache_dir(monkeypatch):
    pytest.importorskip("numpy")
    captured: dict = {}
    _stub_fastembed(monkeypatch, captured)
    from cairn.embed.fastembed_embedder import FastEmbedEmbedder

    FastEmbedEmbedder()
    assert captured["embed"]["cache_dir"] == str(paths.models_root()), (
        "embedder must pin cache_dir; inheriting fastembed's temp default is the bug"
    )


def test_reranker_passes_an_explicit_cache_dir(monkeypatch):
    captured: dict = {}
    _stub_fastembed(monkeypatch, captured)
    from cairn.search import rerank

    monkeypatch.setattr(rerank, "_RERANKER", None)  # defeat the module singleton
    rerank._get_reranker()
    assert captured["rerank"]["cache_dir"] == str(paths.models_root())


def test_model_cache_dir_is_created(monkeypatch):
    """The dir must exist before fastembed writes into it."""
    pytest.importorskip("numpy")
    captured: dict = {}
    _stub_fastembed(monkeypatch, captured)
    from cairn.embed.fastembed_embedder import FastEmbedEmbedder

    FastEmbedEmbedder()
    assert paths.models_root().is_dir()
